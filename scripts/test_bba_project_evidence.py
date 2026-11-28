import copy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bba_project_evidence as e
import bba_commercial_permits as c
from bba_project_followup import queries
from bba_approval_sources import Client
from urllib.error import HTTPError


class EvidenceTests(unittest.TestCase):
    def test_related_decision_does_not_deny_original_or_change_location_dates(self):
        row=dict(projectId='COMPAMD2024000005',status='Unknown',latitude=30,longitude=-81,plannedYear='Not provided')
        entry=dict(projectIDs=[row['projectId'],'MAJMOD2025000007'],aliases=['Water Lily','Tocoi River Estates'],relatedApplicationIDs=['MAJMOD2025000007'],context='Related case',
                   events=[dict(applicationID='MAJMOD2025000007',kind='Related application decision')])
        result=e.enrich([row],dict(projects=[entry]))[0]
        self.assertEqual(result['status'],'Unknown');self.assertEqual(result['latitude'],30)
        self.assertEqual(result['plannedYear'],'Not provided');self.assertEqual(row,dict(projectId='COMPAMD2024000005',status='Unknown',latitude=30,longitude=-81,plannedYear='Not provided'))

    def test_identity_mismatch_gets_no_history(self):
        row=dict(projectId='other',status='Unknown')
        entry=dict(projectIDs=['case'],events=[dict(applicationID='case')])
        self.assertEqual(e.enrich([row],dict(projects=[entry])),[row])

    def test_changed_source_or_failure_withholds_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config').mkdir()
            entry=dict(projectIDs=['case'],events=[dict(sourceURL='https://www.sjcfl.us/case.pdf',applicationID='case',requiredTerms=['exact case denial'])])
            (root/'config/project-evidence.json').write_text(json.dumps(dict(projects=[entry])))
            result=e.collect(root,lambda u:('Unrelated case approval','hash'))
            self.assertEqual(result['projects'][0]['events'],[]);self.assertEqual(len(result['errors']),1)
            def unavailable(u):raise TimeoutError()
            self.assertEqual(e.collect(root,unavailable)['projects'][0]['events'],[])

    def test_all_historical_months_rotate_and_windows_are_bounded(self):
        days=[date(2026,11,1).fromordinal(date(2026,11,1).toordinal()+i) for i in range(24)]
        historical=set()
        for today in days:
            for start,end in c.windows(today):
                self.assertLessEqual((end-start).days,31);self.assertLessEqual(end,today)
                if start.day==1 and start!=date(2025,12,1):historical.add((start.year,start.month))
        self.assertGreaterEqual(len(historical),23)

    def test_alias_search_preserves_case_ids_and_limits(self):
        registry=dict(projects=[dict(projectIDs=['case1','case2'],aliases=['Old name','New name'])])
        tasks=queries([],registry,2)
        self.assertEqual(len(tasks),2);self.assertEqual(tasks[0]['projectIDs'],['case1','case2'])
        self.assertIn('New name',tasks[1]['query'])

    def test_unapproved_source_does_not_fetch(self):
        with patch.object(e.requests,'get') as get:
            with self.assertRaises(ValueError):e.read_source('https://attacker.example/file')
            get.assert_not_called()

    def test_permanent_http_error_is_not_retried(self):
        with patch('bba_approval_sources.build_opener') as opener,patch('bba_approval_sources.time.sleep') as sleep:
            opener.return_value.open.side_effect=HTTPError('url',404,'missing',{},None)
            with self.assertRaises(HTTPError):Client().read('https://www.federalregister.gov/test')
            self.assertEqual(opener.return_value.open.call_count,1);sleep.assert_not_called()

    def test_transient_http_retries_are_bounded(self):
        with patch('bba_approval_sources.build_opener') as opener,patch('bba_approval_sources.time.sleep') as sleep:
            opener.return_value.open.side_effect=HTTPError('url',503,'busy',{},None)
            with self.assertRaises(HTTPError):Client().read('https://www.federalregister.gov/test')
            self.assertEqual(opener.return_value.open.call_count,3);self.assertEqual(sleep.call_count,2)
