import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bba_long_range_sources as m


def table():
    header='Estimates 2025 projections 2030 2035 2040 2045 2050\n'
    rows=['County '+chr(65+i//26)+chr(65+i%26)+' 100,000 110,000 120,000 130,000 140,000 150,000' for i in range(66)]
    return header+'\n'.join(rows+['Saint Johns 348,336 405,745 452,013 489,262 519,627 546,300'])


class LongRangeTests(unittest.TestCase):
    def test_complete_population_table_and_vintage(self):
        rows=m.population_rows(table());self.assertEqual(len(rows),67)
        sj=next(r for r in rows if r['county']=='Saint Johns')
        self.assertEqual(sj['official_projection']['2050'],546300)
        self.assertEqual(sj['additional_residents_to_horizon'],197964)
        self.assertAlmostEqual(sj['growth_to_horizon_percent'],56.83,places=2)

    def test_incomplete_duplicate_and_wrong_columns_fail(self):
        for body in [table().replace('2050','2070',1),table().rsplit('\n',1)[0],table().replace('County AB','County AA')]:
            with self.assertRaises(ValueError):m.population_rows(body)

    def test_scenarios_remain_inferences_and_never_project_years(self):
        row=m.population_rows(table())[-1];sc=m.scenarios(row)
        self.assertEqual([r['year'] for r in sc],[2055,2060])
        self.assertGreater(sc[0]['central_scenario'],546300)
        for r in sc:
            self.assertLess(r['lower_scenario'],r['upper_scenario'])
            self.assertIsNone(r['project_year']);self.assertIsNone(r['latitude']);self.assertFalse(r['publication_allowed'])

    def test_declining_population_scenario_bounds_stay_ordered(self):
        row={'official_projection':{'2045':100000,'2050':90000}}
        sc=m.scenarios(row)[0]
        self.assertLess(sc['lower_scenario'],sc['central_scenario'])
        self.assertLess(sc['central_scenario'],sc['upper_scenario'])

    def test_outlier_trend_requires_review(self):
        with self.assertRaises(ValueError):m.scenarios({'official_projection':{'2045':100,'2050':10000}})

    def test_priority_is_not_a_project_or_spatial_prediction(self):
        tasks=m.search_tasks(m.population_rows(table()),[{'url':'official','text':'CR 210 and US 1'}])
        self.assertEqual(len(tasks),12)
        self.assertTrue(any(t['id']=='growth-saint-johns' for t in tasks))
        for task in tasks:
            self.assertFalse(task['publication_allowed']);self.assertIsNone(task['longitude']);self.assertIsNone(task['project_year'])
        self.assertEqual(tasks[0]['source_urls'],['official'])

    def test_missing_corridor_evidence_remains_unverified(self):
        tasks=m.search_tasks(m.population_rows(table()),[])
        self.assertEqual(tasks[0]['source_urls'],[])
        self.assertIn('still required',tasks[0]['reason'])

    def test_only_approved_https_sources_no_host_suffix_spoof(self):
        for url in ['http://www.sjcfl.us/a','https://www.sjcfl.us.evil.com/','https://u:p@www.sjcfl.us/','https://www.sjcfl.us:444/']:
            with self.assertRaises(ValueError):m.canonical(url)

    def test_plan_links_deduplicate_and_reject_untrusted(self):
        raw=b'<html><p>2050 plan</p><a href="/plan.pdf#x">2050 Land Use</a><a href="/plan.pdf#y">2050 Land Use</a><a href="https://evil.com/file.pdf">2055 plan</a></html>'
        doc=m.document('https://www.sjcfl.us/comprehensive-plan/',lambda u:(raw,'text/html',u))
        self.assertEqual(len(doc['links']),1)
        self.assertEqual(doc['links'][0]['url'],'https://www.sjcfl.us/plan.pdf')

    def test_failure_clears_stale_leads_and_preserves_production(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'projects.json').write_text('preserve')
            out=root/'research-output';out.mkdir();(out/'long_range.review.json').write_text('{"stale":true}')
            with patch.object(m,'document',side_effect=RuntimeError('private body')):
                r=m.run(root)
            self.assertEqual(r['status'],'partial');self.assertEqual(r['population'],[])
            self.assertNotIn('private body',json.dumps(r));self.assertNotIn('stale',(out/'long_range.review.json').read_text())
            self.assertEqual((root/'projects.json').read_text(),'preserve')

    def test_success_keeps_plan_horizon_separate_from_schedules(self):
        def doc(url,getter):
            return {'url':url,'resolved_url':url,'text':table() if url==m.POPULATION else '2055 framework CR 210','links':[],'sha256':'test'}
        with tempfile.TemporaryDirectory() as d,patch.object(m,'document',side_effect=doc):
            r=m.run(Path(d))
            self.assertEqual(r['status'],'completed');self.assertEqual(len(r['population']),67)
            self.assertEqual(r['unresolved_project_candidates'],[])
            self.assertFalse(r['publication_allowed'])
            self.assertTrue(any(x['horizon']==2055 for x in r['sources']))


class LongRangeAgentIntegrationTests(unittest.TestCase):
    def test_growth_search_tasks_are_actually_used_for_news_corroboration(self):
        import bba_agents as agent
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)
            (out/'long_range.review.json').write_text(json.dumps({'status':'completed','sources':[],
                'search_tasks':[{'query':'CR 210 long range planning'}]}))
            with patch.object(agent,'OUT',out),patch.object(agent,'NEWS_QUERIES',[]),patch.object(agent,'fetch',return_value='<rss><channel/></rss>') as fetch:
                agent.news_sources()
                self.assertIn('CR%20210%20long%20range%20planning',fetch.call_args[0][0])

    def test_official_plan_pages_enter_crawl_untrusted_links_do_not(self):
        import bba_agents as agent
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)
            (out/'long_range.review.json').write_text(json.dumps({'status':'completed','search_tasks':[],
                'sources':[{'sourceURL':'https://www.sjcfl.us/comprehensive-plan/','checkStatus':'checked'},
                           {'sourceURL':'https://evil.com/','checkStatus':'checked'}]}))
            with patch.object(agent,'OUT',out),patch.object(agent,'SEEDS',[]),patch.object(agent,'MAX_PAGES',2),patch.object(agent.time,'sleep'),patch.object(agent,'fetch',return_value='<html>2050 comprehensive plan</html>') as fetch:
                pages=agent.discover()
                self.assertEqual(len(pages),1)
                self.assertEqual(fetch.call_args[0][0],'https://www.sjcfl.us/comprehensive-plan/')


class ResearchBudgetTests(unittest.TestCase):
    def test_pdf_timeout_restores_the_previous_alarm_handler(self):
        import signal
        handler=signal.getsignal(signal.SIGALRM)
        with patch.object(m,'PdfReader',side_effect=TimeoutError('too slow')):
            with self.assertRaises(TimeoutError):m.pdf_text(b'%PDF')
        self.assertEqual(signal.getsignal(signal.SIGALRM),handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))

    def test_latest_official_population_link_is_used(self):
        latest='https://www.edr.state.fl.us/Content/population-demographics/data/MediumProjections_2026.pdf'
        def doc(url,getter):
            return {'url':url,'resolved_url':url,'text':table().replace('Estimates 2025','Estimates 2026') if url==latest else 'official plan',
                    'links':[{'url':latest,'title':'County projections'}] if url==m.SOURCES[-1][3] else [],'sha256':'test'}
        with tempfile.TemporaryDirectory() as d,patch.object(m,'document',side_effect=doc):
            report=m.run(Path(d))
            self.assertEqual(len(report['population']),67)
            self.assertEqual(report['population'][0]['source_url'],latest)
            self.assertEqual(report['population'][0]['base_year'],2026)

if __name__=='__main__':unittest.main()
