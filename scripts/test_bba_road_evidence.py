import copy
import unittest
import bba_road_evidence as e

class RoadEvidenceTests(unittest.TestCase):
 def road(self):return {'id':'nflroads-5639','title':'FCE segment','county':'St. Johns','source_url':'https://nflroads.com/ProjectDetails.aspx?p=5639','source_project_number':'4530701','phase':'Not provided','schedule':{'start':'Early 2026','completion':'Late 2030/early 2031'}}
 def test_compact_identity_preserves_all_phase_parts(self):
  for value,want in [('4530701','453070-1'),('40931165201','409311-6-52-01'),('409311652','409311-6-52'),('409311-6-52-01','409311-6-52-01')]:self.assertEqual(e.financial_id(value),want)
  for value in ['123','453070','4530701 junk','453070-1-52-01-extra']:self.assertIsNone(e.financial_id(value))
 def test_tentative_window_keeps_both_years_and_source_phrase(self):
  f=e.date_fact('Late 2030/early 2031','project_expected_completion','source','Not provided');self.assertEqual(f['years'],[2030,2031]);self.assertEqual(f['sourceValue'],'Late 2030/early 2031');self.assertFalse(f['isActualActivityVerified'])
 def test_funding_unknown_boilerplate_and_invalid_dates_rejected(self):
  for v in ['FY 2030','Fiscal year 2030','Funding 2030','TBD','NA','Law 1993','2031 to 2030','2026 2030','February 31, 2027']:
   self.assertIsNone(e.date_fact(v,'project_start','s','Design'))
 def test_phase_specific_id_does_not_join_parent_segment(self):
  r=self.road();r['source_project_number']='409311-6-52-01'
  result=e.reconcile([r],[{'projectId':'other-phase','fdotFinancialProjectIDs':['409311-6-32-01']},{'projectId':'same','fdotFinancialProjectIDs':['40931165201']}])['records'][0]
  self.assertEqual(result['exactMapMatches'],['same']);self.assertEqual(result['relatedSegmentNeedsPhaseCheck'],['other-phase'])
 def test_county_case_and_same_road_are_not_financial_identity(self):
  r=e.reconcile([self.road()],[{'projectId':'4530701','title':'FCE segment'}])['records'][0];self.assertFalse(r['exactMapMatches']);self.assertFalse(r['dateFilterEligible']);self.assertIsNone(r['latitude'])
 def test_multi_id_and_duplicate_source_pages_are_conflicts(self):
  r=self.road();r['source_project_number']='429931-1, 429931-2'
  results=e.reconcile([r,copy.deepcopy(r)],[])['records'];self.assertTrue(all(len(x['conflicts'])==2 for x in results));self.assertTrue(all(not x['publicationAllowed'] for x in results))
 def test_unknown_phase_is_not_construction_status(self):
  r=e.reconcile([self.road()],[])['records'][0];self.assertEqual(r['sourcePhase'],'Not provided');self.assertNotIn('constructionStatus',r);self.assertEqual(r['recordID'],'nflroads-5639')
 def test_source_fields_and_snapshots_preserve_fact_provenance(self):
  r=self.road();r.update(source_checked_at='2026-11-25T00:00:00Z',source_sha256='a'*64,schedule_source_fields={'start':'Construction Start','completion':'Expected Completion'})
  result=e.reconcile([r],[])['records'][0];self.assertEqual(result['dateEvidence'][0]['event'],'construction_scheduled_start');self.assertEqual(result['dateEvidence'][0]['sourceSHA256'],'a'*64);self.assertEqual(result['dateEvidence'][1]['event'],'project_expected_completion')
 def test_readable_report_keeps_raw_dates_and_escapes_source_titles(self):
  r=self.road();r['title']='Road | [name] <tag>'
  report=e.reconcile([r],[]);text=e.markdown(report)
  self.assertIn('Late 2030/early 2031',text);self.assertIn('Road \\| \\[name\\] &lt;tag&gt;',text);self.assertIn('review records',text)
 def test_inverted_schedule_and_other_counties(self):
  r=self.road();r['schedule']={'start':'2031','completion':'2027'};a=e.reconcile([r],[])['records'][0];self.assertTrue(a['conflicts'])
  r['county']='Duval';self.assertFalse(e.reconcile([r],[])['records'])

class MapEvidenceTests(unittest.TestCase):
 def test_vector_drawing_is_not_verified_geometry(self):
  item='a'*32;child='b'*32;calls=[]
  def read(url):
   calls.append(url)
   return {'map':{'itemId':child}} if item in url else {'operationalLayers':[{'layerType':'VectorTileLayer','url':'http://localhost/private'}]}
  result=e.inspect_map('https://fetch-storm.maps.arcgis.com/apps/webappviewer/index.html?id='+item,read)
  self.assertEqual(result['alignmentStatus'],'vector_tiles_only');self.assertFalse(result['publicationAllowed']);self.assertEqual(len(calls),2);self.assertTrue(all(u.startswith('https://www.arcgis.com/sharing/') for u in calls))
 def test_experience_child_maps_are_all_reviewed_without_phase_transfer(self):
  item='a'*32;child='b'*32
  def read(url):return {'dataSources':{'map1':{'type':'WEB_MAP','itemId':child}}} if item in url else {'operationalLayers':[{'layerType':'ArcGISFeatureLayer'}]}
  result=e.inspect_map('https://experience.arcgis.com/experience/'+item,read);self.assertEqual(result['alignmentStatus'],'feature_or_mixed_layers_require_exact_project_query');self.assertFalse(result['publicationAllowed'])
 def test_private_credential_and_invalid_item_urls_rejected(self):
  for url in ['http://experience.arcgis.com/experience/'+'a'*32,'https://example.com/?id='+'a'*32,'https://user:password@experience.arcgis.com/experience/'+'a'*32,'https://experience.arcgis.com/experience/not-an-item']:
   with self.assertRaises(ValueError):e.inspect_map(url,lambda u:{})

if __name__=='__main__':unittest.main()
