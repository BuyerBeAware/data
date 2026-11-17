import copy
import json
from pathlib import Path
import tempfile
import unittest
import bba_road_sources as r

FIX=Path(__file__).parent/'fixtures/roads'

class RoadTests(unittest.TestCase):
    def nfl(self,pid):
        listing={'source_url':r.NFL+'ProjectDetails.aspx?p='+pid,'site_project_id':pid,
                 'listing_status':'Future','discovery_url':r.NFL_LIST,'listing_county':'St. Johns'}
        return r.parse_detail((FIX/('nfl-detail-'+pid+'.html')).read_text(),listing)

    def test_real_cr210_has_financial_id_documents_and_unknown_schedule(self):
        item=self.nfl('6090')
        self.assertEqual(item['financial_project_id'],'441371-1')
        self.assertTrue(item['is_st_johns_county'])
        self.assertEqual(item['schedule'],{'start':'TBD','completion':'TBD'})
        self.assertIsNone(item['latitude'])
        self.assertFalse(item['publication_allowed'])
        self.assertGreater(len(item['documents']),0)

    def test_real_us1_without_financial_id_keeps_source_id(self):
        item=self.nfl('6144')
        self.assertEqual(item['id'],'nflroads-6144')
        self.assertIsNone(item['financial_project_id'])
        self.assertIn('Camacha',item['title'])
        self.assertEqual(item['county'],'St. Johns')

    def test_st_johns_loop_is_volusia_not_st_johns(self):
        listing=r.cfl_list((FIX/'cfl-list.html').read_text(),r.CFL_LISTS[0])[0]
        item=r.parse_detail((FIX/'cfl-detail.html').read_text(),listing)
        self.assertEqual(item['county'],'Volusia')
        self.assertFalse(item['is_st_johns_county'])
        self.assertEqual(item['phase'],'PD&E')
        self.assertNotEqual(item['status'],'Construction')

    def test_list_api_discovers_more_than_original_seeds(self):
        items=r.nfl_list((FIX/'nfl-results-2.json').read_text(),'Future')
        self.assertEqual(len(items),14)
        self.assertTrue(any(p['site_project_id']=='6090' for p in items))
        self.assertTrue(any(p['site_project_id']=='6144' for p in items))
        self.assertEqual(r.nfl_county_id('<select id="county_id"><option value="225">St. Johns</option></select>'),'225')

    def test_detail_identity_and_county_mismatches_fail(self):
        listing={'source_url':r.CFL+'project/111111-1','site_project_id':'111111-1','listing_status':'Future'}
        with self.assertRaises(r.SourceError):r.parse_detail((FIX/'cfl-detail.html').read_text(),listing)
        listing={'source_url':r.NFL+'ProjectDetails.aspx?p=6090','site_project_id':'6090','listing_status':'Future','listing_county':'Volusia'}
        with self.assertRaises(r.SourceError):r.parse_detail((FIX/'nfl-detail-6090.html').read_text(),listing)

    def test_real_id_variants_and_multiple_county_delimiters(self):
        self.assertEqual(self.nfl('5621')['financial_project_id'],'409311-6-52-01')
        self.assertEqual(self.nfl('5690')['financial_project_ids'],['429931-1','429931-2'])
        self.assertIsNone(self.nfl('5690')['financial_project_id'])
        raw=self.nfl('5639')
        self.assertEqual(raw['source_project_number'],'4530701')
        self.assertIsNone(raw['financial_project_id'])
        listing={'source_url':r.NFL+'ProjectDetails.aspx?p=5136','site_project_id':'5136',
                 'listing_status':'Active','listing_county':'Clay/St. Johns'}
        item=r.parse_detail((FIX/'nfl-detail-5136.html').read_text(),listing)
        self.assertTrue(item['is_st_johns_county'])

    def test_missing_county_is_unknown_never_guessed_from_city(self):
        listing={'source_url':r.CFL+'project/992600-7','site_project_id':'992600-7','listing_status':'Construction'}
        item=r.parse_detail((FIX/'cfl-missing-about.html').read_text(),listing)
        self.assertIsNone(item['county'])
        self.assertFalse(item['is_st_johns_county'])
        self.assertEqual(item['cities'],'Titusville')
        self.assertEqual(item['schedule']['completion'],'Fall 2026')

    def test_pipeline_follows_detail_pages_and_reports_budget(self):
        class Client:
            def read(self,url,payload=None):
                if url==r.NFL_LIST:return '<select id="county_id"><option value="225">St. Johns</option></select>'
                if url.endswith('/GetResults'):
                    return json.dumps({'d':"<table id='ProjectSearchResults'><tr><td><a href='ProjectDetails.aspx?p=6090'>CR 210</a></td><td>TBD</td><td>St. Johns</td></tr></table>"})
                if '/projects/' in url:return (FIX/'cfl-list.html').read_text()
                if 'p=6090' in url:return (FIX/'nfl-detail-6090.html').read_text()
                if '/project/' in url:return (FIX/'cfl-detail.html').read_text()
                raise AssertionError(url)
        with tempfile.TemporaryDirectory() as d:
            report=r.run(Path(d),Client())
            self.assertEqual(report['status'],'completed')
            self.assertEqual(report['project_count'],2)
            self.assertEqual(report['st_johns_project_count'],1)
            self.assertEqual(len(report['projects'][0]['listing_statuses']),2)
            limited=r.run(Path(d),Client(),max_details=1)
            self.assertEqual(limited['status'],'partial')
            self.assertEqual(limited['detail_budget_exceeded'],1)

    def test_missing_layouts_and_external_sources_fail(self):
        for payload in ['{}','<html>login</html>']:
            with self.assertRaises(r.SourceError):r.nfl_list(payload,'Future')
        with self.assertRaises(r.SourceError):r.cfl_list('<html>error</html>',r.CFL_LISTS[0])
        for url in ['https://evil.com/','https://nflroads.com.evil.com/','http://nflroads.com/','https://u:p@nflroads.com/']:
            self.assertFalse(r.approved(url))

    def test_failure_is_partial_clears_stale_and_preserves_production(self):
        class Fail:
            def read(self,*args):raise RuntimeError('private body')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'projects.json').write_text('preserve')
            out=root/'research-output';out.mkdir();(out/'road_projects.review.json').write_text('{"old":true}')
            report=r.run(root,Fail())
            self.assertEqual(report['status'],'partial')
            self.assertEqual(report['projects'],[])
            self.assertNotIn('private body',json.dumps(report))
            self.assertEqual((root/'projects.json').read_text(),'preserve')
            self.assertNotIn('old',json.loads((out/'road_projects.review.json').read_text()))

class AgentIntegrationTests(unittest.TestCase):
    def exercise(self,status):
        from contextlib import ExitStack, redirect_stdout
        import io
        from unittest.mock import patch
        import bba_agents as agent
        with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
            root=Path(d);out=root/'research-output';out.mkdir()
            (root/'projects.json').write_text('preserve')
            roads=[{'id':'fdot-441371-1','county':'St. Johns','publication_allowed':False}]
            stack.enter_context(patch.object(agent,'ROOT',root))
            stack.enter_context(patch.object(agent,'OUT',out))
            stack.enter_context(patch.object(agent,'discover_official_roads',return_value={'status':status,'projects':roads,'project_count':1}))
            stack.enter_context(patch.object(agent,'discover_rail_sources',return_value={'status':'completed','projects':[]}))
            stack.enter_context(patch.object(agent,'discover',return_value=[]))
            stack.enter_context(patch.object(agent,'news_sources',return_value=[]))
            ai=stack.enter_context(patch.object(agent,'ai_review',return_value={'enabled':False}))
            stack.enter_context(redirect_stdout(io.StringIO()))
            if status=='completed':agent.main()
            else:
                with self.assertRaises(SystemExit):agent.main()
            report=json.loads((out/'review_queue.json').read_text())
            self.assertEqual(report['road_projects'],roads)
            self.assertEqual(report['road_discovery_status'],status)
            self.assertEqual(ai.call_args.args[3],roads)
            self.assertEqual((root/'projects.json').read_text(),'preserve')

    def test_agent_includes_official_discovery_in_review_and_ai_context(self):
        self.exercise('completed')

    def test_agent_partial_discovery_writes_reports_then_fails(self):
        self.exercise('partial')


if __name__=='__main__':unittest.main()
