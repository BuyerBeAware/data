#!/usr/bin/env python3
"""Read county commercial XML reports and join exact parcel IDs to county geometry.
Outputs are separate review/test files. No production feed writes or geocoding.
Python 3.9+, standard library only.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler
import xml.etree.ElementTree as ET

def corridors(*values):
    value = " ".join(str(v or "") for v in values).upper()
    return [name for name, pattern in {
        "CR 210": r"\b(?:CR[\s-]*210|COUNTY\s+(?:ROAD|RD\.?)[\s-]*210)\b",
        "US-1": r"\b(?:US[\s.-]*1|U\.?S\.?\s*(?:HIGHWAY\s*)?1)\b"}.items() if re.search(pattern, value)]
from sjc_import import write

REPORT = 'https://webapp.sjcfl.us/WATSWebX/WATSReport/MasterQuery.aspx'
PARCEL = 'https://www.gis.sjcfl.us/portal_sjcgis/rest/services/Hosted/Parcel/FeatureServer/0/query'
PERMIT = 'https://webapp.sjcfl.us/WATSWebX/Permit/BLPermit.aspx'
# Include commercial building development; routine roof/sign/pool work remains in review counts.
USES = {'213', '214', '318', '319', '320', '320S', '321', '322', '323',
        '324', '324M', '324S', '325', '326', '326M', '327', '327S', '328C',
        '437', '437B', '437R'}
MAX_BYTES = 8_000_000


class PermitError(ValueError):
    pass


def allowed(url):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and p.username is None and p.password is None
                and p.port in (None, 443) and (p.hostname, p.path) in {
                    ('webapp.sjcfl.us', urlsplit(REPORT).path),
                    ('www.gis.sjcfl.us', urlsplit(PARCEL).path),
                    ('webapp.sjcfl.us', urlsplit(PERMIT).path)})
    except ValueError:
        return False


class Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise PermitError('Unexpected source redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self):
        self.opener = build_opener(Redirects(), HTTPCookieProcessor(CookieJar()))

    def read(self, url, data=None):
        if not allowed(url):
            raise PermitError('Unapproved source URL')
        req = Request(url, data=data, headers={'User-Agent': 'BuyerBeAware/1.0 public permit research'})
        with self.opener.open(req, timeout=60) as response:
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise PermitError('Source exceeded response size limit')
        return body

    def export(self, start, end):
        form = HiddenFields()
        form.feed(self.read(REPORT).decode('utf-8-sig'))
        return self.read(REPORT, urlencode(report_fields(form.fields, start, end)).encode())

    def parcels(self, pins):
        # Inputs are validated ten-digit identifiers, never arbitrary SQL.
        values = [pin[:6] + ' ' + pin[6:] for pin in pins]
        params = {'where': 'pin IN (' + ','.join("'" + p + "'" for p in values) + ')',
                  'outFields': 'pin,prp_addr', 'returnGeometry': 'true', 'outSR': '4326', 'f': 'json'}
        url = PARCEL + '?' + urlencode(params)
        return json.loads(self.read(url)), url


class HiddenFields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'input' and a.get('type') == 'hidden' and a.get('name'):
            self.fields[a['name']] = a.get('value', '')


def report_fields(hidden, start, end):
    if not 0 <= (end - start).days <= 31:
        raise PermitError('Report windows must be at most 31 days')
    if not hidden.get('__VIEWSTATE') or not hidden.get('__EVENTVALIDATION'):
        raise PermitError('County report form state missing or layout changed')
    fields = dict(hidden)
    fields.update({'ctl00$cphBody$ddDtTyp': '1',
                   'ctl00$cphBody$txtFromDt': start.strftime('%m/%d/%Y'),
                   'ctl00$cphBody$txtToDt': end.strftime('%m/%d/%Y'),
                   'ctl00$cphBody$DropDownListPUType': 'C',
                   'ctl00$cphBody$RadioButtonListType': 'D',
                   'ctl00$cphBody$btnXML': 'XML'})
    return fields


def pin_id(value):
    if not re.fullmatch(r'\d{6}[- ]?\d{4}', value):
        raise PermitError('Invalid parcel ID')
    return re.sub(r'[- ]', '', value)


def address_key(value):
    value = value.upper().strip()
    value = re.split(r'\b(?:STE|SUITE|UNIT|BLDG|BUILDING)\b|#', value)[0]
    value = re.sub(r'\bUS\s+(?:HIGHWAY|HWY)\b', 'US', value)
    value = re.sub(r'\b(?:CR|COUNTY RD)\b', 'COUNTY ROAD', value)
    return re.sub(r'[^A-Z0-9]', '', value)


def source_date(value):
    if not value or value.startswith('0001-'):
        return None
    try:
        value = re.sub(r'\.(\d+)(?=[+-]|$)', lambda m: '.' + m[1][:6].ljust(6, '0'), value)
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        raise PermitError('Invalid source date') from None


def parse_export(body):
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise PermitError('Unexpected XML declarations')
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise PermitError('County did not return valid XML') from None
    if root.tag != 'NewDataSet' or any(row.tag != 'MasterQuery' for row in root):
        raise PermitError('Unknown report XML schema')
    rows, seen, excluded = [], {}, 0
    for row in root:
        d = {e.tag: ' '.join((e.text or '').split()) for e in row}
        required = {'PermitNo', 'ParcelNo', 'PU', 'PUDesc', 'STATUS', 'ProjAddrCombined', 'IssueDate', 'Detail'}
        if not required.issubset(d) or not re.fullmatch(r'\d{6,12}', d['PermitNo']):
            raise PermitError('Missing required report fields')
        if d['PU'] not in USES:
            excluded += 1
            continue
        # Deliberately omit contractor names, phone numbers, email and mailing addresses.
        p = {'permit_id': d['PermitNo'], 'parcel_id': pin_id(d['ParcelNo']),
             'name': d.get('ProjName') or d['PUDesc'] + ' — ' + d['ProjAddrCombined'],
             'address': d['ProjAddrCombined'], 'city': d.get('ProjCity', ''),
             'use_code': d['PU'], 'use_description': d['PUDesc'],
             'description': d['Detail'], 'status': d['STATUS'],
             'issued_date': source_date(d['IssueDate']),
             'closed_date': source_date(d.get('ClosedDt', '')),
             'void_date': source_date(d.get('VoidDate', '')),
             'clearance_sheet': d.get('ClrSht') or None,
             'source_url': PERMIT + '?' + urlencode({'PermitNo': d['PermitNo'], 'PopUp': '1'}),
             'report_url': REPORT}
        if not p['address'] or not p['description'] or not p['issued_date']:
            raise PermitError('Permit missing address, description or issue date')
        if p['permit_id'] in seen:
            if seen[p['permit_id']] != p:
                raise PermitError('Conflicting rows for one permit; no silent merge')
            continue
        seen[p['permit_id']] = p
        rows.append(p)
    return rows, {'source_rows': len(root), 'excluded_routine_or_other_use_rows': excluded}


def interior_point(rings):
    """Choose an interior scanline interval using even/odd fill, including holes.
    This is an approximate parcel point, never a building or road alignment.
    """
    if not isinstance(rings, list) or not rings:
        raise PermitError('Missing polygon rings')
    ys = set()
    for ring in rings:
        if len(ring) < 4 or ring[0] != ring[-1]:
            raise PermitError('Invalid polygon ring')
        for point in ring:
            if len(point) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in point):
                raise PermitError('Invalid polygon coordinate')
            x, y = point
            if not (29.3 <= y <= 30.4 and -81.8 <= x <= -81.0):
                raise PermitError('Parcel outside county coordinate bounds')
            ys.add(y)
    ys = sorted(ys)
    mids = [(a+b)/2 for a,b in zip(ys, ys[1:]) if a < b]
    if not mids:
        raise PermitError('Degenerate polygon')
    # Sample at most 33 scanlines, plus central latitude; bound large parcel work.
    samples = {mids[int(i*(len(mids)-1)/min(32, len(mids)-1))] for i in range(min(33, len(mids)))} if len(mids)>1 else set(mids)
    best = None
    for y in sorted(samples):
        xs = []
        for ring in rings:
            for (x1,y1),(x2,y2) in zip(ring, ring[1:]):
                if (y1 <= y < y2) or (y2 <= y < y1):
                    xs.append(x1 + (y-y1)*(x2-x1)/(y2-y1))
        xs.sort()
        if len(xs) % 2:
            raise PermitError('Invalid polygon intersections')
        for a,b in zip(xs[::2], xs[1::2]):
            if b > a and (best is None or b-a > best[0]):
                best = (b-a, (a+b)/2, y)
    if best is None:
        raise PermitError('No interior parcel point')
    return best[1], best[2]


def parcel_matches(data):
    if ('error' in data or data.get('exceededTransferLimit') or
        data.get('geometryType') != 'esriGeometryPolygon' or
        (data.get('spatialReference') or {}).get('wkid') != 4326 or
        not isinstance(data.get('features'), list)):
        raise PermitError('Parcel response incomplete or not WGS84 polygons')
    matches = {}
    for f in data['features']:
        a = f['attributes']
        pin = pin_id(a['pin'])
        entry = {'address': a.get('prp_addr') or '', 'rings': (f.get('geometry') or {}).get('rings')}
        if entry not in matches.setdefault(pin, []):
            matches[pin].append(entry)
    return matches


def qualify(permit, matches, parcel_url):
    p = dict(permit, publication_allowed=False, review_status='needs_review',
             coordinate_verification='unverified', lat=None, lng=None,
             corridor_matches=corridors(permit['address']),
             corridor_match_method='permit_site_address_text', parcel_source_url=parcel_url)
    features = matches.get(permit['parcel_id'], [])
    if len(features) != 1:
        p['location_review_reason'] = 'Parcel absent or multiple distinct geometries'
        return p
    feature = features[0]
    if not feature['address'] or address_key(feature['address']) != address_key(permit['address']):
        p['location_review_reason'] = 'Permit and parcel site addresses differ'
        return p
    try:
        lng, lat = interior_point(feature['rings'])
    except (PermitError, TypeError, ValueError) as exc:
        p['location_review_reason'] = 'Parcel geometry could not establish an interior point'
        return p
    p.update(lat=lat, lng=lng, coordinate_verification='exact_parcel_id_and_site_address_match',
             location_accuracy='approximate_parcel_interior_not_building_footprint',
             parcel_site_address=feature['address'], location_review_reason=None)
    return p


def map_project(p):
    return {'title': p['name'], 'type': 'Commercial Building Permit',
            'status': 'County permit: ' + p['status'] + '; construction activity not independently verified',
            'plannedYear': 'Not provided', 'latitude': p['lat'], 'longitude': p['lng'],
            'details': p['description'] + '\nSite: ' + p['address'] + '\nPermit use: ' + p['use_description'],
            'schedule': 'Permit issued: ' + p['issued_date'] + '; expected completion not provided',
            'projectId': 'SJC-BP-' + p['permit_id'], 'sourceURL': p['source_url'],
            'reviewOnly': True, 'publicationAllowed': False,
            'locationSourceKind': 'County parcel ID and site address matched; approximate parcel interior',
            'locationSourceRef': p['parcel_source_url'],
            'evidenceConfidence': 'official_permit_and_parcel_match',
            'coordinateVerification': p['coordinate_verification'], 'projectHorizon': 'unknown',
            'corridorMatches': p['corridor_matches']}



def merge_test_feed(base, additions):
    """Upsert by stable permit ID; preserve absent records and reject silent relocation."""
    if not isinstance(base, list) or not isinstance(additions, list):
        raise PermitError('Test feeds must be arrays')
    result, index = [], {}
    for p in base:
        key = p.get('projectId')
        if not isinstance(key, str) or not key or key in index:
            raise PermitError('Existing test feed has missing or duplicate IDs')
        if p.get('reviewOnly') is not True or p.get('publicationAllowed') is not False:
            raise PermitError('Only explicitly marked review feeds may be merged')
        index[key] = len(result)
        result.append(dict(p))
    seen = set()
    for p in additions:
        key = p.get('projectId')
        if (not isinstance(key, str) or not key.startswith('SJC-BP-') or key in seen
            or p.get('reviewOnly') is not True or p.get('publicationAllowed') is not False
            or p.get('coordinateVerification') != 'exact_parcel_id_and_site_address_match'):
            raise PermitError('New permit is not a qualified review record')
        for field, lower, upper in [('latitude',29.3,30.4),('longitude',-81.8,-81.0)]:
            v = p.get(field)
            if type(v) not in (int,float) or not math.isfinite(v) or not lower <= v <= upper:
                raise PermitError('Invalid new permit coordinate')
        seen.add(key)
        if key in index:
            previous = result[index[key]]
            if any(previous[k] != p[k] for k in ['latitude','longitude']):
                raise PermitError('Existing permit moved; review the coordinate change')
            result[index[key]] = dict(previous, **p)
        else:
            index[key] = len(result)
            result.append(dict(p))
    return result


def run(start, end, output, client=None, base_test_feed=None):
    output.mkdir(parents=True, exist_ok=True)
    client = client or Client()
    report = {'schema_version': 1, 'status': 'failed', 'publication_allowed': False,
              'retrieved_at': datetime.now(timezone.utc).isoformat(),
              'issue_date_window': [start.isoformat(), end.isoformat()],
              'coverage': 'County commercial issue-date report only; municipal completeness not established',
              'candidates': [], 'windows': []}
    projects, merged = [], []
    try:
        if start > end or (end-start).days > 366:
            raise PermitError('Date range must be ordered and no longer than 366 days')
        cursor, permits = start, {}
        while cursor <= end:
            stop = min(cursor + timedelta(days=30), end)
            rows, counts = parse_export(client.export(cursor, stop))
            report['windows'].append(dict(counts, start=cursor.isoformat(), end=stop.isoformat()))
            for p in rows:
                if not start.isoformat() <= p['issued_date'] <= end.isoformat():
                    raise PermitError('County returned a permit outside requested issue dates')
                if p['permit_id'] in permits and permits[p['permit_id']] != p:
                    raise PermitError('Conflicting permit across report windows')
                permits[p['permit_id']] = p
            if stop == end:
                break
            cursor = stop  # One-day overlap avoids undocumented exclusive date boundaries.
        pins = sorted({p['parcel_id'] for p in permits.values()})
        matches, links = {}, {}
        for offset in range(0, len(pins), 40):
            chunk = pins[offset:offset+40]
            data, url = client.parcels(chunk)
            found = parcel_matches(data)
            if set(found) - set(chunk):
                raise PermitError('Parcel query returned unexpected IDs')
            matches.update(found)
            links.update({pin: url for pin in chunk})
        for p in permits.values():
            row = qualify(p, matches, links[p['parcel_id']])
            report['candidates'].append(row)
            # Retain void/closed records with explicit status so lifecycle updates are not lost.
            if row['lat'] is not None and row['corridor_matches']:
                projects.append(map_project(row))
        if base_test_feed is not None:
            merged = merge_test_feed(json.loads(base_test_feed.read_text()), projects)
            report['merged_test_pin_count'] = len(merged)
        report.update(status='completed', permit_count=len(permits), corridor_test_pin_count=len(projects))
    except Exception as exc:
        report.update(error=str(exc) if isinstance(exc, PermitError) else type(exc).__name__, candidates=[])
        projects = []
    write(output/'sjc_permits.review.json', report)
    write(output/'sjc_permits.test.json', projects)
    if base_test_feed is not None:
        write(output/'sjc_permits.merged.test.json', merged if report['status']=='completed' else [])
    print('Commercial permits: ' + report['status'] + '; ' + str(len(projects)) + ' corridor test pins')
    return 0 if report['status']=='completed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=date.fromisoformat)
    parser.add_argument('--base-test-feed', type=Path, help='Preserve existing review pins and upsert permit IDs into a separate merged test file')
    parser.add_argument('--end', type=date.fromisoformat, default=datetime.now(timezone.utc).date())
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parents[1]/'research-output')
    args = parser.parse_args()
    raise SystemExit(run(args.start or args.end-timedelta(days=31), args.end, args.output_dir, base_test_feed=args.base_test_feed))
