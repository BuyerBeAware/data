import copy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sjc_permits as s

FIXTURES = Path(__file__).parent/'fixtures'


class PermitTests(unittest.TestCase):
    def setUp(self):
        self.body = (FIXTURES/'sjc-commercial.xml').read_bytes()
        self.rows, _ = s.parse_export(self.body)
        self.parcels = json.loads((FIXTURES/'sjc-parcels.json').read_text())

    def test_real_xml_fields_and_minimization(self):
        self.assertEqual(len(self.rows), 3)
        row = next(p for p in self.rows if p['permit_id']=='12611794')
        self.assertEqual(row['parcel_id'], '1851370020')
        self.assertIn("Jeff's Bagel Run", row['name'])
        self.assertIsNone(row['closed_date'])
        self.assertNotIn('Email', json.dumps(self.rows))
        self.assertIn('PermitNo=12611794', row['source_url'])

    def test_real_parcel_join_and_suite_normalization(self):
        matches = s.parcel_matches(self.parcels)
        for p in self.rows:
            q = s.qualify(p, matches, s.PARCEL)
            self.assertIsNotNone(q['lat'])
            self.assertEqual(q['coordinate_verification'], 'exact_parcel_id_and_site_address_match')
            self.assertFalse(q['publication_allowed'])

    def test_address_mismatch_or_missing_or_ambiguous_has_no_pin(self):
        p = self.rows[0]
        for matches in ({}, {p['parcel_id']:[{'address':'1 WRONG ROAD','rings':[]}]},
                        {p['parcel_id']:[{},{}]}):
            self.assertIsNone(s.qualify(p, matches, s.PARCEL)['lat'])

    def test_polygon_hole_and_concavity(self):
        outer=[[-81.5,30],[-81.4,30],[-81.4,30.1],[-81.5,30.1],[-81.5,30]]
        hole=[[-81.48,30.02],[-81.42,30.02],[-81.42,30.08],[-81.48,30.08],[-81.48,30.02]]
        x,y=s.interior_point([outer,hole])
        self.assertTrue(-81.5<x<-81.4 and 30<y<30.1)
        self.assertFalse(-81.48<x<-81.42 and 30.02<y<30.08)
        concave=[[-81.5,30],[-81.4,30],[-81.4,30.02],[-81.48,30.02],[-81.48,30.1],[-81.5,30.1],[-81.5,30]]
        x,y=s.interior_point([concave])
        self.assertTrue(y<30.02 or x<-81.48)

    def test_invalid_geometry_or_projection(self):
        for rings in [[],[[[-81.5,30],[-81.4,30],[-81.5,30]]],[[[0,0]]*4]]:
            with self.assertRaises(s.PermitError): s.interior_point(rings)
        for change in [{'exceededTransferLimit':True},{'spatialReference':{'wkid':3857}},{'error':{}}]:
            with self.assertRaises(s.PermitError): s.parcel_matches(dict(self.parcels,**change))

    def test_malformed_xml_and_schema_fail(self):
        for body in [b'<html>login</html>',b'<!DOCTYPE a><NewDataSet/>',self.body.replace(b'PermitNo',b'OtherNo')]:
            with self.assertRaises(s.PermitError): s.parse_export(body)

    def test_duplicate_and_conflicting_rows(self):
        import xml.etree.ElementTree as E
        r=E.fromstring(self.body);r.append(copy.deepcopy(r[0]))
        self.assertEqual(len(s.parse_export(E.tostring(r))[0]),3)
        r[-1].find('STATUS').text='Different'
        with self.assertRaises(s.PermitError): s.parse_export(E.tostring(r))

    def test_form_scope_and_date_validation(self):
        fields=s.report_fields({'__VIEWSTATE':'x','__EVENTVALIDATION':'y'},date(2026,10,16),date(2026,11,16))
        self.assertEqual(fields['ctl00$cphBody$DropDownListPUType'],'C')
        self.assertNotIn('ctl00$cphBody$CheckBoxOnlyOpen',fields)
        with self.assertRaises(s.PermitError): s.report_fields({},date(2026,10,16),date(2026,11,16))

    def test_pipeline_and_failure_preserve_production_and_clear_stale(self):
        owner=self
        class Client:
            def export(self,a,b): return owner.body
            def parcels(self,pins): return owner.parcels,s.PARCEL
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'projects.json').write_text('keep')
            self.assertEqual(s.run(date(2026,10,16),date(2026,11,16),root,Client()),0)
            projects=json.loads((root/'sjc_permits.test.json').read_text())
            self.assertEqual(len(projects),3)
            base=root/'base.test.json';base.write_text(json.dumps([projects[0]]))
            self.assertEqual(s.run(date(2026,10,16),date(2026,11,16),root,Client(),base),0)
            merged=json.loads((root/'sjc_permits.merged.test.json').read_text())
            self.assertEqual(len(merged),3)
            self.assertEqual(json.loads(base.read_text()),[projects[0]])
            self.assertEqual(len({p['projectId'] for p in projects}),3)
            self.assertTrue(all(p['reviewOnly'] and not p['publicationAllowed'] for p in projects))
            with patch.object(Client,'export',side_effect=RuntimeError('untrusted')):
                self.assertEqual(s.run(date(2026,10,16),date(2026,11,16),root,Client()),1)
            self.assertEqual(json.loads((root/'sjc_permits.test.json').read_text()),[])
            self.assertEqual((root/'projects.json').read_text(),'keep')
            self.assertNotIn('untrusted',(root/'sjc_permits.review.json').read_text())

    def test_upsert_preserves_absent_records_and_updates_status(self):
        base = [dict(s.map_project(s.qualify(self.rows[0], s.parcel_matches(self.parcels), s.PARCEL)))]
        updated = dict(base[0], status="County permit: Certificate Completion")
        merged = s.merge_test_feed(base, [updated])
        self.assertEqual(len(merged),1)
        self.assertEqual(merged[0]['status'],updated['status'])
        self.assertNotEqual(base[0]['status'],updated['status'])
        self.assertEqual(s.merge_test_feed(base, []),base)
        with self.assertRaises(s.PermitError):
            s.merge_test_feed(base,[dict(updated,latitude=30.1)])
        with self.assertRaises(s.PermitError):
            s.merge_test_feed(base,[dict(updated,publicationAllowed=True)])

    def test_source_hosts_and_sql_inputs(self):
        self.assertTrue(s.allowed(s.REPORT))
        for url in ['http://webapp.sjcfl.us/a','https://evil.com',s.REPORT.replace('https://','https://u:p@')]:
            self.assertFalse(s.allowed(url))
        with self.assertRaises(s.PermitError): s.pin_id("123456' OR 1=1")


if __name__=='__main__': unittest.main()
