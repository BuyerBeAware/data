from datetime import datetime,timezone
import unittest
import bba_capital_timing as b

NOW=datetime(2026,11,27,tzinfo=timezone.utc)
def ms(value):return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()*1000
class CapitalTimingTests(unittest.TestCase):
 def setUp(self):self.record={'projectId':'capital:1','title':'Project','status':'Not verified','details':'Project scope','sourceURL':'https://official.example/map','locationReviewedAt':NOW.isoformat(),'latitude':29.5,'longitude':-81.2}
 def run_fields(self,**fields):return b.enrich(self.record,fields,NOW)
 def test_construction_phase_and_actual_start_preserved(self):
  r=self.run_fields(projphase='Construction',phasestatus='Under Construction',actstart=ms('2026-06-08'),planstart=ms('2026-04-01'),planend=ms('2027-03-05'))
  self.assertEqual(r['constructionStatus'],'Under Construction');self.assertEqual(r['projectedStartDate'],'2026-06-08');self.assertEqual(r['projectedCompletionDate'],'2027-03-05');self.assertIn('tentative',r['schedule'])
  for k in ['projectId','latitude','longitude']:self.assertEqual(r[k],self.record[k])
 def test_dated_complete_project_becomes_past_evidence(self):
  r=self.run_fields(projphase='Complete',phasestatus='Complete',actstart=ms('2023-01-01'),actend=ms('2025-05-01'))
  self.assertEqual(r['constructionStatus'],'Completed');self.assertEqual(r['projectedCompletionDate'],'2025-05-01');self.assertFalse(r['dateEvidence']['independentFieldInspection'])
 def test_approval_fiscal_or_design_dates_do_not_establish_construction(self):
  for fields in [dict(projstatus='Board Authorized',fiscalyr=2031),dict(projphase='Design',phasestatus='Complete',actstart=ms('2025-01-01'),actend=ms('2026-01-01'))]:
   r=self.run_fields(**fields);self.assertNotIn('constructionStatus',r);self.assertNotIn('projectedStartDate',r)
 def test_conflicting_phase_actual_and_planned_dates_withheld(self):
  cases=[dict(projphase='Complete',phasestatus='Under Construction'),dict(projphase='Construction',phasestatus='Under Construction',actstart=ms('2027-01-01')),dict(projphase='Construction',phasestatus='Under Construction',actend=ms('2027-01-01')),dict(planstart=ms('2027-01-01'),planend=ms('2026-01-01')),dict(actstart=ms('2025-01-01'),actend=ms('2024-01-01')),dict(projphase='Construction',phasestatus='In Progress',actend=ms('2026-01-01')),dict(projphase='Construction',phasestatus='Under Construction',planend=ms('2025-01-01'))]
  for fields in cases:
   r=self.run_fields(**fields);self.assertTrue(r['timingEvidenceConflicts']);self.assertNotIn('constructionStatus',r);self.assertNotIn('projectedStartDate',r)
 def test_invalid_dates_are_not_accepted(self):
  for value in [True,float('nan'),float('inf'),0,'2026-01-01']:
   r=self.run_fields(projphase='Construction',phasestatus='Under Construction',actstart=value);self.assertTrue(r['timingEvidenceConflicts']);self.assertNotIn('constructionStatus',r)
 def test_phase_complete_without_actual_window_remains_unverified(self):
  r=self.run_fields(projphase='Complete',phasestatus='Complete');self.assertNotIn('constructionStatus',r)
 def test_missing_actual_start_is_not_assumed_from_planned_start(self):
  r=self.run_fields(projphase='Construction',phasestatus='Under Construction',planstart=ms('2025-01-01'),planend=ms('2027-01-01'));self.assertNotIn('constructionStatus',r)
 def test_conflict_does_not_retain_old_verified_dates(self):
  self.record.update(constructionStatus='Completed',projectedCompletionDate='2025-01-01')
  r=self.run_fields(projphase='Complete',phasestatus='Under Construction');self.assertNotIn('constructionStatus',r);self.assertNotIn('projectedCompletionDate',r)

if __name__=='__main__':unittest.main()
