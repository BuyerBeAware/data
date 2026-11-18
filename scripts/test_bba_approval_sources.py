import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bba_approval_sources as a
import bba_road_sources as roads
PROGRAM_HTML='<h1>First Coast Expressway</h1><p>Shands Bridge. Construction on the first segment of the FCE was completed in summer 2019.</p><table><a class="RoadLink" href="http://nflroads.com/ProjectDetails?p=5136" target="_blank">First Coast Expressway – New St. Johns River Bridge</a><a class="RoadLink" href="http://nflroads.com/ProjectDetails?p=5605" target="_blank">First Coast Expressway – East of CR 2209 to East of CR 16A Spur</a><a class="RoadLink" href="http://nflroads.com/ProjectDetails?p=5639" target="_blank">First Coast Expressway – I-95 to East of CR 2209</a><tr>\n<td><a class="RoadLink" href="FirstCoastExpressway/documents/2014-04-07-st-johns-river-crossing-record-of-decision.pdf" target="_blank">St. Johns River Crossing Record of Decision</a></td>\n<td style="text-align:center;">4/7/14</td>\n</tr><tr>\n<td><a class="RoadLink" href="FirstCoastExpressway/documents/2013-10-11-st-johns-river-crossing-final-environmental-impact-statement.pdf" target="_blank">St. Johns River Crossing Final Environmental Impact Statement</a></td>\n<td style="text-align:center;">10/11/13</td>\n</tr></table>'
DETAIL_JSON='{"document_number": "2014-13321", "title": "Notice of Final Federal Agency Actions on Proposed Transportation Projects in Florida", "html_url": "https://www.federalregister.gov/documents/2014/06/09/2014-13321/notice-of-final-federal-agency-actions-on-proposed-transportation-projects-in-florida", "pdf_url": "https://www.govinfo.gov/content/pkg/FR-2014-06-09/pdf/2014-13321.pdf", "publication_date": "2014-06-09", "agencies": [{"raw_name": "DEPARTMENT OF TRANSPORTATION", "name": "Transportation Department", "id": 492, "url": "https://www.federalregister.gov/agencies/transportation-department", "json_url": "https://www.federalregister.gov/api/v1/agencies/492", "parent_id": null, "slug": "transportation-department"}, {"raw_name": "Federal Highway Administration", "name": "Federal Highway Administration", "id": 170, "url": "https://www.federalregister.gov/agencies/federal-highway-administration", "json_url": "https://www.federalregister.gov/api/v1/agencies/170", "parent_id": 492, "slug": "federal-highway-administration"}], "body_html_url": "https://www.federalregister.gov/documents/full_text/html/2014/06/09/2014-13321.html", "abstract": "This notice announces actions taken by the FHWA and other Federal Agencies since January 1, 2013, that are final within the meaning of 23 U.S.C. 139(l)(1). The actions relate to the proposed SR 40 (from 1 mile W of SR 326 to US 17/SR 15) in Marion, Lake and Volusia Counties; SR 200/US 301 Baldwin Bypass in Duval County; PJ Adams Parkway in Okaloosa County; SR 710 (from SR 76 to Blue Heron Blvd. at I-95) in Martin and Palm Beach Counties; SR 968/SW 1st Street Bridge over Miami River (from SW 5th Avenue to SW 2nd Avenue) in Miami-Dade County; Starke Bypass US 301 (from CR 227 to CR 233) in Bradford County; Crosstown Parkway (from Manth Lane to US 1) in St. Lucie County, and St. Johns River Crossing in Clay and St. Johns Counties in the State of Florida. These actions grant licenses, permits, and approvals for the project."}'

class ApprovalTests(unittest.TestCase):
    def test_real_corridor_follows_three_segments_and_keeps_historical_decisions(self):
        p=a.program(PROGRAM_HTML)
        self.assertEqual({s['site_project_id'] for s in p['segments']},{'5136','5605','5639'})
        self.assertTrue(all(s['source_url'].startswith('https://nflroads.com/ProjectDetails.aspx?p=') for s in p['segments']))
        rod=next(d for d in p['documents'] if d['evidence_kind']=='record_of_decision')
        self.assertEqual(rod['listed_date'],'4/7/14')
        self.assertIn('not automatically verified',rod['verification'])
        self.assertTrue(any('2019' in s for s in p['historical_completion_evidence']))
        self.assertFalse(p['publication_allowed'])
        self.assertIsNone(p['geometry'])

    def test_historical_multicounty_federal_notice_does_not_become_construction(self):
        d=json.loads(DETAIL_JSON)
        body='<p>St. Johns River Crossing in Clay and St. Johns Counties. Financial project 422938–7. Judicial review deadline November 2014.</p>'
        n=a.federal_notice(d,body)
        self.assertEqual(n['document_number'],'2014-13321')
        self.assertEqual(n['financial_project_ids'],['422938-7'])
        self.assertEqual(n['approval_status'],'final_agency_actions_announced')
        self.assertEqual(n['current_construction_status'],'not_established_by_historical_notice')
        self.assertFalse(n['publication_allowed'])
        self.assertIsNone(n['latitude'])
        self.assertIn('govinfo.gov',n['official_pdf_url'])

    def test_historical_E_identifiers_are_preserved(self):
        d=json.loads(DETAIL_JSON)
        d['document_number']='E8-357'
        d['html_url']='https://www.federalregister.gov/documents/2008/01/01/E8-357/example'
        n=a.federal_notice(d,'<p>Environmental review for a proposed highway.</p>')
        self.assertEqual(n['document_number'],'E8-357')

    def test_unrelated_identifier_is_not_invented(self):
        d=json.loads(DETAIL_JSON)
        n=a.federal_notice(d,'<p>SR 23; Shands Bridge; a meeting at 500 South Legacy Trail. Project number 4530701.</p>')
        self.assertEqual(n['financial_project_ids'],[])
        self.assertIsNone(n['longitude'])

    def test_url_identity_and_untrusted_hosts_are_rejected(self):
        d=json.loads(DETAIL_JSON);d['html_url']='https://www.federalregister.gov/documents/2020/01/01/2020-12345/wrong'
        with self.assertRaises(a.SourceError):a.federal_notice(d,'body')
        for u in ['http://nflroads.com','https://federalregister.gov.evil.com','https://u:p@www.federalregister.gov/','https://www.federalregister.gov:444/']:
            self.assertFalse(a.approved(u))
        with self.assertRaises(a.SourceError):a.program('<html>First Coast Expressway Shands Bridge</html>')
        for raw in ['{}','{"results":[],"total_pages":"2"}','not json']:
            with self.assertRaises(a.SourceError):a.fr_page(raw)

    def test_pagination_dedup_budgets_and_no_production_writes(self):
        detail=json.loads(DETAIL_JSON)
        doc={'document_number':detail['document_number'],'title':detail['title']}
        calls=[]
        class Client:
            def read(self,url):
                calls.append(url)
                if url==a.FCE:return PROGRAM_HTML
                if '/api/v1/documents.json?' in url:
                    return json.dumps({'total_pages':2,'count':2,'results':[doc]})
                if '/api/v1/documents/' in url:return json.dumps(detail)
                if '/full_text/html/' in url:return '<p>St. Johns River Crossing; 422938-7.</p>'
                raise AssertionError(url)
        with tempfile.TemporaryDirectory() as t,patch.object(a,'QUERIES',[('federal-highway-administration','Florida')]):
            root=Path(t);(root/'projects.json').write_text('preserve')
            r=a.run(root,Client())
            self.assertEqual(r['status'],'completed');self.assertEqual(r['notice_count'],1)
            self.assertTrue(any('page=2' in u for u in calls))
            self.assertIn('order=oldest',calls[1])
            self.assertEqual((root/'projects.json').read_text(),'preserve')
            self.assertEqual(a.run(root,Client(),max_pages=1)['status'],'partial')
            self.assertEqual(a.run(root,Client(),max_details=0)['status'],'partial')

    def test_old_federal_html_uses_explicit_official_gpo_fallback(self):
        from urllib.error import HTTPError
        detail=json.loads(DETAIL_JSON)
        raw='https://www.govinfo.gov/content/pkg/FR-2014-06-09/html/2014-13321.htm'
        detail['raw_text_url']=raw
        doc={'document_number':detail['document_number'],'title':detail['title']}
        class Client:
            def read(self,url):
                if url==a.FCE:return PROGRAM_HTML
                if '/api/v1/documents.json?' in url:return json.dumps({'total_pages':1,'count':1,'results':[doc]})
                if '/api/v1/documents/' in url:return json.dumps(detail)
                if url==raw:return '<p>St. Johns River Crossing; 422938-7.</p>'
                raise HTTPError(url,404,'Not found',None,None)
        with tempfile.TemporaryDirectory() as t,patch.object(a,'QUERIES',[('federal-highway-administration','Florida')]):
            r=a.run(Path(t),Client())
            self.assertEqual(r['status'],'completed')
            self.assertEqual(r['notices'][0]['body_source_url'],raw)
            self.assertEqual(r['notices'][0]['financial_project_ids'],['422938-7'])

    def test_failed_sources_clear_stale_report_and_do_not_leak_errors(self):
        class Fail:
            def read(self,url):raise RuntimeError('sensitive debug body')
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);r=a.run(root,Fail())
            self.assertEqual(r['status'],'partial');self.assertEqual(r['notices'],[])
            self.assertNotIn('sensitive debug body',json.dumps(r))
            self.assertEqual(json.loads((root/'research-output/major_road_approvals.review.json').read_text())['notices'],[])

if __name__=='__main__':unittest.main()
