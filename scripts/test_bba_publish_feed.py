import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bba_publish_feed as b

class PublishTests(unittest.TestCase):
    def record(self,key='new'):
        return b.base_record(key,'Project','Work description','https://www.palmcoast.gov/geographic-information-system',29.5,-81.2,b.FLAGLER,datetime.now(timezone.utc).isoformat(),'Official geometry checked inside county',['https://www.palmcoast.gov/geographic-information-system'])
    def test_merge_retains_history_and_preserves_moved_pin(self):
        old=self.record('a');new=self.record('a');new['latitude']=29.6
        rows,changes=b.merge([old],[new,self.record('b')]);self.assertEqual(len(rows),2);self.assertEqual(rows[0],old)
        self.assertEqual(changes['added'],1);self.assertEqual(len(changes['withheld']),1)
    def test_exact_identity_updates_details_without_moving(self):
        old=self.record();new=dict(old,details='Updated scope');rows,_=b.merge([old],[new]);self.assertEqual(rows,[new])
    def test_bad_estimates_and_coordinates_rejected(self):
        for field,value in [('locationEstimateBasis',''),('locationEvidenceURLs',[]),('locationReviewedAt','bad'),('latitude',float('nan')),('latitude',0),('countyID','other'),('reviewOnly',False),('sourceURL','http://example.org')]:
            r=self.record();r[field]=value
            with self.assertRaises((ValueError,KeyError)):b.validate([r])
    def test_conflicting_incoming_records_fail(self):
        r=self.record('new');other=dict(r,details='Conflicting scope')
        with self.assertRaises(ValueError):b.merge([self.record('old')],[r,other])
    def test_duplicate_empty_and_oversize_feeds_rejected(self):
        r=self.record()
        for rows in [[],[r,r],[r]*5001]:
            with self.assertRaises(ValueError):b.validate(rows)
    def test_input_failure_keeps_published_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'feeds').mkdir();(root/'config').mkdir()
            prior={'projects':[self.record()],'revision':'prior'};target=root/'feeds/beta.json';target.write_text(json.dumps(prior));before=target.read_bytes()
            (root/'config/counties.json').write_text('{"counties":[]}')
            with patch.object(b,'sjc_records',side_effect=ValueError('Source unavailable')):
                with self.assertRaises(ValueError):b.run(root,root)
            self.assertEqual(target.read_bytes(),before)
    def test_success_keeps_durable_history_and_matches_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'feeds').mkdir();(root/'config').mkdir()
            old=self.record('old');old['locationReviewedAt']='2026-11-01T00:00:00Z'
            (root/'feeds/beta-seed.json').write_text(json.dumps([old]));(root/'config/counties.json').write_text(json.dumps({'counties':[{'id':b.FLAGLER}]}));(root/(b.FLAGLER+'.review.json')).write_text('{}')
            with patch.object(b,'sjc_records',return_value=([],{})),patch.object(b,'flagler_records',return_value=([self.record('new')],{})):
                b.run(root,root)
            payload=json.loads((root/'feeds/beta.json').read_text());self.assertEqual(len(payload['projects']),2)
            self.assertEqual(payload['revision'],b.digest(payload['projects']));self.assertTrue((root/'feeds/history'/(payload['revision']+'.json')).exists())
    def test_directional_claims_withheld_and_geometry_recomputed(self):
        cfg={'id':'source','url':'https://official.example/layer','map_url':'https://www.palmcoast.gov/geographic-information-system','authority':'Palm Coast'}
        row={'id':'a','title':'Project','record_type':'source','description':'East of I-95','source_urls':[cfg['url']+'/query?objectIds=1'],'geometry_evidence':{'x':-81.2,'y':29.5}}
        report={'county_id':b.FLAGLER,'checked_at':datetime.now(timezone.utc).isoformat(),'coverage':[{'source':'source','status':'complete_snapshot'}],'records':[row]}
        rings=[[[-81.5,29.2],[-81,29.2],[-81,29.8],[-81.5,29.8],[-81.5,29.2]]]
        with patch.object(b,'county_boundary',return_value=rings):
            accepted,diagnostics=b.flagler_records(report,{'gis':[cfg]},None,report['checked_at'])
        self.assertFalse(accepted);self.assertEqual(len(diagnostics['withheld']),1)
        row['description']='Site work';row['geometry_evidence']={'x':0,'y':0}
        with patch.object(b,'county_boundary',return_value=rings):accepted,_=b.flagler_records(report,{'gis':[cfg]},None,report['checked_at'])
        self.assertFalse(accepted)
    def test_missing_core_snapshot_aborts(self):
        with self.assertRaises(ValueError):b.flagler_records({'county_id':b.FLAGLER,'checked_at':datetime.now(timezone.utc).isoformat(),'coverage':[]},{'gis':[{'id':'needed'}]},None,'')

if __name__=='__main__':unittest.main()
