#!/usr/bin/env python3
"""Evidence-gap audit and exact-identity enrichment queue. Never writes map feeds."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
CASE=re.compile(r'\b(?:ZON|PUD|COMP)\s*[- ]?\d{2}-\d{4}\b',re.I)
DOMAINS={'FL-12109':['webapp.sjcfl.us','sjcfl.us','nflroads.com'],
 'FL-12035':['palmcoast.gov','flaglercounty.gov','nflroads.com'],
 'FL-12019':['claycounty.novusagenda.com','claycountygov.com','clayutility.org','nflroads.com']}

def gaps(row):
    issues=[]
    if row.get('coordinateVerification') not in {'exact_parcel_id_and_site_address_match','official_county_point_inside_matching_project_boundary','exact_case_document_parcel_contains_existing_point'}:issues.append('exact_case_parcel_or_boundary_not_verified')
    if row.get('constructionStatus') in (None,'','unknown'):issues.append('physical_work_status_not_verified')
    if not any(re.search(r'\b20\d{2}\b',str(row.get(k) or '')) for k in ('projectedStartDate','projectedCompletionDate')) and str(row.get('plannedYear','')).lower() in ('','not provided','unknown'):issues.append('physical_work_dates_not_published_or_not_found')
    if not row.get('recordStage'):issues.append('application_stage_not_structured')
    return issues

def audit(projects):
    tasks=[];counts=Counter();counties=Counter();identities={}
    for row in projects:
        key=row['projectId'];county=row.get('countyID','FL-12109');counties[county]+=1
        issues=gaps(row);counts.update(issues)
        case=CASE.search(row['title']);identity=case.group(0) if case else (key.split(':')[-2] if key.startswith('FL-') and len(key.split(':'))>=4 else key)
        identity=re.sub(r'\s+',' ',identity).strip()
        identities.setdefault((county,identity),[]).append(key)
        domains=DOMAINS.get(county,[])
        queries=[f'site:{d} "{identity}" "{row["title"]}" {term}' for term in ['staff report parcel site plan','construction schedule opening completion'] for d in domains[:2]]
        tasks.append({'recordID':key,'countyID':county,'exactIdentity':identity,'gaps':issues,
            'priority':(40 if 'exact_case_parcel_or_boundary_not_verified' in issues else 0)+(30 if 'physical_work_status_not_verified' in issues else 0)+(20 if 'physical_work_dates_not_published_or_not_found' in issues else 0),
            'queries':queries,'requiredEvidence':['Match county plus exact case/permit/FDOT ID','Resolve exact parcel or project boundary; check existing point is inside','Separate filing/hearing/approval/funding dates from physical work dates','Require project-specific source passage; reject boilerplate, drafts, recommendations and conflicting claims'],
            'publicationAllowed':False})
    return {'generatedAt':datetime.now(timezone.utc).isoformat(),'records':len(projects),'counties':dict(counties),'gapCounts':dict(counts),
        'policy':'Enrich existing identities before expansion. An unknown schedule cannot be manufactured from approvals, land-use designations or population projections.',
        'relatedRecordGroups':[{'countyID':c,'identity':i,'recordIDs':ids,'automaticMerge':False} for (c,i),ids in identities.items() if len(ids)>1],
        'queue':sorted(tasks,key=lambda t:(-t['priority'],t['countyID'],t['recordID']))}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--feed',type=Path,default=ROOT/'feeds/beta-v2.json');p.add_argument('--out',type=Path,default=ROOT/'research-output/project-quality.json');a=p.parse_args()
    report=audit(json.loads(a.feed.read_text())['projects']);a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('queue','relatedRecordGroups')}))
