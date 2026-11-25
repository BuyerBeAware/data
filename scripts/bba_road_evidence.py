#!/usr/bin/env python3
"""Source-specific road dates and exact agency identity reconciliation; review only.
A project schedule is not proof of physical activity. Never writes map feeds.
"""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit,parse_qs
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
COUNTIES={'clay','stjohns','flagler'}

def financial_id(value):
    if not isinstance(value,str):return None
    # Preserve segment, phase and sequence. Never match by the six-digit parent alone.
    value=value.strip()
    if re.fullmatch(r'\d{7}|\d{9}|\d{11}',value):
        value=value[:6]+'-'+value[6:7]+('' if len(value)==7 else '-'+value[7:9])+('' if len(value)<11 else '-'+value[9:11])
    return value if re.fullmatch(r'\d{6}-\d(?:-\d{2}){0,2}',value) else None

def date_fact(value,event,source,phase):
    if not value or str(value).strip().lower() in {'na','n/a','tbd','none','unknown','not provided'}:return None
    text=str(value).strip()
    if re.search(r'\bfy\b|fiscal|funding|budget',text,re.I):return None
    years=[int(y) for y in re.findall(r'\b20\d{2}\b',text)]
    if not years or len(years)>2 or years!=sorted(years):return None
    if len(years)==2 and (years[1]-years[0]>10 or not re.search(r'[-/–—]|\bto\b|\bthrough\b',text)):return None
    exact=bool(re.fullmatch(r'(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}',text,re.I))
    if exact:
        try:datetime.strptime(text,'%B %d, %Y')
        except ValueError:return None
    return {'event':event,'sourceValue':text,'years':list(range(years[0],years[-1]+1)),
        'precision':'year_window' if len(set(years))>1 else 'calendar_date' if exact else 'approximate_period' if re.search(r'early|mid|late|spring|summer|fall|winter',text,re.I) else 'year',
        'certainty':'published_schedule_subject_to_change','sourcePhase':phase,
        'sourceURL':source,'isActualActivityVerified':False}

def reconcile(projects,map_records):
    result=[];all_ids=Counter()
    for p in projects:
        counties={re.sub(r'[^a-z]','',c.lower()) for c in re.split(r'[,/]',p.get('county') or '')}
        if not counties & COUNTIES:continue
        raw=p.get('source_project_number') or ''
        identifiers=[]
        for value in raw.split(','):
            ident=financial_id(value)
            if ident and ident not in identifiers:identifiers.append(ident)
        for ident in identifiers:all_ids[ident]+=1
        phase=p.get('phase') or 'Not provided';source=p['source_url'];schedule=p.get('schedule') or {}
        facts=[f for f in (date_fact(schedule.get('start'),'project_start',source,phase),date_fact(schedule.get('completion'),'project_expected_completion',source,phase)) if f]
        for fact in facts:
            key='start' if fact['event']=='project_start' else 'completion'
            fact['sourceField']=(p.get('schedule_source_fields') or {}).get(key,'Not captured')
            fact['sourceCheckedAt']=p.get('source_checked_at')
            fact['sourceSHA256']=p.get('source_sha256')
            if fact['sourceField']=='Construction Start':fact['event']='construction_scheduled_start'
            elif fact['sourceField']=='Construction End':fact['event']='construction_expected_completion'
        conflicts=[]
        if len(facts)==2 and facts[0]['years'][0]>facts[1]['years'][-1]:conflicts.append('Published start follows expected completion')
        if len(identifiers)>1:conflicts.append('Multiple financial IDs; schedule is not automatically assigned to each phase')
        matched=[];related=[]
        for row in map_records:
            # Only explicit FDOT identity fields qualify; never infer one from a county case number or road name.
            values=row.get('fdotFinancialProjectIDs') or []
            candidates=[financial_id(x) for x in values if isinstance(x,str)]
            if identifiers and set(identifiers)&set(candidates):matched.append(row['projectId'])
            elif any(x and y and x.split('-')[:2]==y.split('-')[:2] for x in identifiers for y in candidates):related.append(row['projectId'])
        result.append({'recordID':p['id'],'title':p['title'],'counties':p.get('county'),'sourceURL':source,
            'sourceCheckedAt':p.get('source_checked_at'),'sourceSHA256':p.get('source_sha256'),
            'sourceProjectNumber':raw or None,'financialProjectIDs':identifiers,'sourcePhase':phase,'dateEvidence':facts,
            'sourceSchedule':schedule,'exactMapMatches':matched,'relatedSegmentNeedsPhaseCheck':related,
            'conflicts':conflicts,'dateFilterEligible':False,'officialLinkedMaps':p.get('linked_maps',[]),
            'nextAction':'Resolve official financial ID' if not identifiers else 'Resolve multi-ID/phase schedule' if conflicts else 'Verify exact project geometry and construction phase',
            'publicationAllowed':False,'latitude':None,'longitude':None})
    for row in result:
        if any(all_ids[i]>1 for i in row['financialProjectIDs']):row['conflicts'].append('Financial ID appears on multiple source pages; preserve separate source records')
    return {'schemaVersion':1,'generatedAt':datetime.now(timezone.utc).isoformat(),'records':result,
        'summary':{'roadRecords':len(result),'withScheduleEvidence':sum(bool(r['dateEvidence']) for r in result),
            'withFinancialIdentity':sum(bool(r['financialProjectIDs']) for r in result),'withConflicts':sum(bool(r['conflicts']) for r in result),
            'exactMapMatches':sum(bool(r['exactMapMatches']) for r in result)},
        'policy':'Project-start/expected-completion evidence is separate from filing, approval and funding. Exact agency/project/phase and location verification required before map changes. No dates or coordinates inferred from road names.'}

def inspect_map(url,read_json):
    parts=urlsplit(url)
    if parts.scheme!='https' or parts.username or parts.password or parts.port not in (None,443):raise ValueError('Invalid public map URL')
    if parts.hostname=='experience.arcgis.com':item=parts.path.rstrip('/').split('/')[-1]
    elif (parts.hostname or '').endswith('.maps.arcgis.com'):item=parse_qs(parts.query).get('id',[''])[0]
    else:raise ValueError('Untrusted linked map host')
    if not re.fullmatch('[a-f0-9]{32}',item):raise ValueError('Invalid map item identity')
    base='https://www.arcgis.com/sharing/rest/content/items/'
    data=read_json(base+item+'/data?f=json')
    maps=[]
    if data.get('map',{}).get('itemId'):maps.append(data['map']['itemId'])
    for source in data.get('dataSources',{}).values():
        if source.get('type')=='WEB_MAP' and source.get('itemId'):maps.append(source['itemId'])
    if not maps and 'operationalLayers' in data:maps=[item]
    maps=list(dict.fromkeys(maps))
    if len(maps)>8:raise ValueError('Map inspection budget exceeded')
    types=[]
    for mid in maps:
        if not re.fullmatch('[a-f0-9]{32}',mid):raise ValueError('Invalid child map identity')
        layers=data if mid==item else read_json(base+mid+'/data?f=json')
        for layer in layers.get('operationalLayers',[]):types.append(layer.get('layerType','Unknown'))
    status='vector_tiles_only' if types and all(t=='VectorTileLayer' for t in types) else 'feature_or_mixed_layers_require_exact_project_query' if types else 'no_queryable_alignment_identified'
    return {'officialLinkedURL':url,'mapItemID':item,'childMapIDs':maps,'layerTypes':types,'alignmentStatus':status,'publicationAllowed':False}


def read_json(url):
    with urlopen(Request(url,headers={'User-Agent':'BuyerBeAware evidence review'}),timeout=25) as response:data=response.read(3_000_001)
    if len(data)>3_000_000:raise ValueError('Public map response exceeded limit')
    result=json.loads(data)
    if 'error' in result:raise ValueError('Public map item unavailable')
    return result


def markdown(report):
    def safe(value):return str(value or 'Not stated').replace('\\','\\\\').replace('|','\\|').replace('\n',' ').replace('[','\\[').replace(']','\\]').replace('<','&lt;').replace('>','&gt;')
    lines=['# BBA road evidence review','', 'Generated: '+report['generatedAt'],'',
      'Source-specific project schedules are subject to change. These are review records; construction activity and map locations are not established by this report. Funding/approval years are not work dates.', '',
      '| Project and official source | Published start | Expected completion | Financial IDs | Verification still needed |',
      '| --- | --- | --- | --- | --- |']
    for row in report['records']:
        notes=row['conflicts'] or [row['nextAction']]
        types=[m['alignmentStatus'] for m in row.get('mapEvidence',[])]
        if types:notes=notes+types
        lines.append('| ['+safe(row['title'])+']('+row['sourceURL']+') | '+safe(row['sourceSchedule'].get('start'))+' | '+safe(row['sourceSchedule'].get('completion'))+' | '+safe(', '.join(row['financialProjectIDs']))+' | '+safe('; '.join(notes))+' |')
    return '\n'.join(lines)+'\n'


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--refresh',action='store_true');p.add_argument('--inspect-maps',action='store_true');p.add_argument('--roads',type=Path,default=ROOT/'research-output/road_projects.review.json');p.add_argument('--feed',type=Path,default=ROOT/'feeds/beta-v2.json');p.add_argument('--out',type=Path,default=ROOT/'research-output/road-evidence.review.json');a=p.parse_args()
    if a.refresh:
        from bba_road_sources import run
        report=run(ROOT,max_details=150,county_scope=COUNTIES,snapshot_dir=ROOT/'research-output/road-source-pages')
    else:report=json.loads(a.roads.read_text())
    if report.get('status')!='completed':raise ValueError('Incomplete official road scan; retain previous evidence report')
    output=reconcile(report['projects'],json.loads(a.feed.read_text())['projects'])
    if a.inspect_maps:
        for row in output['records']:
            row['mapEvidence']=[]
            for url in row['officialLinkedMaps'][:3]:
                try:row['mapEvidence'].append(inspect_map(url,read_json))
                except Exception as exc:row['mapEvidence'].append({'officialLinkedURL':url,'alignmentStatus':'inspection_failed','errorType':type(exc).__name__,'publicationAllowed':False})
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(output,indent=2)+'\n');a.out.with_suffix('.md').write_text(markdown(output));print(json.dumps(output['summary']))
