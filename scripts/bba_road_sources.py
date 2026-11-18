#!/usr/bin/env python3
"""Discover official FDOT road projects through structured lists and detail pages.
No guessed coordinates or production feed writes. Requires beautifulsoup4.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bs4 import BeautifulSoup

NFL = 'https://nflroads.com/'
CFL = 'https://www.cflroads.com/'
NFL_LIST = NFL + 'ProjectList.aspx'
CFL_LISTS = [CFL+'projects/Road/US1/Future', CFL+'projects/Road/US1/Construction']
PID = re.compile(r'\d{6}-\d(?:-\d)?')
FINANCIAL = re.compile(r'\d{6}-\d(?:-\d{1,2}){0,2}')


def county_keys(value):
    return {re.sub(r'[^a-z]', '', c.lower()) for c in re.split(r'[,/]',value) if c.strip()}


class SourceError(ValueError):
    pass


def approved(url):
    try:
        p=urlsplit(url)
        return (p.scheme=='https' and p.hostname in {'nflroads.com','www.nflroads.com','www.cflroads.com','cflroads.com'}
                and p.username is None and p.password is None and p.port in (None,443))
    except ValueError:
        return False


class Redirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not approved(newurl): raise SourceError('Source redirected outside FDOT project sites')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class Client:
    def read(self,url,payload=None):
        if not approved(url): raise SourceError('Unapproved project source')
        headers={'User-Agent':'BuyerBeAware/1.0 official project research'}
        body=None
        if payload is not None:
            headers['Content-Type']='application/json; charset=utf-8'
            body=json.dumps(payload).encode()
        with build_opener(Redirects()).open(Request(url,data=body,headers=headers),timeout=25) as response:
            data=response.read(2_000_001)
        if len(data)>2_000_000: raise SourceError('Source exceeded size limit')
        return data.decode('utf-8-sig')


def clean(node):
    return node.get_text(' ',strip=True) if node else ''


def nfl_county_id(html):
    s=BeautifulSoup(html,'html.parser')
    select=s.select_one('#county_id')
    if select:
        for option in select.select('option[value]'):
            if clean(option)=='St. Johns' and option['value'].isdigit(): return option['value']
    raise SourceError('St. Johns county selector missing; source layout changed')


def nfl_list(payload,status):
    try: html=json.loads(payload)['d']
    except (ValueError,KeyError,TypeError): raise SourceError('Invalid North Florida list response') from None
    s=BeautifulSoup(html,'html.parser')
    table=s.select_one('#ProjectSearchResults')
    if table is None: raise SourceError('North Florida results table missing')
    links=[]
    for row in table.select('tr'):
        cells=row.select('td')
        if not cells: continue
        if len(cells)!=3: raise SourceError('Unexpected project-list columns')
        a=cells[0].find('a',href=True)
        if not a: raise SourceError('Project row missing detail link')
        url=urljoin(NFL,a['href']);p=urlsplit(url);ids=parse_qs(p.query).get('p',[])
        if not approved(url) or p.path!='/ProjectDetails.aspx' or len(ids)!=1 or not ids[0].isdigit():
            raise SourceError('Unexpected North Florida project URL')
        links.append({'source_url':NFL+'ProjectDetails.aspx?'+urlencode({'p':ids[0]}),
                      'site_project_id':ids[0], 'listing_status':status,
                      'listing_county':clean(cells[2]),'listing_schedule':clean(cells[1]),
                      'discovery_url':NFL_LIST,'listing_title':clean(a)})
    return links


def cfl_list(html,url):
    s=BeautifulSoup(html,'html.parser')
    h=s.find('h1')
    if not h or 'US 1' not in clean(h) or 'Projects' not in clean(h):
        raise SourceError('Central Florida US-1 listing heading missing')
    links={}
    for a in s.select('a[href]'):
        target=urljoin(CFL,a['href']);m=re.fullmatch(r'/project/('+PID.pattern+')/?',urlsplit(target).path)
        if approved(target) and m:
            pid=m[1]
            links[pid]={'source_url':CFL+'project/'+pid,'site_project_id':pid,
                        'listing_status':'Future' if '/Future' in url else 'Construction',
                        'discovery_url':url,'listing_title':clean(a)}
    if not links: raise SourceError('No project links in Central Florida listing; inspect source')
    return list(links.values())


def table_fields(soup,selector):
    result={}
    for row in soup.select(selector):
        cells=row.find_all(['th','td'],recursive=False)
        if len(cells)!=2: continue
        key=clean(cells[0]).rstrip(':')
        value=clean(cells[1])
        if key in result and result[key]!=value:
            raise SourceError('Conflicting duplicate detail fields')
        result[key]=value
    return result


def parse_detail(html,listing):
    s=BeautifulSoup(html,'html.parser');url=listing['source_url']
    is_nfl='nflroads.com' in urlsplit(url).hostname
    if is_nfl:
        title=clean(s.select_one('#MainContent_ProjectTitle'))
        fields=table_fields(s,'table.ProjectDetails tr')
        county=fields.get('Counties','')
        raw_number=fields.get('Project #') or None
        numbers=[n.strip() for n in (raw_number or '').split(',') if FINANCIAL.fullmatch(n.strip())]
        number=numbers[0] if len(numbers)==1 and raw_number==numbers[0] else None
        overview=s.select_one('#overview')
        if overview:
            overview=BeautifulSoup(str(overview),'html.parser')
            for t in overview.select('.SectionTitle'):t.decompose()
        about=clean(overview)
        phase=fields.get('Phase') or 'Not provided'
        start=fields.get('Project Start') or None
        finish=fields.get('Expected Completion') or None
        documents_node=s.select_one('#documents')
        last_updated=None
    else:
        heading=clean(s.find('h1'))
        match=re.fullmatch(r'('+PID.pattern+r')\s+(.+)',heading)
        if not match or match[1]!=listing['site_project_id']:
            raise SourceError('Detail identity does not match requested project')
        number,title=match.groups()
        raw_number=number
        numbers=[number]
        fields=table_fields(s,'tr')
        county=fields.get('County','')
        about=clean(s.select_one('.about-section'))
        phase=fields.get('Phase') or 'Not provided'
        start=fields.get('Construction Start') or None
        finish=fields.get('Construction End') or fields.get('Est. Completion') or None
        documents_node=s
        last_updated=clean(s.find('time')) or None
    if not title:
        raise SourceError('Project title missing')
    description_status='provided' if about else 'not_provided'
    if not about:
        if not (is_nfl and s.select_one('#overview') is not None and county and numbers):
            raise SourceError('Project description or recognized detail layout missing')
        about='Description not provided by the official project page.'
    if listing.get('listing_county') and county_keys(county)!=county_keys(listing['listing_county']):
        raise SourceError('List and detail county disagree')
    docs={}
    if documents_node:
        for a in documents_node.select('a[href]'):
            href=urljoin(url,a['href'])
            if approved(href) and (urlsplit(href).path.lower().endswith('.pdf') or '/ProjectFiles/' in href):
                docs[href]={'title':clean(a),'url':href}
    # County is authoritative; a title containing "St. Johns Loop" does not locate it in St. Johns County.
    county_names=county_keys(county)
    status=phase if phase!='Not provided' else listing['listing_status']+' listing; phase not provided'
    return {**listing,'id':'fdot-'+number if number else 'nflroads-'+listing['site_project_id'],
            'financial_project_id':number,'financial_project_ids':numbers,'source_project_number':raw_number,
            'title':title,'county':county or None,'county_status':'provided' if county else 'not_provided',
            'cities':fields.get('Cities') or fields.get('City') or None,
            'phase':phase,'status':status,
            'is_st_johns_county':'stjohns' in county_names,
            'description':about[:12000], 'description_status':description_status, 'schedule':{'start':start,'completion':finish},
            'funding_status':'Not established; inspect linked project documents',
            'source_last_updated':last_updated,'documents':list(docs.values()),
            'latitude':None,'longitude':None,'geometry':None,'location_status':'needs_verified_alignment',
            'project_horizon':'Not inferred from publication, meeting or study dates',
            'publication_allowed':False,'review_only':True}


def write(path,payload):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(payload,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    temp.replace(path)


def run(root=None,client=None,max_details=250,extra_listings=None):
    root=Path(root) if root else Path(__file__).resolve().parents[1]
    output=root/'research-output';output.mkdir(parents=True,exist_ok=True)
    client=client or Client()
    report={'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),
            'status':'completed','publication_allowed':False,'sources':[], 'projects':[],
            'coverage':'All counties in NFLRoads active/future lists and CFLRoads US-1 future/construction lists; not all Florida districts or completed projects'}
    listings=list(extra_listings or [])
    def error(url,exc):
        report['status']='partial'
        report['sources'].append({'url':url,'status':'failed','error':str(exc) if isinstance(exc,SourceError) else type(exc).__name__})
    try:
        # Validate the known selector, but do not constrain a regional highway to one county.
        nfl_county_id(client.read(NFL_LIST))
        county_id='0'
        for typ,status in [('1','Active'),('2','Future')]:
            payload={'projectType':typ,'countyId':county_id,'cityId':'0','roadId':'0','projectCategoryId':'0','searchPhrase':''}
            url=NFL_LIST+'/GetResults'
            try:
                found=nfl_list(client.read(url,payload),status);listings.extend(found)
                report['sources'].append({'url':url,'query':payload,'status':'completed','projects_listed':len(found)})
            except Exception as exc: error(url,exc)
    except Exception as exc: error(NFL_LIST,exc)
    for url in CFL_LISTS:
        try:
            found=cfl_list(client.read(url),url);listings.extend(found)
            report['sources'].append({'url':url,'status':'completed','projects_listed':len(found)})
        except Exception as exc: error(url,exc)
    by_url={}
    for listing in listings:
        url=listing['source_url']
        by_url.setdefault(url,[]).append(listing)
    urls=list(by_url)
    report['discovered_detail_count']=len(urls)
    if len(urls)>max_details:
        report['status']='partial'
        report['detail_budget_exceeded']=len(urls)-max_details
    def read(url):
        try:
            primary=next((l for l in by_url[url] if l.get('listing_county')),by_url[url][0])
            item=parse_detail(client.read(url),primary)
            item['discovered_in']=sorted({l['discovery_url'] for l in by_url[url]})
            item['listing_statuses']=sorted({l['listing_status'] for l in by_url[url]})
            return item,None
        except Exception as exc:return None,exc
    identities={}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for url,(item,exc) in zip(urls[:max_details],pool.map(read,urls[:max_details])):
            if exc:error(url,exc);continue
            key=item['id']
            if key in identities:
                # A financial ID may cover multiple source projects. Preserve distinct
                # records under a shared group; never merge their counties or schedules.
                item['identity_conflict']=True
                item['financial_identity_group']=key
                item['id']=key+'--'+urlsplit(url).hostname+'-'+item['site_project_id']
                identities[key]['identity_conflict']=True
                identities[key]['financial_identity_group']=key
            identities.setdefault(key,item)
            report['projects'].append(item)
    report['identity_conflict_count']=sum(bool(p.get('identity_conflict')) for p in report['projects'])
    report['project_count']=len(report['projects'])
    report['st_johns_project_count']=sum(p['is_st_johns_county'] for p in report['projects'])
    write(output/'road_projects.review.json',report)
    return report


if __name__=='__main__':
    result=run()
    print('Road discovery: '+result['status']+'; '+str(result['project_count'])+' detail records')
    raise SystemExit(0 if result['status']=='completed' else 1)
