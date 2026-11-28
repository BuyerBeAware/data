#!/usr/bin/env python3
"""Publish the three-county beta v2 feed; never change the build-19 v1 contract."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
from urllib.parse import urlencode

import bba_publish_feed as core
from bba_county_discovery import session,geometry_candidate
from bba_location_correlation import constraints
from bba_clay_enrichment import enrich

ROOT=Path(__file__).resolve().parents[1]
CLAY='FL-12019'


def clay_records(report,county,s,stamp):
    if report.get('county_id')!=CLAY:raise ValueError('Wrong Clay artifact')
    observed=datetime.fromisoformat(report['checked_at'].replace('Z','+00:00'))
    if abs((datetime.now(timezone.utc)-observed).total_seconds())>86400:raise ValueError('Stale Clay artifact')
    configs={x['id']:x for x in county['gis']}
    complete={x['source'] for x in report['coverage'] if x['status']=='complete_snapshot'}
    if not set(configs).issubset(complete):raise ValueError('Clay core GIS snapshot incomplete')
    rings=core.county_boundary(s,CLAY);records=[];held=[]
    for row in report['records']:
        if not row.get('in_current_snapshot',True):continue
        cfg=configs.get(row.get('record_type'))
        if not cfg:continue
        point=geometry_candidate(row.get('geometry_evidence'),rings)
        if not point or constraints(row.get('description','')):
            held.append({'id':row['id'],'reason':'Missing/out-of-county geometry or unsupported directional claim'});continue
        if not any(u.startswith(cfg['url']+'/query?') for u in row['source_urls']):raise ValueError('Clay source geometry mismatch')
        fields=row.get('official_fields',{})
        if cfg['id']=='clay-zoning-ordinances':
            title='Clay zoning record: '+row['title']
            description=('County GIS zoning amendment record '+row['record_number']+'. Ordinance '+str(fields.get('ORDINANCE_2') or 'not provided')+'. Zoning change: '+str(fields.get('AMMENDED') or 'not provided')+'. An ordinance/application date is not a work schedule. Construction status, development footprint and project name beyond this case ID have not been verified.')
            source=cfg['map_url']+'&'+urlencode({'center':str(point['longitude'])+','+str(point['latitude']),'level':16})
            typ='Rezoning Application'
        else:
            title=row['title'];source=cfg['map_url'];typ='Road Project'
            description='Clay County Bonded Transportation Program public alignment: '+str(row.get('description') or row['title'])+'. Project reference '+str(fields.get('ProjectNo') or row['record_number'])+'. The sourced line identifies a representative project area; construction status and dates have not been verified.'
        record=core.base_record(row['id'],title,description,source,point['latitude'],point['longitude'],CLAY,stamp,
            cfg['authority']+' published geometry for this exact record, checked inside Clay County. '+point['method']+'. This is an estimated project area, not a surveyed work footprint.',[source]+row['source_urls'])
        record['type']=typ
        record['status']='County zoning record; construction status not verified' if typ=='Rezoning Application' else 'Public road alignment; construction status not verified'
        record['locationSourceRef']=next(u for u in row['source_urls'] if u.startswith(cfg['url']+'/query?'))
        records.append(record)
    if not records:raise ValueError('No qualified Clay records; preserve previous v2 feed')
    return records,{'accepted':len(records),'withheld':held,'scope':'Current/prior two ordinance years plus public bonded road project alignments; not full permit/pending case coverage'}


def run(root,artifacts):
    stamp=datetime.now(timezone.utc).isoformat();feeds=root/'feeds';target=feeds/'beta-v2.json'
    legacy=json.loads((feeds/'beta.json').read_text())
    if set(legacy['counties'])!={core.SJC,core.FLAGLER}:raise ValueError('Legacy feed contract changed')
    previous=json.loads(target.read_text())['projects'] if target.exists() else legacy['projects']
    # Retain every previously published identity and coordinate across both streams.
    baseline,base_changes=core.merge(previous,legacy['projects'])
    county=next(c for c in json.loads((root/'config/counties.json').read_text())['counties'] if c['id']==CLAY)
    report=json.loads((artifacts/(CLAY+'.review.json')).read_text())
    incoming,diagnostics=clay_records(report,county,session(),stamp)
    # Enrich exact identities from official documents and exact parcels before publication.
    for document in county.get('project_documents',[]):
        matches=[i for i,r in enumerate(incoming) if r['projectId']==document['recordID']]
        if len(matches)!=1:raise ValueError('Configured document identity not in current snapshot')
        incoming[matches[0]]=enrich(incoming[matches[0]],document,session(),stamp)
    merged,changes=core.merge(baseline,incoming)
    research=legacy.get('researchProjects',[])
    output={'schemaVersion':1,'audience':'bba-beta','generatedAt':stamp,'revision':core.digest({'projects':merged,'researchProjects':research} if research else merged),
        'counties':[core.SJC,core.FLAGLER,CLAY],'projects':merged,'researchProjects':legacy.get('researchProjects',[]),
        'coverage':{'scope':'Three-county beta v2; incomplete permit/work-date coverage. Unsupported evidence remains in review.','clayAccepted':len(incoming)}}
    if len(json.dumps(output,ensure_ascii=False).encode())>8_000_000:raise ValueError('Client feed byte limit exceeded')
    if target.exists():
        old=json.loads(target.read_text());core.write(feeds/'history-v2'/(old['revision']+'.json'),old)
    core.write(feeds/'history-v2'/(output['revision']+'.json'),output)
    core.write(feeds/'latest-publication-v2.json',{'generatedAt':stamp,'count':len(merged),'revision':output['revision'],'changes':changes,'legacyMerge':base_changes,'clay':diagnostics})
    core.write(target,output)
    print(json.dumps({'published':len(merged),'clay':len(incoming),'revision':output['revision'],'withheld':len(diagnostics['withheld'])+len(changes['withheld'])}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifacts',type=Path,default=ROOT/'research-output/county-discovery');a=p.parse_args();run(ROOT,a.artifacts)
