#!/usr/bin/env python3
"""Review county point/description conflicts; never invent replacement coordinates.

Supported checks are explicit cardinal-side statements about numbered I/US/SR/CR
roads. Other wording remains unverified. This is a rejection gate, not geocoding.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sjc_import import collect, normalize, write, ROOT

ROAD = 'https://www.gis.sjcfl.us/portal_sjcgis/rest/services/Road/MapServer/2/query'
POLYGON = ('https://services1.arcgis.com/t2yugAJW83eUIFui/arcgis/rest/services/'
           'WATS_Project_Polygon/FeatureServer/0/query')
PATTERN = re.compile(
    r'\b(?:located\s+)?(?:on\s+the\s+)?(east|west|north|south)(?:ern)?'
    r'\s+(?:side\s+of\s+|of\s+)(?:the\s+)?'
    r'(Interstate|I|US|U\.S\.|SR|State\s+Road|CR|County\s+Road)\s*(?:Highway\s*)?[-.]?\s*(\d+)\b', re.I)


def constraints(description):
    """Retain exact evidence; reject negative/alternative clauses as ambiguous."""
    result = []
    for match in PATTERN.finditer(description):
        prefix = re.split(r'[.;!?]', description[:match.start()])[-1].lower()
        ambiguous = bool(re.search(r'\b(not|except|either|both|extends?|extending)\b', prefix))
        family = match[2].lower().replace('.', '')
        family = 'I' if family in ('i', 'interstate') else 'US' if family == 'us' else 'CR' if family in ('cr', 'county road') else 'SR'
        result.append({'side': match[1].lower(), 'route': family + '-' + match[3],
                       'quote': match[0], 'ambiguous_wording': ambiguous})
    return result


def query(endpoint, params):
    url = endpoint + '?' + urlencode(dict(params, f='json', outSR='4326'))
    with urlopen(Request(url, headers={'User-Agent': 'BuyerBeAware location review'}), timeout=30) as response:
        body = response.read(8_000_001)
    if len(body) > 8_000_000:
        raise ValueError('GIS response too large')
    data = json.loads(body)
    if data.get('error') or data.get('exceededTransferLimit') or not isinstance(data.get('features'), list):
        raise ValueError('GIS response failed or truncated')
    if (data.get('spatialReference') or {}).get('wkid') != 4326:
        raise ValueError('GIS response is not WGS84')
    return data['features'], url


def road_geometry(route, lng, lat):
    family, number = route.split('-')
    names = {'I': 'Interstate ', 'US': 'US ', 'SR': 'State Road ', 'CR': 'County Road '}
    features, url = query(ROAD, {
        'where': "NAME='" + names[family] + number + "'",
        'geometry': f'{lng-.1},{lat-.1},{lng+.1},{lat+.1}',
        'geometryType': 'esriGeometryEnvelope', 'inSR': '4326',
        'outFields': 'OBJECTID,NAME,ROADNAME,PRETYPE', 'returnGeometry': 'true'})
    return [path for f in features for path in f.get('geometry', {}).get('paths', [])], url


def point_in_polygon(lng, lat, rings):
    inside = False
    for ring in rings:
        for a, b in zip(ring, ring[1:] + ring[:1]):
            if (a[1] > lat) != (b[1] > lat):
                if lng < (b[0]-a[0])*(lat-a[1])/(b[1]-a[1])+a[0]:
                    inside = not inside
    return inside


def check_side(lng, lat, side, paths):
    """Intersect a local transect with all carriageways; wide/complex bands defer."""
    axis = 1 if side in ('east', 'west') else 0
    other = 1-axis
    point = (lng, lat)
    crossings = []
    for path in paths:
        for a, b in zip(path, path[1:]):
            if min(a[axis], b[axis]) <= point[axis] < max(a[axis], b[axis]):
                crossings.append(a[other] + (point[axis]-a[axis]) *
                                 (b[other]-a[other])/(b[axis]-a[axis]))
    if not crossings:
        return {'result': 'unresolved', 'reason': 'no local road transect'}
    scale = 111320 * (math.cos(math.radians(lat)) if other == 0 else 1)
    lo, hi = min(crossings), max(crossings)
    if (hi-lo)*scale > 500:
        return {'result': 'unresolved', 'reason': 'ambiguous road geometry'}
    margin = 50 / scale
    if lo-margin <= point[other] <= hi+margin:
        return {'result': 'unresolved', 'reason': 'point within road uncertainty band'}
    positive = point[other] > hi
    matches = positive == (side in ('east', 'north'))
    return {'result': 'consistent' if matches else 'conflict',
            'distance_to_road_band_metres': round(min(abs(point[other]-lo), abs(point[other]-hi))*scale),
            'road_transect_range': [lo, hi]}


def review(candidate, get_road=road_geometry, get_polygon=query):
    checks = []
    lng, lat = candidate.get('lng'), candidate.get('lat')
    valid = all(type(v) in (float, int) and math.isfinite(v) for v in (lng, lat))
    valid = valid and -180 <= lng <= 180 and -90 <= lat <= 90
    for constraint in constraints(candidate.get('full_description', '')):
        check = dict(constraint, result='unresolved')
        try:
            if not valid or constraint['ambiguous_wording']:
                raise ValueError('invalid coordinates or ambiguous wording')
            paths, url = get_road(constraint['route'], lng, lat)
            check['road_source_url'] = url
            check.update(check_side(lng, lat, constraint['side'], paths))
        except Exception as exc:
            check['reason'] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        checks.append(check)
    result = {'project_id': candidate['project_id'], 'name': candidate['name'],
              'original_coordinate': {'latitude': lat, 'longitude': lng},
              'source_record_url': candidate['source_record_url'], 'checks': checks,
              'status': 'not_checked_no_supported_statement',
              'publication_allowed': False, 'replacement_coordinate': None}
    if checks:
        result['status'] = ('conflict' if any(c['result'] == 'conflict' for c in checks)
                            else 'unresolved' if any(c['result'] == 'unresolved' for c in checks)
                            else 'directionally_consistent_not_parcel_verified')
        try:
            number = candidate['project_id']
            if not re.fullmatch(r'[A-Za-z0-9()_-]{3,80}', number):
                raise ValueError('invalid application identity')
            features, url = get_polygon(POLYGON, {'where': "PROJECT_NUM='" + number + "'",
                'outFields': 'PROJECT_NUM', 'returnGeometry': 'true'})
            if len(features) != 1 or features[0]['attributes']['PROJECT_NUM'] != number:
                raise ValueError('missing or ambiguous same-application boundary')
            rings = features[0]['geometry']['rings']
            result['boundary_source_url'] = url
            result['point_inside_application_boundary'] = point_in_polygon(lng, lat, rings) if valid else False
            if not result['point_inside_application_boundary']:
                result['status'] = 'conflict'
        except Exception as exc:
            result['boundary_error'] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            if result['status'] != 'conflict':
                result['status'] = 'unresolved'
        result['required_action'] = ('Verify the specific parcel/subarea against official application plans; '
                                     'a broad PUD boundary or address alone cannot resolve a directional conflict.')
    return result


def apply_gate(candidates, projects, reviewer=review):
    reviews = [reviewer(c) for c in candidates]
    by_id = {r['project_id']: r for r in reviews}
    for candidate in candidates:
        candidate['location_correlation'] = by_id[candidate['project_id']]
    safe = []
    for project in projects:
        row = by_id[project['projectId']]
        if row['status'] not in ('conflict', 'unresolved'):
            safe.append(project)
    return reviews, safe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'research-output')
    parser.add_argument('--existing-import', action='store_true')
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    if args.existing_import:
        report = json.loads((out/'sjc_candidates.json').read_text())
        if report.get('status') != 'completed':
            raise ValueError('county import incomplete')
        candidates = report['candidates']
        projects = json.loads((out/'sjc_projects.test.json').read_text())
    else:
        candidates, projects, _ = normalize(collect())
        report = {'status': 'completed', 'candidates': candidates, 'publication_allowed': False}
    # A failed gate must not leave the preceding import's ungated map-test file.
    write(out/'sjc_projects.test.json', [])
    reviews, safe = apply_gate(candidates, projects)
    report['map_test_count'] = len(safe)
    write(out/'sjc_candidates.json', report)
    write(out/'sjc_projects.test.json', safe)
    ledger = {'generated_at': datetime.now(timezone.utc).isoformat(), 'publication_allowed': False,
              'coverage': 'Explicit cardinal-side statements for numbered I/US/SR/CR roads only; '
                          'consistent means directional evidence only, never parcel verification.',
              'withheld_map_test_count': len(projects)-len(safe), 'records': reviews}
    write(out/'location_correlation.review.json', ledger)
    print(f'Location review: {len(reviews)} records; {len(projects)-len(safe)} test pins withheld')


if __name__ == '__main__':
    main()
