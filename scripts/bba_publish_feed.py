#!/usr/bin/env python3
"""Publish a versioned beta feed from complete official GIS snapshots; no AI key.
Legacy/production projects.json is never a destination. Old pins are never deleted
or moved automatically. Unlocated records and news remain review artifacts.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from sjc_import import collect, normalize
from bba_location_correlation import apply_gate, constraints
from bba_county_discovery import session, request_json, geometry_candidate

ROOT = Path(__file__).resolve().parents[1]
SJC = 'FL-12109'
FLAGLER = 'FL-12035'
BOUNDARY = 'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query'
BOUNDS = {SJC: (29.3,30.4,-81.8,-81.0), FLAGLER:(29.15,29.8,-81.65,-80.95), 'FL-12019':(29.5,30.35,-82.2,-81.45)}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def https(raw):
    try:
        p=urlsplit(raw)
        return p.scheme=='https' and bool(p.hostname) and p.username is None and p.password is None and p.port in (None,443)
    except (ValueError,TypeError):return False


def validate(rows):
    if not rows or len(rows)>5000:raise ValueError('Unexpected feed size')
    seen=set()
    for r in rows:
        ident=r.get('projectId')
        if not isinstance(ident,str) or not ident.strip() or ident in seen:raise ValueError('Missing/duplicate record identity')
        seen.add(ident)
        if any(not isinstance(r.get(k),str) or not r[k].strip() for k in ('title','type','status','plannedYear','details','schedule','sourceURL')):
            raise ValueError('Incomplete app record: '+ident)
        if not https(r['sourceURL']) or r.get('reviewOnly') is not True or r.get('publicationAllowed') is not False:
            raise ValueError('Invalid beta provenance/flags: '+ident)
        county=r.get('countyID',SJC)
        if county not in BOUNDS:raise ValueError('Unsupported county')
        a,b,c,d=BOUNDS[county];lat,lng=r.get('latitude'),r.get('longitude')
        if not all(type(x) in (int,float) and math.isfinite(x) for x in (lat,lng)) or not(a<=lat<=b and c<=lng<=d):
            raise ValueError('Invalid/out-of-scope coordinate: '+ident)
        if r.get('locationPrecision')=='estimated':
            if not r.get('locationEstimateBasis','').strip() or not any(https(x) for x in r.get('locationEvidenceURLs',[])):
                raise ValueError('Estimate lacks evidence')
            datetime.fromisoformat(r['locationReviewedAt'].replace('Z','+00:00'))
        elif r.get('locationPrecision') not in (None,'verified'):raise ValueError('Unknown location precision')


def merge(previous, incoming):
    """Exact identities only. Keep all history; changed coordinates go to review."""
    validate(previous)
    out={r['projectId']:dict(r) for r in previous};held=[];added=0;updated=0;seen={}
    for r in incoming:
        validate([r]);key=r['projectId']
        if key in seen:
            if seen[key]!=r:raise ValueError('Conflicting incoming identity: '+key)
            continue
        seen[key]=r
        if key in out and (out[key]['latitude'],out[key]['longitude'])!=(r['latitude'],r['longitude']):
            held.append({'id':key,'reason':'Existing coordinate changed; location review required'})
            continue
        if key not in out:added+=1
        elif out[key]!=r:updated+=1
        out[key]=dict(r)
    rows=list(out.values());validate(rows)
    return rows,{'added':added,'updated':updated,'retained':len(previous),'withheld':held}


def base_record(ident,title,description,source,lat,lng,county,stamp,basis,urls):
    return dict(projectId=ident,title=title,type='Development Application',
        status='Official project listed; physical construction status not verified',plannedYear='Not provided',
        details=description or 'Official project area; detailed work scope not provided.',schedule='Work dates not verified',
        sourceURL=source,latitude=lat,longitude=lng,countyID=county,reviewOnly=True,publicationAllowed=False,
        locationPrecision='estimated',locationEstimateBasis=basis,locationEvidenceURLs=list(dict.fromkeys(urls)),
        locationReviewedAt=stamp,locationSourceKind='Estimated representative project area; not a surveyed footprint',
        coordinateVerification='official_project_area_estimate',evidenceConfidence='official_geometry_estimated_work_location',
        projectHorizon='unknown')


def county_boundary(s,county):
    data=request_json(s,BOUNDARY,{'where':"GEOID='%s'"%county.removeprefix('FL-'),'outFields':'GEOID','returnGeometry':'true','outSR':4326,'f':'json'})
    f=data.get('features',[])
    if len(f)!=1 or f[0]['attributes']['GEOID']!=county.removeprefix('FL-'):raise ValueError('County boundary identity mismatch')
    return f[0]['geometry']['rings']


def sjc_records(s,stamp):
    features=collect();candidates,projects,skipped=normalize(features)
    reviews,safe=apply_gate(candidates,projects)
    rings=county_boundary(s,SJC);out=[]
    for r in safe:
        p=geometry_candidate({'x':r['longitude'],'y':r['latitude']},rings)
        if not p:continue
        r.update(countyID=SJC,locationPrecision='estimated',locationEstimateBasis='Official county tracker point for this exact application, checked inside St Johns County. This locates the application area; the specific building or work footprint is not verified.',
            locationEvidenceURLs=[r['sourceURL'],r['locationSourceRef']],locationReviewedAt=stamp,
            evidenceConfidence='official_geometry_estimated_work_location',coordinateVerification='official_project_area_estimate')
        out.append(r)
    return out,{'fetched':len(features),'accepted':len(out),'locationReviews':reviews,'skipped':skipped}


def flagler_records(report,config,s,stamp):
    if report.get('county_id')!=FLAGLER:raise ValueError('Wrong county artifact')
    observed=datetime.fromisoformat(report['checked_at'].replace('Z','+00:00'))
    if abs((datetime.now(timezone.utc)-observed).total_seconds())>86400:raise ValueError('Stale discovery artifact')
    sources={x['id']:x for x in config['gis']}
    completed={x['source'] for x in report['coverage'] if x['status']=='complete_snapshot'}
    if not set(sources).issubset(completed):raise ValueError('Incomplete Flagler core sources')
    rings=county_boundary(s,FLAGLER);out=[];held=[];seen=set()
    for r in report['records']:
        if not r.get('in_current_snapshot',True):continue
        cfg=sources.get(r.get('record_type'))
        if not cfg:continue
        ident=r['id']
        if ident in seen:raise ValueError('Duplicate GIS identity')
        seen.add(ident)
        p=geometry_candidate(r.get('geometry_evidence'),rings)
        # Unsupported directional/subarea claims stay in review; never guess the side.
        if not p or constraints(r.get('description','')):
            held.append({'id':ident,'reason':'Geometry/county/description-side check needs review'});continue
        if not any(url.startswith(cfg['url']+'/query?') for url in r['source_urls']):
            raise ValueError('Geometry provenance mismatch')
        record=base_record(ident,r['title'],r['description'],cfg['map_url'],p['latitude'],p['longitude'],FLAGLER,stamp,
            'Official '+cfg['authority']+' geometry for this record, checked inside Flagler County. '+p['method']+'. The exact work footprint/phase is not verified.',r['source_urls'])
        # Agency disposition belongs to its application/phase, not physical construction completion.
        record['details']+='\nAgency record status (not confirmation of physical completion): '+str(r.get('status') or 'Not provided')
        out.append(record)
    return out,{'accepted':len(out),'withheld':held,'currentFeatures':len(seen)}


def write(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(payload,indent=2,ensure_ascii=False,allow_nan=False)+'\n');temp.replace(path)


def run(root,artifacts):
    stamp=datetime.now(timezone.utc).isoformat();feeds=root/'feeds';target=feeds/'beta.json'
    previous=json.loads(target.read_text())['projects'] if target.exists() else json.loads((feeds/'beta-seed.json').read_text())
    config=json.loads((root/'config/counties.json').read_text())['counties'];s=session()
    sjc,sjc_report=sjc_records(s,stamp)
    report=json.loads((artifacts/(FLAGLER+'.review.json')).read_text())
    flagler,flagler_report=flagler_records(report,next(c for c in config if c['id']==FLAGLER),s,stamp)
    # Validate the complete candidate before touching the current feed.
    merged,changes=merge(previous,sjc+flagler)
    if any(r.get('countyID',SJC) not in (SJC,FLAGLER) for r in merged):raise ValueError('Legacy two-county feed cannot contain Clay records')
    generated={'schemaVersion':1,'audience':'bba-beta','generatedAt':stamp,'revision':digest(merged),
        'counties':[SJC,FLAGLER],'projects':merged,
        'coverage':{'scope':'Official county/municipal project GIS areas; incomplete permit and work-date coverage. News/unlocated records remain review-only.',
                    'stJohnsAccepted':len(sjc),'flaglerAccepted':len(flagler)}}
    if target.exists():
        old=json.loads(target.read_text());write(feeds/'history'/(old['revision']+'.json'),old)
    write(feeds/'history'/(generated['revision']+'.json'),generated)
    write(feeds/'latest-publication.json',{'generatedAt':stamp,'revision':generated['revision'],'count':len(merged),'changes':changes,'stJohns':sjc_report,'flagler':flagler_report})
    write(target,generated)
    print(json.dumps({'published':len(merged),'revision':generated['revision'],'added':changes['added'],'updated':changes['updated'],'withheldMoves':len(changes['withheld'])}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifacts',type=Path,default=ROOT/'research-output/county-discovery');a=p.parse_args()
    run(ROOT,a.artifacts)
