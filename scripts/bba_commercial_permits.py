"""Repeatable commercial permit discovery; exact parcel/address gate before beta pins.

Recent 45 days plus one rotating historical month (24 months), and configured
bootstrap months. Report issue/close dates are never physical work dates.
"""
from datetime import date, datetime, timedelta, timezone
import calendar
import hashlib
import json
from pathlib import Path
from sjc_permits import Client, parse_export, parcel_matches, qualify, map_project

ROOT = Path(__file__).resolve().parents[1]


def windows(today):
    recent = today - timedelta(days=44)
    # Each historical month is revisited within 24 days, including status updates.
    index = today.year * 12 + today.month - 1 - (today.toordinal() % 24)
    year, month = divmod(index, 12); month += 1
    historical = (date(year, month, 1), min(today, date(year, month, calendar.monthrange(year, month)[1])))
    months = [(date(2025, 12, 1), date(2025, 12, 31)), historical]
    cursor = recent
    while cursor <= today:
        stop = min(today, cursor + timedelta(days=30)); months.append((cursor, stop)); cursor = stop + timedelta(days=1)
    return sorted(set(months))


def run(root=ROOT, client=None, today=None):
    today = today or date.today(); client = client or Client()
    stamp = datetime.now(timezone.utc).isoformat()
    report = dict(checkedAt=stamp, status='completed', windows=[], candidates=[], projects=[], errors=[],
                  coverage='Commercial issue-date reports: recent 45 days, rotating 24-month history and December 2025 bootstrap. Not complete permit coverage.')
    permits = {}
    for start, end in windows(today):
        try:
            raw = client.export(start, end); rows, counts = parse_export(raw)
            for row in rows:
                if not start.isoformat() <= row['issued_date'] <= end.isoformat(): raise ValueError('Issue date outside requested window')
                key = row['permit_id']
                if key in permits and permits[key] != row: raise ValueError('Conflicting permit identity')
                permits[key] = row
            report['windows'].append(dict(start=str(start), end=str(end), sourceSHA256=hashlib.sha256(raw).hexdigest(), **counts))
        except Exception as exc:
            report['status'] = 'partial'; report['errors'].append(dict(start=str(start), end=str(end), error=type(exc).__name__))
    pins = sorted({p['parcel_id'] for p in permits.values()}); matches = {}; urls = {}
    for offset in range(0, len(pins), 40):
        batch = pins[offset:offset+40]
        try:
            data, url = client.parcels(batch); found = parcel_matches(data)
            if set(found) - set(batch): raise ValueError('Unexpected parcel identity')
            matches.update(found); urls.update({p: url for p in batch})
        except Exception as exc:
            report['status'] = 'partial'; report['errors'].append(dict(parcels=batch, error=type(exc).__name__))
    for permit in permits.values():
        candidate = qualify(permit, matches, urls.get(permit['parcel_id'], ''))
        report['candidates'].append(candidate)
        if candidate['lat'] is None: continue
        row = map_project(candidate)
        row.update(countyID='FL-12109', locationPrecision='estimated', locationReviewedAt=stamp,
                   locationEstimateBasis='Exact county permit parcel ID and site address match a single parcel geometry. Estimated parcel interior, not the building footprint.',
                   locationEvidenceURLs=[row['sourceURL']], applicationIssuedDate=permit['issued_date'],
                   dateCheckedAt=stamp, dateCheckStatus='County permit record checked; physical work timing unverified')
        # No construction year inferred from issuance, permit closure or the word Open.
        row['schedule'] = 'Construction start and completion dates not verified. Permit issued ' + permit['issued_date'] + '.'
        row['evidenceEvents'] = [dict(date=permit['issued_date'], kind='Permit issued',
             summary='County commercial building permit issued; current permit status: ' + permit['status'] + '. This is not an opening date.',
             sourceURL=permit['source_url'], checkedAt=stamp)]
        report['projects'].append(row)
    out = Path(root)/'research-output'; out.mkdir(parents=True, exist_ok=True)
    (out/'commercial-permits.review.json').write_text(json.dumps(report, indent=2)+'\n')
    print('Commercial permits:', report['status'], len(permits), 'records;', len(report['projects']), 'located candidates')
    return report


if __name__ == '__main__':
    result = run()
    raise SystemExit(0 if result['status']=='completed' else 1)
