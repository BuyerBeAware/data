import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bba_publish_clay as b

class ClayTests(unittest.TestCase):
    def setUp(self):
        self.county=next(c for c in json.loads((b.ROOT/'config/counties.json').read_text())['counties'] if c['id']==b.CLAY)
        self.county.pop('project_documents',None) # Publication fixture has no registered document.
        self.cfg=self.county['gis'][0]
        self.stamp=datetime.now(timezone.utc).isoformat()
        self.row={'id':'clay:test:1','title':'PUD 26-0007','record_number':'PUD 26-0007','record_type':self.cfg['id'],
            'description':'Zoning amendment','source_urls':[self.cfg['url']+'/query?objectIds=1'],
            'official_fields':{'ORDINANCE_2':'2026-56','AMMENDED':'PUD'},'geometry_evidence':{'x':-81.8,'y':30}}
        self.report={'county_id':b.CLAY,'checked_at':self.stamp,'coverage':[{'source':c['id'],'status':'complete_snapshot'} for c in self.county['gis']], 'records':[self.row]}
        self.rings=[[[-82,29.6],[-81.6,29.6],[-81.6,30.3],[-82,30.3],[-82,29.6]]]
    def collect(self):
        with patch.object(b.core,'county_boundary',return_value=self.rings):return b.clay_records(self.report,self.county,None,self.stamp)
    def test_ordinance_year_is_not_work_year(self):
        rows,_=self.collect();r=rows[0]
        self.assertEqual(r['plannedYear'],'Not provided');self.assertEqual(r['projectHorizon'],'unknown')
        self.assertIn('2026-56',r['details']);self.assertIn('not a work schedule',r['details'])
        self.assertEqual(r['locationPrecision'],'estimated');self.assertTrue(r['reviewOnly'])
        self.assertNotIn('/rest/services/',r['sourceURL']);b.core.validate(rows)
    def test_alignment_point_is_sourced_and_undated(self):
        cfg=self.county['gis'][1];self.row.update(record_type=cfg['id'],title='Road corridor',source_urls=[cfg['url']+'/query?objectIds=1'],geometry_evidence={'paths':[[[-81.9,30],[-81.7,30]]]})
        rows,_=self.collect();self.assertEqual(rows[0]['type'],'Road Project');self.assertAlmostEqual(rows[0]['longitude'],-81.8)
        self.assertEqual(rows[0]['plannedYear'],'Not provided')
    def test_missing_stale_or_wrong_artifacts_rejected(self):
        for field,value in [('county_id','FL-12035'),('checked_at','2020-01-01T00:00:00Z'),('coverage',[])]:
            report=copy.deepcopy(self.report);report[field]=value
            with self.assertRaises(ValueError):b.clay_records(report,self.county,None,self.stamp)
    def test_outside_and_directional_claims_not_published(self):
        for geometry,description in [({'x':0,'y':0},''),({'x':-81.8,'y':30},'East of I-95')]:
            self.row.update(geometry_evidence=geometry,description=description)
            with self.assertRaises(ValueError):self.collect()
    def test_wrong_source_rejected(self):
        self.row['source_urls']=['https://example.com/query?objectIds=1']
        with self.assertRaises(ValueError):self.collect()
    def test_v2_preserves_v1_bytes_and_historical_pins(self):
        rows,_=self.collect()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'feeds').mkdir();(root/'config').mkdir()
            (root/'config/counties.json').write_text(json.dumps({'counties':[self.county]}))
            (root/(b.CLAY+'.review.json')).write_text(json.dumps(self.report))
            old=copy.deepcopy(rows[0]);old.update(projectId='original',countyID=b.core.SJC,latitude=30,longitude=-81.5)
            legacy=root/'feeds/beta.json';legacy.write_text(json.dumps({'counties':[b.core.SJC,b.core.FLAGLER],'projects':[old]}));before=legacy.read_bytes()
            with patch.object(b,'clay_records',return_value=(rows,{"withheld":[]})):b.run(root,root)
            self.assertEqual(legacy.read_bytes(),before)
            output=json.loads((root/'feeds/beta-v2.json').read_text())
            self.assertEqual(output['projects'][0],old);self.assertEqual(len(output['projects']),2)
            self.assertTrue((root/'feeds/history-v2'/(output['revision']+'.json')).exists())
            before_v2=(root/'feeds/beta-v2.json').read_bytes()
            with patch.object(b,'clay_records',side_effect=ValueError('Unavailable')):
                with self.assertRaises(ValueError):b.run(root,root)
            self.assertEqual((root/'feeds/beta-v2.json').read_bytes(),before_v2)

if __name__=='__main__':unittest.main()
