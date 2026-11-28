"""Checked, attributed events; application history never becomes a work schedule.

Registry is an explicit reviewed identity join, not a fuzzy name match. Missing or
changed source anchors withhold the event. News and community search results remain
leads until reviewed; they cannot change approval, geometry or construction dates.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
HOSTS = {'www.sjcfl.us','sjcfl.us','stjohnsclerk.com','www.actionnewsjax.com'}


def normalized(text):
    return ' '.join(re.sub(r'[^a-z0-9]', ' ', text.lower()).split())


def read_source(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None,443):
        raise ValueError('Unapproved evidence source')
    response = requests.get(url, timeout=(12,45), allow_redirects=False)
    response.raise_for_status()
    if response.is_redirect: raise ValueError('Evidence source redirected; review required')
    raw = response.content
    if len(raw)>12_000_000: raise ValueError('Evidence source too large')
    if raw.startswith(b'%PDF'):
        text = ' '.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(raw)).pages)
    else:
        soup = BeautifulSoup(raw,'html.parser')
        for tag in soup(['script','style','nav','footer']): tag.decompose()
        text = soup.get_text(' ',strip=True)
    return text, hashlib.sha256(raw).hexdigest()


def collect(root=ROOT, reader=read_source):
    root = Path(root); stamp = datetime.now(timezone.utc).isoformat()
    registry = json.loads((root/'config/project-evidence.json').read_text())
    report = dict(checkedAt=stamp, projects=[], errors=[], publicationAllowed=False)
    cache = {}
    for entry in registry['projects']:
        result = dict(entry, events=[])
        for event in entry['events']:
            url = event['sourceURL']
            try:
                if url not in cache: cache[url] = reader(url)
                text, digest = cache[url]
                if not all(normalized(term) in normalized(text) for term in event['requiredTerms']):
                    raise ValueError('Source identity/decision anchors changed')
                result['events'].append({k:v for k,v in event.items() if k!='requiredTerms'} |
                                        dict(checkedAt=stamp, sourceSHA256=digest))
            except Exception as exc:
                report['errors'].append(dict(sourceURL=url, applicationID=event['applicationID'], error=type(exc).__name__))
        report['projects'].append(result)
    out=root/'research-output';out.mkdir(parents=True,exist_ok=True)
    (out/'project-evidence.review.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def enrich(rows, report):
    out=[]
    for row in rows:
        r=dict(row)
        for entry in report['projects']:
            if r['projectId'] not in entry['projectIDs']: continue
            if not entry['events']: continue
            r.update(evidenceEvents=entry['events'],projectAliases=entry['aliases'],
                     relatedApplicationIDs=entry['relatedApplicationIDs'],researchContext=entry['context'])
            # Keep the exact application's status separate from related history.
            if r['projectId']=='COMPAMD2024000005' and any(e['applicationID']==r['projectId'] for e in entry['events']):
                r['status']='Original FLUM amendment: PZA recommended denial of transmittal April 3, 2025; final disposition not verified here'
            if r['projectId']=='MAJMOD2025000007' and any(e['applicationID']==r['projectId'] and e['kind']=='Related application decision' for e in entry['events']):
                r['status']='County commission denied this application November 4, 2025; subsequent court outcome not verified'
        out.append(r)
    return out


if __name__=='__main__':
    result=collect()
    print('Checked project history:',sum(len(p['events']) for p in result['projects']),'events;',len(result['errors']),'unavailable sources')
