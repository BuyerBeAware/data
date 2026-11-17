from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
import bba_rail_sources as r

FIX=Path(__file__).parent/'fixtures/rail'

class RailTests(unittest.TestCase):
    def test_real_planning_source_never_claims_construction(self):
        p=r.jta_project((FIX/'jta.html').read_text(),'2026-11-17')
        self.assertEqual(p['status'],'Planning')
        self.assertFalse(p['publicationAllowed'])
        self.assertNotIn('latitude',p)
        self.assertIn('not established',p['projectHorizon'])

    def test_old_scheduled_notice_is_not_active_or_confirmed_complete(self):
        p=r.county_projects((FIX/'county.json').read_text(),'2026-11-17',date(2026,11,17))
        race=next(x for x in p if x['id']=='sjc-rail-notice-73883')
        self.assertIn('Past scheduled',race['status'])
        self.assertIn('completion not verified',race['status'])
        self.assertIn('2026-02-10',race['projectHorizon'])

    def test_future_notice_still_not_verified_construction(self):
        p=r.county_projects((FIX/'county.json').read_text(),'2026-02-01',date(2026,2,1))
        self.assertTrue(all('not independently verified' in x['status'] for x in p))

    def test_bad_layout_and_duplicate_notices_fail(self):
        with self.assertRaises(r.RailSourceError):r.jta_project('<html>Error</html>','2026-11-17')
        posts=json.loads((FIX/'county.json').read_text());posts.append(posts[0])
        with self.assertRaises(r.RailSourceError):r.county_projects(json.dumps(posts),'2026-11-17',date(2026,11,17))

    def test_pipeline_operator_directory_is_context_not_project(self):
        def get(url):
            if url==r.JTA:return (FIX/'jta.html').read_text()
            if url==r.COUNTY:return (FIX/'county.json').read_text()
            return '<h1>Industrial Development</h1><p>Rail service information</p>'
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'projects.json').write_text('keep')
            report=r.run(root,get,today=date(2026,11,17))
            self.assertEqual(report['status'],'completed')
            self.assertEqual(len(report['projects']),3)
            self.assertEqual((root/'projects.json').read_text(),'keep')
            self.assertEqual(report['sources'][-1]['kind'],'operator_directory')

    def test_failed_sources_are_partial_and_clear_old_candidates(self):
        def fail(url):raise RuntimeError('private data')
        with tempfile.TemporaryDirectory() as d:
            report=r.run(Path(d),fail)
            self.assertEqual(report['status'],'partial')
            self.assertEqual(report['projects'],[])
            self.assertNotIn('private data',json.dumps(report))

if __name__=='__main__':unittest.main()
