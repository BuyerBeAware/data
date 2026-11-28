"""Bounded public news/context leads using exact case IDs, names and aliases.
No model, paid key, pin, status or date inference. Unreviewed leads are separate.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
import requests

ROOT=Path(__file__).resolve().parents[1]


def queries(rows, registry, limit=12):
    tasks=[];seen=set()
    for item in registry['projects']:
        for alias in item['aliases']:
            query='"'+alias+'" Florida development approval construction'
            if query not in seen:tasks.append(dict(projectIDs=item['projectIDs'],query=query));seen.add(query)
    # Prioritize a deterministic daily slice; do not search every project on every run.
    eligible=sorted([r for r in rows if r.get('projectHorizon')=='unknown' and len(r.get('title',''))>5],key=lambda r:r['projectId'])
    if eligible:
        start=(datetime.now(timezone.utc).date().toordinal()*max(1,limit-len(tasks)))%len(eligible)
        for r in (eligible[start:]+eligible[:start])[:max(0,limit-len(tasks))]:
            tasks.append(dict(projectIDs=[r['projectId']],query='"'+r['title']+'" Florida project permit planning'))
    return tasks[:limit]


def run(root=ROOT, get=requests.get):
    root=Path(root);feed=json.loads((root/'feeds/beta-v2.json').read_text())
    registry=json.loads((root/'config/project-evidence.json').read_text())
    report=dict(checkedAt=datetime.now(timezone.utc).isoformat(),reviewOnly=True,publicationAllowed=False,leads=[],queries=[],errors=[])
    for task in queries(feed['projects'],registry):
        url='https://news.google.com/rss/search?'+urlencode(dict(q=task['query'],hl='en-US',gl='US',ceid='US:en'))
        report['queries'].append(task)
        try:
            response=get(url,timeout=(12,25));response.raise_for_status()
            if len(response.content)>2_000_000:raise ValueError('RSS response too large')
            for item in ET.fromstring(response.content).findall('.//item')[:8]:
                report['leads'].append(dict(projectIDs=task['projectIDs'],title=item.findtext('title',''),url=item.findtext('link',''),
                    publishedAt=item.findtext('pubDate',''),publisher=item.findtext('source',''),sourceKind='Unreviewed news/context lead',
                    independentCorroborationVerified=False))
        except Exception as exc:report['errors'].append(dict(query=task['query'],error=type(exc).__name__))
    out=root/'research-output';out.mkdir(parents=True,exist_ok=True)
    (out/'project-followup.review.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Project follow-up:',len(report['queries']),'queries;',len(report['leads']),'unreviewed leads;',len(report['errors']),'source failures')
    return report


if __name__=='__main__':run()
