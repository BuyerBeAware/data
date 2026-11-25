import copy
import json
from unittest import TestCase,main
from unittest.mock import Mock,patch
import bba_clay_enrichment as e
import bba_project_quality as q

TEXT='''Staff Report and Recommendations for ZON 25-0038
Property Information
Parcel ID: 06-05-24-005947-005-08 Parcel Address: 5082 CR 218
Current Zoning: BA-2 Current Land Use: COM
Proposed Zoning: BA Total Acres: 1.03
Introduction: Recommendation: approval. Meeting March 2026. Zoning code adopted 1993.
'''
class QualityTests(TestCase):
 def setUp(self):
  self.row={'projectId':'case:1','countyID':'FL-12019','title':'ZON 25-0038','status':'Not verified','plannedYear':'Not provided','schedule':'Work dates not verified','latitude':30.0,'longitude':-81.8,'locationEvidenceURLs':[],'reviewOnly':True,'publicationAllowed':False}
  self.doc={'recordID':'case:1','caseID':'ZON 25-0038','url':'https://claycounty.novusagenda.com/agendapublic/AttachmentViewer.ashx?AttachmentID=36415&ItemID=16472'}
  self.parcel={'features':[{'attributes':{'ParcelDisp':'06-05-24-005947-005-08','HouseNo':'5082','StreetName':'COUNTY ROAD 218','StreetCity':'Middleburg','TotalAcres':'1.03'},'geometry':{'rings':[[[-82,29.8],[-81.6,29.8],[-81.6,30.2],[-82,30.2],[-82,29.8]]]}}]}
 def call(self):
  reader=Mock();reader.pages=[Mock()];reader.pages[0].extract_text.return_value=TEXT
  client=Mock();client.get.return_value.content=b'%PDF mock'
  with patch.object(e,'PdfReader',return_value=reader),patch.object(e,'request_json',return_value=self.parcel):return e.enrich(self.row,self.doc,client,'2026-11-25T00:00:00Z')
 def test_exact_case_parcel_and_address_enrich_without_date_invention(self):
  result=self.call();self.assertIn('5082 CR 218',result['title']);self.assertIn('BA-2 to BA',result['title'])
  for key in ['projectId','latitude','longitude','plannedYear','schedule','publicationAllowed']:self.assertEqual(result[key],self.row[key])
  self.assertEqual(result['recordStage'],'zoning_record_documented');self.assertNotIn('approved',result['status']);self.assertNotIn('constructionStatus',result)
 def test_recommendation_and_ordinance_year_not_work_year(self):
  facts=e.extract(TEXT,'ZON 25-0038');self.assertFalse(facts['recommendationIsApproval']);self.assertNotIn('plannedYear',facts)
 def test_other_case_and_prefix_match_rejected(self):
  for text in [TEXT.replace('25-0038','25-00380'),TEXT.replace('25-0038','25-0039')]:
   with self.assertRaises(ValueError):e.extract(text,'ZON 25-0038')
 def test_ambiguous_parcel_rejected(self):
  with self.assertRaises(ValueError):e.extract(TEXT.replace('Parcel Address:','06-05-24-005947-005-09 Parcel Address:'),'ZON 25-0038')
 def test_moved_pin_wrong_parcel_or_duplicate_parcel_withheld(self):
  for change in ['outside','identity','duplicate']:
   saved=copy.deepcopy(self.parcel)
   if change=='outside':self.row['longitude']=-80
   if change=='identity':self.parcel['features'][0]['attributes']['ParcelDisp']='other'
   if change=='duplicate':self.parcel['features']*=2
   with self.assertRaises(ValueError):self.call()
   self.parcel=saved;self.row['longitude']=-81.8
 def test_same_house_wrong_street_rejected(self):
  self.parcel['features'][0]['attributes']['StreetName']='COUNTY ROAD 220'
  with self.assertRaises(ValueError):self.call()
 def test_untrusted_source_or_county_rejected(self):
  self.doc['url']='https://example.org/AttachmentViewer.ashx'
  with self.assertRaises(ValueError):self.call()
  self.doc['url']='https://claycounty.novusagenda.com/agendapublic/AttachmentViewer.ashx';self.row['countyID']='FL-12035'
  with self.assertRaises(ValueError):self.call()
 def test_quality_queue_preserves_separate_features_and_counties(self):
  other=dict(self.row,projectId='case:2');third=dict(other,projectId='case:3',countyID='FL-12035')
  result=q.audit([self.row,other,third]);self.assertEqual(result['records'],3);self.assertEqual(len(result['relatedRecordGroups']),1)
  self.assertFalse(result['relatedRecordGroups'][0]['automaticMerge']);self.assertEqual(len(result['queue']),3)
  self.assertTrue(all(not t['publicationAllowed'] for t in result['queue']))
 def test_verified_location_removes_gap_but_not_unknown_schedule(self):
  result=q.audit([self.call()]);self.assertNotIn('exact_case_parcel_or_boundary_not_verified',result['gapCounts']);self.assertEqual(result['gapCounts']['physical_work_dates_not_published_or_not_found'],1)

if __name__=='__main__':main()
