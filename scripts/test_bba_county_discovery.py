import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import bba_county_discovery as b

COUNTY={'id':'FL-12109','state':'FL'}
class DiscoveryTests(unittest.TestCase):
    def test_clearing_register_discovers_station_without_seed(self):
        html='<table id="ContentPlaceHolder1_gdvApps"><tr><td>2025000003</td><td>Twin Creeks Fire Station</td><td>2/27/2025</td><td>2/12/2026</td></tr></table>'
        r=b.parse_register(html,'CGCP','https://webapp.sjcfl.us/CSubmitalWeb/',COUNTY)[0]
        self.assertEqual(r['record_number'],'2025000003')
        self.assertIsNone(r['coordinates']);self.assertFalse(r['publication_eligible'])
        self.assertEqual(r['horizon'],'unknown')
        self.assertIn('ApplType=CGCP',r['source_urls'][0])
    def test_missing_table_and_paging_fail_visibly(self):
        for html in ['<html>Access denied</html>', '<table id="gdvApps"></table><a href="javascript:Page$2">2</a>']:
            with self.assertRaises(ValueError):b.parse_register(html,'CGCP','https://example.org/',COUNTY)
    def test_different_permits_same_name_stay_distinct(self):
        a=b.record('a',COUNTY,'Station','',[]);c=b.record('b',COUNTY,'Station','',[])
        self.assertEqual(len(b.deduplicate([a,c,a])),2)
    def test_identity_conflict_rejected(self):
        with self.assertRaises(ValueError):b.deduplicate([b.record('a',COUNTY,'A','',[]),b.record('a',COUNTY,'B','',[])])
    def test_community_matches_do_not_change_record(self):
        r=b.record('a',COUNTY,'Twin Creeks Fire Station','',[],record_number='2025000003')
        lead={'title':'Twin Creeks Fire Station canceled?'}
        b.correlate([r],[lead]);self.assertEqual(lead['possible_record_matches'],['a'])
        self.assertEqual(r['status'],'Not verified');self.assertIsNone(r['coordinates'])
    def test_gis_missing_batch_record_rejected(self):
        cfg={'url':'https://example.org','oid':'OBJECTID','fields':['OBJECTID']}
        with patch.object(b,'request_json',side_effect=[{'objectIds':[1]}, {'features':[]}]):
            with self.assertRaises(ValueError):b.gis_records(None,COUNTY,cfg)
    def test_gis_truncated_ids_rejected(self):
        with patch.object(b,'request_json',return_value={'objectIds':[1],'exceededTransferLimit':True}):
            with self.assertRaises(ValueError):b.gis_records(None,COUNTY,{'url':'https://example.org'})
    def test_gis_approval_not_completion_and_no_contact_fields(self):
        config=json.loads((Path(__file__).parents[1]/'config/counties.json').read_text())
        county=config['counties'][1];cfg=county['gis'][0]
        a={cfg['oid']:1,cfg['identity']:123,cfg['name']:'A',cfg['status']:'Approved'}
        with patch.object(b,'request_json',side_effect=[{'objectIds':[1]}, {'features':[{'attributes':a,'geometry':{'x':-81.2,'y':29.5}}]}]):
            r=b.gis_records(None,county,cfg)[0]
        self.assertEqual(r['status'],'Approved');self.assertEqual(r['horizon'],'unknown')
        self.assertIsNone(r['coordinates']);self.assertFalse(r['publication_eligible'])
        for county in config['counties']:
            for source in county['gis']:
                self.assertFalse({'pocname','pocemail','pocphone','Internal_Notes','Folder_Link'} & set(source['fields']))
    def test_new_state_does_not_inherit_florida_agencies(self):
        county={'id':'GA-13127','name':'Glynn County','state':'GA','queries':[],'news_domains':[],
                'agency_queries':['site:dot.ga.gov project']}
        queries=' '.join(q for q,c in b.search_queries(county))
        self.assertIn('dot.ga.gov',queries);self.assertIn('GA',queries)
        self.assertNotIn('Florida',queries);self.assertNotIn('sjrwmd',queries)
    def test_description_and_parent_extraction(self):
        row=b.record('a',COUNTY,'Station','',['https://webapp.sjcfl.us/CSubmitalWeb/Record?ApplNum=2025000003&ApplType=CGCP'],record_number='2025000003')
        response=Mock(text='<h3>CGCP 2025000003</h3><p><b>Proj Desc:</b><span id="ContentPlaceHolder1_lblProjDesc">Clearing only; future facility needs separate review.</span></p><a href="Record.aspx?ApplType=SMADJ&amp;ApplNum=2024000052   ">SMADJ-2024000052</a><a href="ProjData?ApplNum=2025000003">Data</a>')
        client=Mock();client.get.return_value=response;b.enrich_register(client,row)
        self.assertIn('Clearing only',row['description']);self.assertEqual(len(row['related_records']),1)
        self.assertNotIn(' ',row['related_records'][0]['url']);self.assertEqual(row['status'],'Not verified')
    def test_polygon_hole_and_county_gate(self):
        outer=[[0,0],[10,0],[10,10],[0,10],[0,0]]
        hole=[[2,2],[8,2],[8,8],[2,8],[2,2]]
        candidate=b.geometry_candidate({'rings':[outer,hole]},[outer])
        point=(candidate['longitude'],candidate['latitude'])
        self.assertTrue(b.inside(point,[outer,hole]));self.assertFalse(candidate['publication_eligible'])
        self.assertIsNone(b.geometry_candidate({'x':20,'y':20},[outer]))
        self.assertIsNone(b.geometry_candidate({'paths':[[[11,11],[12,12]]]},[outer]))
        candidate=b.geometry_candidate({'paths':[[[1,1],[2,2]]]},[outer])
        self.assertEqual((candidate['longitude'],candidate['latitude']),(1.5,1.5))
        self.assertIn('alignment',candidate['method'])
        self.assertFalse(candidate['publication_eligible'])
    def test_clay_window_rolls_without_changing_existing_sources(self):
        from datetime import datetime,timezone
        year=datetime.now(timezone.utc).year
        self.assertEqual(b.gis_where({}),'1=1')
        config=json.loads((Path(__file__).parents[1]/'config/counties.json').read_text())
        clay=next(c for c in config['counties'] if c['id']=='FL-12019')
        clause=b.gis_where(clay['gis'][0])
        for value in (year,year-1,year-2):self.assertIn(str(value)+'-%',clause)
        self.assertNotIn('{year}',clause)

    def test_multiple_features_same_application_preserved(self):
        config=json.loads((Path(__file__).parents[1]/'config/counties.json').read_text())
        county=config['counties'][1];cfg=county['gis'][0]
        features=[{'attributes':{cfg['oid']:oid,cfg['identity']:123,cfg['name']:'Phase '+str(oid)}} for oid in (1,2)]
        with patch.object(b,'request_json',side_effect=[{'objectIds':[1,2]}, {'features':features}]):
            rows=b.gis_records(None,county,cfg)
        self.assertEqual(len(b.deduplicate(rows)),2)
if __name__=='__main__':unittest.main()
