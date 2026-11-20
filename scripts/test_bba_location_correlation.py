import copy
import unittest
from unittest.mock import patch
from bba_location_correlation import constraints, check_side, review, apply_gate, road_geometry


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {'project_id': 'MAJMOD2026000009', 'name': 'Durbin Park',
            'lng': -81.50426102423594, 'lat': 30.093042504716806,
            'full_description': 'Mixed-Use Parcel E (AKA East Parcel), located on the eastern side of Interstate 95, to modify residential setbacks.',
            'source_record_url': 'https://example.invalid/record'}
        self.paths = [[[-81.4980081, 30.08], [-81.4980081, 30.11]],
                      [[-81.4977321, 30.08], [-81.4977321, 30.11]]]

    def road(self, *args):
        return self.paths, 'https://example.invalid/official-road'

    def polygon(self, *args):
        return [{'attributes': {'PROJECT_NUM': 'MAJMOD2026000009'}, 'geometry': {'rings': [[
            [-81.522, 30.082], [-81.481, 30.082], [-81.481, 30.105], [-81.522, 30.105],
            [-81.522, 30.082]]]}}], 'https://example.invalid/official-boundary'

    def test_durbin_west_point_conflicts_despite_inside_parent_polygon(self):
        result = review(self.candidate, self.road, self.polygon)
        self.assertEqual(result['status'], 'conflict')
        self.assertTrue(result['point_inside_application_boundary'])
        self.assertAlmostEqual(result['checks'][0]['distance_to_road_band_metres'], 602, delta=2)
        self.assertIsNone(result['replacement_coordinate'])
        self.assertFalse(result['publication_allowed'])

    def test_east_point_directionally_consistent_only(self):
        self.candidate['lng'] = -81.49
        result = review(self.candidate, self.road, self.polygon)
        self.assertEqual(result['status'], 'directionally_consistent_not_parcel_verified')
        self.assertFalse(result['publication_allowed'])

    def test_aliases_and_no_bare_east_parcel_inference(self):
        self.assertEqual(constraints('East Parcel, near I-95.'), [])
        for phrase, route in [('east of I-95', 'I-95'), ('west of U.S. 1', 'US-1'),
                              ('north of SR 23', 'SR-23'), ('south of State Road 16', 'SR-16'), ('north of County Road 210', 'CR-210'), ('east of US Highway 1', 'US-1')]:
            self.assertEqual(constraints(phrase)[0]['route'], route)

    def test_negative_or_span_wording_deferred(self):
        for prefix in ('not ', 'either ', 'extends from '):
            self.candidate['full_description'] = prefix + 'east of I-95'
            self.assertEqual(review(self.candidate, self.road, self.polygon)['status'], 'unresolved')

    def test_cardinal_directions(self):
        horizontal = [[[0, 0], [2, 0]]]
        self.assertEqual(check_side(1, 1, 'north', horizontal)['result'], 'consistent')
        self.assertEqual(check_side(1, 1, 'south', horizontal)['result'], 'conflict')
        self.assertEqual(check_side(-81.51, 30.09, 'west', self.paths)['result'], 'consistent')

    def test_road_band_missing_and_complex_geometry_defer(self):
        self.assertEqual(check_side(-81.498, 30.09, 'east', self.paths)['result'], 'unresolved')
        self.assertEqual(check_side(1, 1, 'east', [])['result'], 'unresolved')
        self.assertEqual(check_side(1, 1, 'east', [[[0, 0], [0, 2]], [[.1, 0], [.1, 2]]])['result'], 'unresolved')

    def test_source_failure_and_identity_mismatch_defer(self):
        def fail(*args):
            raise TimeoutError()
        self.assertEqual(review(self.candidate, fail, self.polygon)['status'], 'unresolved')
        self.candidate['lng'] = -81.49
        self.assertEqual(review(self.candidate, self.road, lambda *a: ([], 'url'))['status'], 'unresolved')

    def test_gate_retains_record_and_original_coordinate_but_withholds_pin(self):
        original = copy.deepcopy(self.candidate)
        projects = [{'projectId': self.candidate['project_id'], 'latitude': self.candidate['lat'],
                     'longitude': self.candidate['lng']}]
        reviews, safe = apply_gate([self.candidate], projects,
                                   lambda c: review(c, self.road, self.polygon))
        self.assertEqual(safe, [])
        self.assertEqual(self.candidate['lng'], original['lng'])
        self.assertEqual(projects[0]['longitude'], original['lng'])
        self.assertEqual(len(reviews), 1)

    def test_invalid_coordinate_never_verifies(self):
        self.candidate['lng'] = float('nan')
        self.assertNotIn(review(self.candidate, self.road, self.polygon)['status'],
                         ('directionally_consistent_not_parcel_verified',))

    def test_route_identity_includes_directional_suffixes_excludes_alternate_route(self):
        with patch('bba_location_correlation.query', return_value=([], 'url')) as get:
            for route, expected in [('US-1', "ROADNAME='1' AND PRETYPE='US'"),
                                    ('CR-210', "ROADNAME='210' AND PRETYPE='COUNTY ROAD'")]:
                road_geometry(route, -81.5, 30.1)
                self.assertEqual(get.call_args[0][1]['where'], expected)


if __name__ == '__main__':
    unittest.main()
