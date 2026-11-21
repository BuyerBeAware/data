#!/usr/bin/env python3
"""Official long-range plan evidence and population-driven research leads.
Never produces map pins or claims that growth forecasts establish a project.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

POPULATION = 'https://www.edr.state.fl.us/Content/population-demographics/data/MediumProjections_2025.pdf'
SOURCES = [
    ('sjc-comp-plan', 'St. Johns County comprehensive plan', 'St. Johns County', 'https://www.sjcfl.us/comprehensive-plan/', 2050, 'Land use, transportation, housing and capital-improvement policies; not individual construction commitments.'),
    ('nftpo-lrtp', 'North Florida long-range transportation plan', 'Northeast Florida', 'https://northfloridatpo.com/planning/lrtp', 2050, 'Needs and cost-feasible project plans, including phased funding windows. A plan horizon is not a project start date.'),
    ('nftpo-tip', 'Transportation Improvement Program', 'Northeast Florida', 'https://northfloridatpo.com/planning/tip', None, 'Rolling five-year transportation program. Distinguish design, right-of-way and construction phases.'),
    ('nftpo-model', 'Regional growth and travel modeling', 'Northeast Florida', 'https://northfloridatpo.com/planning/modeling', 2050, 'Population, land use and travel assumptions used for regional planning; not parcel-level predictions.'),
    ('fdot-ftp', 'Florida Transportation Plan', 'Florida statewide', 'https://www.fdot.gov/planning/ftp/default.shtm', 2055, 'Statewide long-range framework. The source page says final 2055 documentation awaits approval; do not label the framework an approved construction program.'),
    ('edr-population', 'Official county population projections', 'All Florida counties', 'https://www.edr.state.fl.us/Content/population-demographics/data/index-floridaproducts.cfm', 2050, 'Official projections inform where to investigate. County growth does not identify an individual project or parcel.'),
]
ALLOWED = {'www.sjcfl.us', 'sjcfl.us', 'northfloridatpo.com', 'cdn.northfloridatpo.com',
           'www.fdot.gov', 'fdot.gov', 'fdotwww.blob.core.windows.net',
           'www.edr.state.fl.us', 'edr.state.fl.us', 'bebr.ufl.edu'}
TERMS = re.compile(r'land.?use|transportation|capital|improvement|needs|cost.feasible|priority|205\d|206\d|population|projection', re.I)
CORRIDORS = {'CR 210': re.compile(r'\b(?:CR|County Road)\s*[- ]?210\b', re.I),
             'US-1': re.compile(r'\b(?:US|U\.S\.)\s*[- ]?1\b', re.I)}


def canonical(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in ALLOWED or p.username or p.password or p.port not in (None,443):
        raise ValueError('Unapproved research source')
    return urlunsplit((p.scheme,p.netloc,p.path,p.query,''))


def read(url):
    url = canonical(url)
    # Validate every redirect before following; no credentials or private endpoints.
    session = requests.Session()
    for _ in range(5):
        with session.get(url, headers={'User-Agent':'BuyerBeAware long-range research'}, timeout=30,
                         stream=True, allow_redirects=False) as response:
            if response.is_redirect:
                url = canonical(urljoin(url,response.headers['Location']))
                continue
            response.raise_for_status()
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 40_000_000: raise ValueError('Research document exceeds size budget')
                chunks.append(chunk)
            return b''.join(chunks), response.headers.get('Content-Type',''), url
    raise ValueError('Too many redirects')


def document(url, getter=read):
    body, kind, final = getter(url)
    if 'pdf' in kind or body.startswith(b'%PDF'):
        pdf=PdfReader(io.BytesIO(body))
        if len(pdf.pages)>800: raise ValueError('PDF page budget exceeded; manual follow-up required')
        pages=[p.extract_text() or '' for p in pdf.pages]
        full_text='\n'.join(pages)
        if len(full_text)>2_000_000: raise ValueError('PDF text budget exceeded; manual follow-up required')
        return {'url':url,'resolved_url':final,'text':full_text,
                'page_count':len(pages),'links':[],'sha256':hashlib.sha256(body).hexdigest()}
    if 'html' not in kind: raise ValueError('Unsupported source content type')
    soup=BeautifulSoup(body,'html.parser');links={}
    for a in soup.find_all('a',href=True):
        title=a.get_text(' ',strip=True)
        try: link=canonical(urljoin(final,a['href']))
        except ValueError:continue
        if TERMS.search(title+' '+link) and ('.pdf' in urlsplit(link).path.lower()):links[link]=title
    for tag in soup(['script','style','nav','footer']):tag.decompose()
    return {'url':url,'resolved_url':final,'text':' '.join(soup.stripped_strings)[:150000],
            'links':[{'url':u,'title':t} for u,t in sorted(links.items())],
            'sha256':hashlib.sha256(body).hexdigest()}


def population_rows(text, source_url=POPULATION):
    base_match=re.search(r'Estimates(?:\s+for)?\s+(20[0-9]{2})',text)
    base=int(base_match[1]) if base_match else 2025
    header=re.search(r'County,\s*(20[0-9]{2})\s*[–-]\s*(20[0-9]{2})',text)
    years=list(range(int(header[1]),int(header[2])+1,5)) if header else [2030,2035,2040,2045,2050]
    if len(years)!=5 or base>=years[0]:raise ValueError('Population projection columns changed')
    if not all(str(x) in text for x in [base]+years):
        raise ValueError('Population table vintage/columns changed')
    result=[];seen=set()
    for line in text.splitlines():
        match=re.fullmatch(r'\s*([A-Za-z .-]+?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*',line)
        if not match or match[1].strip().upper()=='FLORIDA':continue
        name=match[1].strip();values=[int(match[i].replace(',','')) for i in range(2,8)]
        if name in seen or any(v<=0 for v in values):raise ValueError('Invalid/duplicate county population row')
        seen.add(name)
        result.append({'county':name,'base_year':base,'base_population':values[0],'projection_horizon_year':years[-1],
                       'official_projection':dict(zip(map(str,years),values[1:])),
                       'growth_to_horizon_percent':round((values[-1]/values[0]-1)*100,2),
                       'additional_residents_to_horizon':values[-1]-values[0],
                       'source_url':source_url,'projection_series':'official medium series'})
    if len(result)!=67:raise ValueError('Incomplete population table; expected all 67 Florida counties')
    return result


def scenarios(row):
    projected=row['official_projection'];last=max(map(int,projected));previous=last-5
    rate=(projected[str(last)]/projected[str(previous)])**(1/5)-1
    if not -.05<=rate<=.10:raise ValueError('Unexpected trend; manual model review required')
    result=[]
    for target in [last+5,last+10]:
        values=sorted(round(projected[str(last)]*(1+rate*factor)**(target-last)) for factor in [.75,1,1.25])
        result.append({'year':target,'lower_scenario':values[0],'central_scenario':values[1],
                       'upper_scenario':values[2],'kind':'BBA mechanical search-priority scenario, not an official forecast',
                       'assumption':'Continue the last official five-year annualized growth rate at 75%, 100%, 125%; no spatial allocation',
                       'project_year':None,'latitude':None,'longitude':None,'publication_allowed':False})
    return result


def search_tasks(rows, docs):
    # Rank relative growth and absolute additional residents equally; these are
    # research priorities, not probabilities that a development will be built.
    max_growth=max(max(r['growth_to_horizon_percent'],0) for r in rows) or 1
    max_added=max(max(r['additional_residents_to_horizon'],0) for r in rows) or 1
    ranked=sorted(rows,key=lambda r: -(max(r['growth_to_horizon_percent'],0)/max_growth+
                                     max(r['additional_residents_to_horizon'],0)/max_added))
    tasks=[]
    for row in ranked[:10]:
        tasks.append({'id':'growth-'+row['county'].lower().replace(' ','-'),
            'query':row['county']+' County Florida future land use comprehensive plan highway widening capital improvements 2050',
            'county':row['county'],'reason':'Official relative growth plus additional residents; county-scale evidence only',
            'source_urls':[row['source_url']],'evidence_kind':'growth_inference_search_lead',
            'confidence':'low_inference_not_project_evidence','project_year':None,
            'latitude':None,'longitude':None,'publication_allowed':False})
    for corridor,pattern in CORRIDORS.items():
        sources=[d['url'] for d in docs if pattern.search(d['text'])]
        tasks.insert(0,{'id':'corridor-'+corridor.lower().replace(' ','-'),
            'query':corridor+' St Johns County future land use rezoning PD&E right of way widening 2030 2040 2050',
            'county':'Saint Johns','reason':'Requested test corridor; '+('referenced in official planning documents' if sources else 'specific source corroboration still required'),
            'source_urls':sources,'evidence_kind':'corridor_search_lead','confidence':'research_priority_only',
            'project_year':None,'latitude':None,'longitude':None,'publication_allowed':False})
    return tasks


def run(root, getter=read, max_documents=16):
    out=Path(root)/'research-output';out.mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),
            'status':'completed','publication_allowed':False,'sources':[],'documents':[],
            'population':[],'search_tasks':[],'errors':[]}
    docs=[];queue={};population_urls={POPULATION}
    for ident,title,scope,url,horizon,summary in SOURCES:
        row={'id':ident,'title':title,'scope':scope,'sourceURL':url,'horizon':horizon,
             'summary':summary,'checkedAt':report['generated_at'],'checkStatus':'unavailable'}
        try:
            doc=document(url,getter);docs.append(doc);row['checkStatus']='checked'
            for link in doc['links']:
                if re.search(r'MediumProjections_[0-9]{4}\.pdf$',link['url'],re.I): population_urls.add(link['url'])
                else: queue.setdefault(link['url'],link)
        except Exception as exc:report['errors'].append({'url':url,'error':type(exc).__name__})
        report['sources'].append(row)
    for url in list(queue)[:max_documents]:
        try:docs.append(document(url,getter))
        except Exception as exc:report['errors'].append({'url':url,'error':type(exc).__name__})
    report['document_budget']={'discovered':len(queue),'attempted':min(len(queue),max_documents),
                               'remaining':[u for u in queue if u not in list(queue)[:max_documents]]}
    try:
        population_url=max(population_urls,key=lambda u:int(re.search(r'([0-9]{4})\.pdf$',u)[1]))
        popdoc=document(population_url,getter);rows=population_rows(popdoc['text'],population_url);docs.append(popdoc)
        for row in rows:row['post_horizon_scenarios']=scenarios(row)
        report['population']=rows;report['search_tasks']=search_tasks(rows,docs)
    except Exception as exc:report['errors'].append({'url':POPULATION,'error':type(exc).__name__})
    # Keep plan evidence distinct from project schedules, including every linked
    # horizon beyond 2050. Table column dates are never assigned to a row by regex.
    for doc in docs:
        evidence=[]
        for corridor,pattern in CORRIDORS.items():
            for match in list(pattern.finditer(doc['text']))[:8]:
                evidence.append({'corridor':corridor,'excerpt':doc['text'][max(0,match.start()-80):match.end()+180],
                                 'interpretation':'plan mention only; row, phase, funding period and geometry require verification'})
        report['documents'].append({k:v for k,v in doc.items() if k!='text'}|{'corridor_evidence':evidence})
    candidates={}
    for doc in docs:
        for match in re.finditer(r'\b[0-9]{6}-[0-9](?:-[0-9]{1,2})?\b',doc['text']):
            ident=match[0]
            row=candidates.setdefault(ident,{'financial_project_id':ident,'source_urls':[],
                'evidence_kind':'official_plan_id_mention','construction_status':'not_verified',
                'project_year':None,'latitude':None,'longitude':None,'publication_allowed':False})
            if doc['url'] not in row['source_urls']:row['source_urls'].append(doc['url'])
    report['unresolved_project_candidates']=list(candidates.values())
    report['coverage']='Initial Florida research: SJC land use, North Florida plans, statewide framework and all-county population. Planning horizon is not a project schedule. Growth leads have no map coordinates. Needs plans are not funded construction.'
    if report['errors'] or report['document_budget']['remaining']:report['status']='partial'
    path=out/'long_range.review.json';path.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    (out/'planning_sources_review.json').write_text(json.dumps({'status':'completed','sources':report['sources']},indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--max-documents',type=int,default=16);a=p.parse_args()
    report=run(a.root,max_documents=a.max_documents)
    print(f"Long-range research: {report['status']}; {len(report['population'])} counties; {len(report['documents'])} documents; {len(report['search_tasks'])} search priorities")
    if report['status']!='completed':raise SystemExit(1)
