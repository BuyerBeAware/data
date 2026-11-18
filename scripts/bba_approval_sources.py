#!/usr/bin/env python3
"""County-independent state corridor history and federal highway/bridge approvals.
Documents are evidence, not map points or proof of current construction.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from bs4 import BeautifulSoup
from bba_road_sources import SourceError, clean, write

FCE='https://nflroads.com/FirstCoastExpressway.aspx'
FR='https://www.federalregister.gov/api/v1/documents.json'
QUERIES=[('federal-highway-administration','Florida'),('coast-guard','Florida bridge'),
         ('engineers-corps','Florida highway'),('environmental-protection-agency','Florida highway')]
HOSTS={'nflroads.com','www.nflroads.com','www.federalregister.gov','federalregister.gov',
       'www.govinfo.gov','govinfo.gov','public-inspection.federalregister.gov'}
ID=re.compile(r'\b\d{6}-\d(?:-\d{1,2}){0,2}\b')
NOTICE=re.compile(r'final (?:federal )?agency actions|environmental impact statement|record of decision|finding of no significant impact|bridge.*(?:permit|construction|replacement)|(?:permit|construction|replacement).*bridge',re.I)


def approved(url):
    try:
        p=urlsplit(url)
        return p.scheme=='https' and p.hostname in HOSTS and p.username is None and p.password is None and p.port in (None,443)
    except ValueError:return False


class Redirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not approved(newurl):raise SourceError('Approval source redirected outside approved hosts')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class Client:
    def read(self,url):
        if not approved(url):raise SourceError('Unapproved approval source')
        with build_opener(Redirects()).open(Request(url,headers={'User-Agent':'BuyerBeAware/1.0 official highway research'}),timeout=25) as r:
            raw=r.read(4_000_001)
        if len(raw)>4_000_000:raise SourceError('Approval source exceeded size limit')
        return raw.decode('utf-8-sig')


def program(html):
    s=BeautifulSoup(html,'html.parser')
    text=clean(s)
    if 'First Coast Expressway' not in text or 'Shands Bridge' not in text:raise SourceError('Corridor identity missing')
    segments={};documents={};history=[]
    for a in s.select('a[href]'):
        u=urljoin(FCE,a['href']);p=urlsplit(u);label=clean(a)
        # FDOT publishes legacy HTTP and extensionless links. Canonicalize only this known host/path.
        if p.hostname in {'nflroads.com','www.nflroads.com'} and p.path in ['/ProjectDetails','/ProjectDetails.aspx']:
            ids=parse_qs(p.query).get('p',[])
            if len(ids)==1 and ids[0].isdigit():
                u='https://nflroads.com/ProjectDetails.aspx?p='+ids[0]
                segments[u]={'source_url':u,'site_project_id':ids[0],'listing_title':label,
                             'listing_status':'Corridor listing; current phase requires detail source','discovery_url':FCE}
        if approved(u) and p.path.lower().endswith('.pdf'):
            row=a.find_parent('tr');cells=row.find_all('td') if row else []
            date=clean(cells[-1]) if len(cells)>1 else None
            kind='record_of_decision' if 'record of decision' in label.lower() else 'environmental_impact_statement' if 'environmental impact statement' in label.lower() else 'project_document'
            documents[u]={'title':label,'url':u,'listed_date':date,'evidence_kind':kind,
                          'verification':'official_program_link; PDF contents not automatically verified',
                          'construction_status':'not_established_by_document_listing'}
    for node in s.find_all(['p','li']):
        t=clean(node)
        if re.search(r'\bcompleted\b',t,re.I) and ('FCE' in t or '2019' in t):
            if t not in history:history.append(t)
    if not segments or not any(d['evidence_kind']=='record_of_decision' for d in documents.values()):
        raise SourceError('Corridor segments or decision document missing')
    return {'id':'fdot-program-first-coast-expressway','title':'First Coast Expressway / SR 23 / Shands Bridge replacement',
            'aliases':['First Coast Outer Beltway','St. Johns River Crossing','SR 23','Shands Bridge'],
            'source_url':FCE,'jurisdiction':'FDOT / Florida Turnpike Enterprise',
            'segments':list(segments.values()),'documents':list(documents.values()),
            'historical_completion_evidence':history,'historical_evidence_source_url':FCE,
            'schedule_note':'Program and segment schedules can differ; retain detail-page schedules and conflicts.',
            'publication_allowed':False,'latitude':None,'longitude':None,'geometry':None}


def fr_url(agency,term,page=1):
    return FR+'?'+urlencode({'conditions[agencies][]':agency,'conditions[term]':term,
                            'per_page':100,'order':'oldest','page':page})


def fr_page(raw):
    try:d=json.loads(raw)
    except ValueError:raise SourceError('Invalid federal search JSON') from None
    if not isinstance(d,dict) or not isinstance(d.get('results'),list) or not isinstance(d.get('total_pages'),int):
        raise SourceError('Federal search schema changed')
    if d['total_pages']<0:raise SourceError('Invalid federal page count')
    return d


def federal_notice(detail,body):
    number=detail.get('document_number','');title=detail.get('title','');url=detail.get('html_url','')
    if not re.fullmatch(r'(?:\d{4}|\d{2}|E\d)-\d+',number) or not title or not approved(url):raise SourceError('Federal document identity missing')
    if number not in urlsplit(url).path.split('/'):raise SourceError('Federal URL/document identity mismatch')
    text=clean(BeautifulSoup(body,'html.parser'))
    if not text:raise SourceError('Federal document body missing')
    norm=text.replace('\u2013','-').replace('\u2011','-').replace('\u2014','-')
    phase='final_agency_actions_announced' if re.search(r'final (?:federal )?agency actions',title,re.I) else 'environmental_review_or_permit_notice'
    return {'id':'federal-register-'+number,'document_number':number,'title':title,
            'source_url':url,'official_pdf_url':detail.get('pdf_url') if approved(detail.get('pdf_url','')) else None,
            'body_source_url':detail.get('body_html_url'),'publication_date':detail.get('publication_date'),
            'agencies':[a.get('name') for a in detail.get('agencies',[])],
            'approval_status':phase,'current_construction_status':'not_established_by_historical_notice',
            'financial_project_ids':sorted(set(ID.findall(norm))),
            'evidence_excerpt':text[:18000],'body_sha256':__import__('hashlib').sha256(body.encode()).hexdigest(),
            'confidence':'primary_agency_notice; project associations require explicit identifiers',
            'scope_note':'One notice may cover several projects; do not treat all locations or dates as one project.',
            'latitude':None,'longitude':None,'geometry':None,'publication_allowed':False,'review_only':True}


def run(root=None,client=None,max_pages=10,max_details=250):
    root=Path(root) if root else Path(__file__).resolve().parents[1];out=root/'research-output';out.mkdir(parents=True,exist_ok=True)
    c=client or Client();r={'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),'status':'completed',
       'publication_allowed':False,'review_only':True,'programs':[],'notices':[],'sources':[],
       'coverage':{'county_filter':None,'federal_history_start':'All available API history; no recent-only cutoff',
                   'federal_queries':QUERIES,'state_programs':[FCE],
                   'limitations':['Not a complete statewide or national permit inventory','County-independent does not mean every FDOT district is integrated',
                                  'USACE district permit portals and state ETDM/work-program databases are not yet integrated',
                                  'Document listings are not proof of current construction or a verified map alignment']}}
    def fail(u,e):
        r['status']='partial';r['sources'].append({'url':u,'status':'failed','error':str(e) if isinstance(e,SourceError) else type(e).__name__})
    try:
        r['programs'].append(program(c.read(FCE)));r['sources'].append({'url':FCE,'status':'completed'})
    except Exception as e:fail(FCE,e)
    candidates={}
    for agency,term in QUERIES:
        u=fr_url(agency,term)
        try:
            first=fr_page(c.read(u));pages=first['total_pages']
            r['sources'].append({'url':u,'status':'completed','result_count':first.get('count'),'pages':pages})
            if pages>max_pages:fail(u,SourceError('Federal page budget exceeded; history incomplete'))
            for page in range(1,min(pages,max_pages)+1):
                data=first if page==1 else fr_page(c.read(fr_url(agency,term,page)))
                for item in data['results']:
                    if NOTICE.search(item.get('title','')):candidates[item['document_number']]=item
        except Exception as e:fail(u,e)
    keys=sorted(candidates) # Stable order; explicit truncation is a failing coverage condition.
    if len(keys)>max_details:fail(FR,SourceError('Federal detail budget exceeded; notices incomplete'))
    def fetch(number):
        u='https://www.federalregister.gov/api/v1/documents/'+number+'.json'
        try:
            d=json.loads(c.read(u))
            if d.get('document_number')!=number:raise SourceError('Requested federal document identity mismatch')
            body_url=d.get('body_html_url','')
            if not approved(body_url) or number not in urlsplit(body_url).path:raise SourceError('Federal body URL identity mismatch')
            try:
                body=c.read(body_url)
            except HTTPError as error:
                # FederalRegister.gov HTML is absent for many 1990s/2000s notices.
                # The API explicitly supplies the official GPO raw-text edition.
                fallback=d.get('raw_text_url','')
                if error.code!=404 or not approved(fallback) or number not in urlsplit(fallback).path:raise
                body=c.read(fallback)
                d={**d,'body_html_url':fallback}
            return federal_notice(d,body),None
        except Exception as e:return None,(u,e)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for item,error in pool.map(fetch,keys[:max_details]):
            if error:fail(*error)
            else:r['notices'].append(item)
    r['candidate_count']=len(keys);r['notice_count']=len(r['notices'])
    write(out/'major_road_approvals.review.json',r)
    return r


if __name__=='__main__':
    r=run();print('Major-road approvals:',r['status'],r['notice_count'],'notices')
    raise SystemExit(0 if r['status']=='completed' else 1)
