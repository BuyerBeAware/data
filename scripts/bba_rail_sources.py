#!/usr/bin/env python3
"""Read official rail planning and county crossing notices; never fabricate pins."""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from bs4 import BeautifulSoup

JTA='https://www.jtafla.com/mobilityworks-20/projects/jrtc-rail-terminal-project-development/'
COUNTY='https://www.sjcfl.us/wp-json/wp/v2/posts?'+urlencode({'search':'railroad','per_page':20,'_fields':'id,date,modified,link,title,content'})
OPERATOR='https://fecrwy.com/industrial-development/'
HOSTS={'www.jtafla.com','jtafla.com','www.sjcfl.us','sjcfl.us','fecrwy.com','www.fecrwy.com'}

class RailSourceError(ValueError):pass


def approved(url):
    try:
        p=urlsplit(url)
        return p.scheme=='https' and p.hostname in HOSTS and p.username is None and p.password is None and p.port in (None,443)
    except ValueError:return False


class Redirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not approved(newurl):raise RailSourceError('Unapproved source redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch(url):
    if not approved(url):raise RailSourceError('Unapproved rail source')
    with build_opener(Redirects()).open(Request(url,headers={'User-Agent':'BuyerBeAware/1.0 public project research'}),timeout=25) as response:
        body=response.read(2_000_001)
    if len(body)>2_000_000:raise RailSourceError('Source exceeded size limit')
    return body.decode('utf-8-sig')


def plain(html):
    s=BeautifulSoup(html,'html.parser')
    for t in s(['script','style','nav','footer','header']):t.decompose()
    return ' '.join(s.get_text(' ',strip=True).split())


def record(key,title,summary,status,geography,url,checked,published=None):
    if not approved(url):raise RailSourceError('Invalid project source URL')
    return {'id':key,'title':title,'summary':summary,'status':status,'geography':geography,
            'sourceURL':url,'sourceCheckedAt':checked,'publishedDate':published,
            'projectHorizon':'Construction/service date not established by this record',
            'evidenceKind':'official_source','locationStatus':'alignment_not_verified',
            'publicationAllowed':False}


def jta_project(html,checked):
    s=BeautifulSoup(html,'html.parser')
    heading=next((h for h in s.find_all(['h1','h2']) if 'JRTC Rail Terminal Project Development' in h.get_text()),None)
    status=next((h for h in s.find_all(['h2','h3','h4']) if h.get_text(' ',strip=True)=='Status'),None)
    if heading is None or status is None:raise RailSourceError('JTA project fields missing')
    card=status.find_parent(class_='card')
    status_value=card.select_one('.card-body') if card else status.find_next_sibling()
    status_text=plain(str(status_value)) if status_value else ''
    if not status_text or len(status_text)>100:raise RailSourceError('JTA status could not be read')
    text=plain(html)
    for expected in ['St. Augustine','Jacksonville','proposed']:
        if expected.casefold() not in text.casefold():raise RailSourceError('JTA rail scope changed; inspect source')
    p=record('jta-jrtc-regional-rail-planning',heading.get_text(' ',strip=True),
        'JTA describes proposed passenger rail between Jacksonville and St. Augustine. The terminal project covers planning and environmental review at the Jacksonville Regional Transportation Center. A planning record does not establish that service or construction is approved.',
        status_text,'Jacksonville terminal planning; related Jacksonville–St. Augustine regional rail corridor',JTA,checked)
    p['evidenceKind']='agency_planning_project'
    return p


def scheduled_end(html):
    # Only an explicit scheduled range; never infer completion from a publication date.
    text=plain(html)
    m=re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+(\d{1,2})\s*[-–]\s*(\d{1,2}),?\s+(20\d{2})\b',text,re.I)
    if not m:return None
    try:
        month=datetime.strptime(m[1].title(),'%b').month
        return date(int(m[4]),month,int(m[3]))
    except ValueError:raise RailSourceError('Unrecognized notice date') from None


def county_projects(payload,checked,today):
    try:posts=json.loads(payload)
    except ValueError:raise RailSourceError('County returned invalid JSON') from None
    if not isinstance(posts,list):raise RailSourceError('County search returned an error')
    result=[];seen=set()
    for post in posts:
        title=plain(post['title']['rendered'])
        if not re.search(r'\b(railroad|railway|rail|grade crossing)\b',title,re.I):continue
        key='sjc-rail-notice-'+str(post['id'])
        if type(post['id']) is not int or key in seen:raise RailSourceError('Invalid or duplicate county notice ID')
        seen.add(key)
        body=post['content']['rendered'];summary=plain(body)
        end=scheduled_end(body)
        status='Announcement; current project activity not independently verified'
        if end and end<today:status='Past scheduled work window; completion not verified'
        published=str(post['date'])[:10]
        date.fromisoformat(published)
        p=record(key,title,summary[:650],status,'St. Johns County; see source for crossing and work limits',post['link'],checked,published)
        p['evidenceKind']='county_rail_work_notice'
        if end:
            p['projectHorizon']='Source scheduled window ended '+end.isoformat()+'; completion not confirmed'
        result.append(p)
    return result


def run(root=None,get=fetch,today=None):
    root=Path(root) if root else Path(__file__).resolve().parents[1]
    output=root/'research-output';output.mkdir(parents=True,exist_ok=True)
    checked=datetime.now(timezone.utc).isoformat();today=today or datetime.now(timezone.utc).date()
    report={'schema_version':1,'status':'completed','generated_at':checked,'publication_allowed':False,
            'coverage':'JTA regional rail planning project and latest 20 matching county posts; operator directory checked as context, not a project',
            'sources':[],'projects':[]}
    for url,kind in [(JTA,'project'),(COUNTY,'county_notices'),(OPERATOR,'operator_directory')]:
        try:
            body=get(url)
            if kind=='project':report['projects'].append(jta_project(body,checked))
            elif kind=='county_notices':report['projects'].extend(county_projects(body,checked,today))
            else:
                if 'industrial' not in plain(body).lower():raise RailSourceError('Operator directory layout changed')
            report['sources'].append({'url':url,'status':'checked','kind':kind,
                'note':'Directory is context only; no specific developer project promoted' if kind=='operator_directory' else None})
        except Exception as exc:
            report['status']='partial'
            report['sources'].append({'url':url,'status':'failed','error':str(exc) if isinstance(exc,RailSourceError) else type(exc).__name__})
    temp=output/'rail_projects.review.json.tmp';temp.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n');temp.replace(output/'rail_projects.review.json')
    return report

if __name__=='__main__':
    report=run();print('Rail discovery: '+report['status']+'; '+str(len(report['projects']))+' records')
    raise SystemExit(0 if report['status']=='completed' else 1)
