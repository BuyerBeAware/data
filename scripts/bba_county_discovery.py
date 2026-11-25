#!/usr/bin/env python3
"""Configurable public-project discovery. Review artifacts only; never writes app feeds.
Community/news are leads, not proof of approvals, construction dates or locations.
"""
import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse, quote, parse_qs
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
STAMP = datetime.now(timezone.utc).isoformat()


def session():
    s = requests.Session()
    s.headers['User-Agent'] = 'BuyerBeAwarePublicProjectResearch/1.0'
    s.mount('https://', HTTPAdapter(max_retries=Retry(total=2, backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504], allowed_methods=['GET'])))
    return s


def request_json(s, url, params):
    r = s.get(url, params=params, timeout=(12, 45)); r.raise_for_status()
    d = r.json()
    if 'error' in d:
        raise ValueError(str(d['error'])[:400])
    return d


def http_url(value):
    return isinstance(value, str) and urlparse(value).scheme in ('http', 'https') and bool(urlparse(value).hostname)


def record(key, county, title, description, sources, **kwargs):
    return dict(id=key, county_id=county['id'], state=county['state'], title=title,
                description=description, source_urls=list(dict.fromkeys(sources)),
                checked_at=STAMP, status='Not verified', horizon='unknown',
                confidence='official record; location/timing need review', coordinates=None,
                publication_eligible=False, **kwargs)


def parse_register(html, typ, base, county):
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', id=re.compile(r'gdvApps$'))
    if table is None:
        raise ValueError('Register table missing; empty and unavailable are different')
    rows = []
    for tr in table.select('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) < 4:
            continue
        number = cells[0].get_text(' ', strip=True)
        if not re.fullmatch(r'\d{10}', number):
            continue
        url = urljoin(base, 'Record') + '?' + urlencode({'ApplNum': number, 'ApplType': typ})
        rows.append(record(f"{county['id']}:sjc:{typ}:{number}", county,
            cells[1].get_text(' ', strip=True), '', [url], authority='St Johns County',
            record_type=typ, record_number=number,
            application_created=cells[2].get_text(' ', strip=True),
            application_updated=cells[3].get_text(' ', strip=True)))
    # Do not silently return one page if the portal begins paging.
    if soup.find('a', href=re.compile(r'Page\$')):
        raise ValueError('Register paging detected: adapter must be updated; not complete')
    return rows


def register_year(s, county, typ, year):
    cfg = county['register']; url = urljoin(cfg['base'], 'AppPicklist')
    url += '?' + urlencode({'ApplType': typ, 'View': 'Apps'})
    r = s.get(url, timeout=35); r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    sel = soup.find('select', attrs={'name': re.compile('ddlYr$')})
    if not sel or str(year) not in [o.get('value') for o in sel.select('option')]:
        raise ValueError('Requested register year unavailable')
    form = {i['name']: i.get('value', '') for i in soup.select('input[type=hidden][name]')}
    form.update({'__EVENTTARGET': sel['name'], '__EVENTARGUMENT': '', sel['name']: str(year)})
    r = s.post(url, data=form, timeout=45); r.raise_for_status()
    result_soup = BeautifulSoup(r.text, 'html.parser')
    chosen = result_soup.find('select', attrs={'name': sel['name']})
    selected = chosen.find('option', selected=True) if chosen else None
    if not selected or selected.get('value') != str(year):
        raise ValueError('Portal did not confirm requested year')
    if result_soup.find('table',id=re.compile(r'gdvApps$')) is None:
        # Portal hides an empty GridView. Year confirmation prevents treating a login/error page as empty.
        if result_soup.find(id='ContentPlaceHolder1_ddlYr') and 'Application Submittal' in r.text:
            return []
        raise ValueError('Unrecognized empty listing response')
    return parse_register(r.text, typ, cfg['base'], county)


def enrich_register(s, row):
    url=row['source_urls'][0]
    response=s.get(url,timeout=35);response.raise_for_status()
    soup=BeautifulSoup(response.text,'html.parser')
    heading=' '.join(h.get_text(' ',strip=True) for h in soup.select('h1,h2,h3'))
    if row['record_number'] not in heading:
        raise ValueError('Detail page identity missing')
    # Store bounded public project text, excluding applicant/contact tables.
    descriptions=[]
    for tr in soup.select('tr'):
        cells=tr.find_all(['td','th'],recursive=False)
        if len(cells)>=2:
            label=cells[0].get_text(' ',strip=True)
            if re.search(r'^(Proj(ect)? Desc(ription)?|Location|Status|Issue Date|Comp(letion)? Date)[: ]*$',label,re.I):
                descriptions.append(label+': '+cells[-1].get_text(' ',strip=True))
    direct=soup.find(id=re.compile(r'lblProjDesc$'))
    if direct:descriptions.insert(0,direct.get_text(' ',strip=True))
    row['description']=' | '.join(descriptions)[:10000]
    row['detail_heading']=heading[:500]
    related=[]
    for a in soup.select('a[href]'):
        href=urljoin(url,a['href'].strip());parsed=urlparse(href);args=parse_qs(parsed.query)
        number=args.get('ApplNum',[''])[0].strip();typ=args.get('ApplType',[''])[0].strip()
        if parsed.hostname=='webapp.sjcfl.us' and re.fullmatch(r'\d{10}',number) and re.fullmatch('[A-Z]+',typ) and number!=row['record_number']:
            canonical='https://webapp.sjcfl.us/CSubmitalWeb/Record?'+urlencode({'ApplType':typ,'ApplNum':number})
            item={'url':canonical,'label':a.get_text(' ',strip=True)[:150]}
            if item not in related:related.append(item)
    row['related_records']=related
    row['source_urls']=list(dict.fromkeys(row['source_urls']+[r['url'] for r in related]))
    row['detail_checked_at']=STAMP


def gis_where(cfg):
    year=datetime.now(timezone.utc).year
    return cfg.get('where','1=1').format(year=year,previous_year=year-1,two_years_ago=year-2)


def gis_records(s, county, cfg):
    ids = request_json(s, cfg['url'] + '/query', {'f':'json', 'where':gis_where(cfg), 'returnIdsOnly':'true'})
    if ids.get('exceededTransferLimit') or 'objectIds' not in ids:
        raise ValueError('Incomplete object ID enumeration')
    oid_list = sorted(set(ids['objectIds'] or [])); found = {}; rows = []
    for start in range(0, len(oid_list), 100):
        batch = oid_list[start:start+100]
        data = request_json(s, cfg['url'] + '/query', {'f':'json', 'objectIds':','.join(map(str,batch)),
            'outFields':','.join(cfg['fields']), 'returnGeometry':'true', 'outSR':4326})
        if data.get('exceededTransferLimit'):
            raise ValueError('GIS batch truncated')
        for f in data.get('features', []):
            a = f['attributes']; oid = a[cfg['oid']]
            if oid in found:
                raise ValueError('Duplicate GIS object ID')
            found[oid] = True
            identity = a.get(cfg['identity'])
            if identity in (None, ''):
                raise ValueError('Missing stable project identity')
            evidence = cfg['url'] + '/query?' + urlencode({'objectIds':oid, 'outFields':','.join(cfg['fields']), 'outSR':4326, 'f':'pjson'})
            urls = [cfg['map_url'], evidence, cfg['trust_url']]
            if http_url(a.get(cfg['link'])):
                urls.append(a[cfg['link']])
            r = record(f"{county['id']}:{cfg['id']}:{identity}:{oid}", county,
                str(a.get(cfg['name']) or identity), str(a.get(cfg['description']) or ''), urls,
                authority=cfg['authority'], record_type=cfg['id'], record_number=str(identity),
                official_fields=a, geometry_evidence=f.get('geometry'),
                location_review='Official project geometry; county, phase and description cross-check required')
            r['status'] = str(a.get(cfg['status']) or 'Not provided')
            # Approval, phase completion and fiscal dates are deliberately not construction completion/year.
            rows.append(r)
    if set(found) != set(oid_list):
        raise ValueError('GIS completeness mismatch')
    return rows


def inside(point, rings):
    """Even-odd containment handles disjoint rings and holes in ArcGIS geometry."""
    x,y=point;hit=False
    for ring in rings:
        for a,b in zip(ring,ring[1:]+ring[:1]):
            if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
                hit=not hit
    return hit


def geometry_candidate(geometry, county_rings):
    if not geometry:return None
    if 'x' in geometry and 'y' in geometry:
        point=(geometry['x'],geometry['y']);kind='official project representative point'
    elif geometry.get('rings'):
        rings=geometry['rings'];ys=sorted(set(p[1] for ring in rings for p in ring))
        # Mid-scanlines avoid polygon vertices. Choose widest interior interval (never a bbox centroid).
        best=None
        scanlines=[(a+b)/2 for a,b in zip(ys,ys[1:])]
        scanlines=scanlines[::max(1,len(scanlines)//200)]
        for y in scanlines:
            xs=[]
            for ring in rings:
                for a,b in zip(ring,ring[1:]+ring[:1]):
                    if (a[1]>y)!=(b[1]>y):xs.append(a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1]))
            xs.sort()
            for a,b in zip(xs[::2],xs[1::2]):
                p=((a+b)/2,y)
                if inside(p,rings) and inside(p,county_rings) and (best is None or b-a>best[0]):best=(b-a,p)
        if best is None:return None
        point=best[1];kind='interior point of official project polygon; not a building footprint'
    elif geometry.get('paths'):
        # A point ON a sourced alignment, never an invented corridor polygon.
        options=[]
        for path in geometry['paths']:
            for a,b in zip(path,path[1:]):
                p=((a[0]+b[0])/2,(a[1]+b[1])/2)
                if inside(p,county_rings):options.append(((a[0]-b[0])**2+(a[1]-b[1])**2,p))
        if not options:return None
        point=max(options,key=lambda x:x[0])[1];kind='representative point on official public project alignment; exact work limits not verified'
    else:return None
    if not (-180<=point[0]<=180 and -90<=point[1]<=90) or not inside(point,county_rings):return None
    return {'latitude':point[1],'longitude':point[0],'method':kind,
            'county_boundary_verified':True,'description_phase_verified':False,
            'review_required':True,'publication_eligible':False}


def location_candidates(s, county, rows):
    boundary_url='https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query'
    data=request_json(s,boundary_url,{'where':"GEOID='%s'"%county['geoid'],'outFields':'GEOID,NAME','returnGeometry':'true','outSR':4326,'f':'json'})
    features=data.get('features',[])
    if len(features)!=1 or features[0]['attributes'].get('GEOID')!=county['geoid']:
        raise ValueError('County boundary identity mismatch')
    rings=features[0]['geometry']['rings'];candidates=[]
    for row in rows:
        if not row.get('in_current_snapshot',True):continue
        geometry=row.get('geometry_evidence')
        if geometry:
            candidate=geometry_candidate(geometry,rings)
            if candidate:
                row['location_candidate']=candidate
                candidates.append({'record_id':row['id'],'title':row['title'],'county_id':county['id'],
                    'source_urls':row['source_urls'],'boundary_source':boundary_url+'?'+urlencode({'where':"GEOID='%s'"%county['geoid'],'f':'pjson'}),
                    **candidate})
            else:row['location_review']='Withheld: missing, unsupported or out-of-county geometry'
    return candidates


def deduplicate(rows):
    result = {}
    for r in rows:
        if r['id'] in result:
            old = result[r['id']]
            if old['title'] != r['title'] or old.get('official_fields') != r.get('official_fields'):
                raise ValueError('Conflicting exact identity: ' + r['id'])
            old['source_urls'] = list(dict.fromkeys(old['source_urls'] + r['source_urls']))
        else:
            result[r['id']] = r
    return list(result.values())


def search_queries(county):
    queries = [(q, 'news') for q in county['queries']]
    for domain in county['news_domains']:
        queries.append((f'site:{domain} "{county["name"]}" development construction', 'news'))
    for site in ('reddit.com', 'facebook.com/groups', 'nextdoor.com'):
        queries.append((f'site:{site} "{county["name"]}" development construction road fire station', 'public-community'))
    for topic in county.get('agency_queries', []) + ['developer site plan', 'capital improvement budget bid zoning agenda']:
        queries.append((f'"{county["name"]}" {county["state"]} {topic}', 'official-or-developer-lead'))
    return queries


def rss_leads(s, county, query, category):
    # News index is supplemental; Bing indexes publicly visible community/developer pages.
    endpoint = 'https://news.google.com/rss/search' if category == 'news' else 'https://www.bing.com/search'
    params = {'q':query, 'hl':'en-US', 'gl':'US', 'ceid':'US:en'} if category == 'news' else {'q':query, 'format':'rss'}
    r = s.get(endpoint, params=params, timeout=30); r.raise_for_status()
    root = ET.fromstring(r.content)
    leads = []
    for item in root.findall('.//item')[:15]:
        title = item.findtext('title') or ''; link = item.findtext('link') or ''
        if not http_url(link):
            continue
        leads.append({'id':hashlib.sha256((county['id']+link).encode()).hexdigest()[:24],
            'county_id':county['id'], 'title':title, 'source_url':link, 'source_class':category,
            'published_label':item.findtext('pubDate'), 'publisher':item.findtext('source'),
            'publisher_url':item.find('source').get('url') if item.find('source') is not None else None, 'query':query, 'checked_at':STAMP,
            'confidence':'unverified lead', 'coordinates':None, 'publication_eligible':False})
    return leads


def correlate(rows, leads):
    """Suggestions only. Same address/name is not proof two permits are one project."""
    stop = {'the','and','for','county','johns','saint','flagler','palm','coast','project','phase'}
    for lead in leads:
        text = re.sub(r'[^a-z0-9]+',' ',lead['title'].lower())
        matches = []
        for r in rows:
            number = r.get('record_number','')
            tokens = set(re.findall(r'[a-z0-9]+',r['title'].lower())) - stop
            tokens = {t for t in tokens if len(t)>2}
            if (len(number)>=6 and number.lower() in text) or (len(tokens)>=2 and tokens <= set(text.split())):
                matches.append(r['id'])
        lead['possible_record_matches'] = matches
        lead['match_policy'] = 'Review suggestion only; no automatic record merge/status/location change'


def run(county, out, state_dir, history_years=1):
    s = session(); rows=[]; leads=[]; coverage=[]; started=time.monotonic()
    def budget():
        return time.monotonic()-started<1200
    now = datetime.now(timezone.utc).year
    state_dir.mkdir(parents=True,exist_ok=True)
    state_file=state_dir/(county['id']+'.json')
    state=json.loads(state_file.read_text()) if state_file.exists() else {}
    if county.get('register'):
        for typ in county['register']['types']:
            oldest=county['register']['history_start']; cursor=state.get(typ,now-2)
            cursor=min(now-2,max(oldest,int(cursor)))
            years=list(dict.fromkeys([now,now-1]+[max(oldest,cursor-i) for i in range(history_years)]))
            historic_ok=True
            for year in years:
                if not budget():
                    coverage.append({'source':typ,'year':year,'status':'failed','error':'Run budget exhausted; retry this year next run'})
                    historic_ok=False;continue
                try:
                    batch=register_year(s,county,typ,year);rows.extend(batch)
                    coverage.append({'source':typ,'year':year,'status':'complete_listing','records':len(batch)})
                except Exception as e:
                    coverage.append({'source':typ,'year':year,'status':'failed','error':str(e)[:400]})
                    if year<=cursor:historic_ok=False
                time.sleep(.25)
            if historic_ok:
                nxt=cursor-history_years;state[typ]=now-2 if nxt<oldest else nxt
    # Persist a fair rotating detail queue. New/updated rows come first; all others age out.
    detail_state=state.setdefault('detail_checks',{})
    for row in rows:
        cached=detail_state.get(row['id'],{})
        if cached.get('updated')==row.get('application_updated') and cached.get('data'):
            row.update(cached['data'])
    eligible=[r for r in rows if r.get('record_number')]
    eligible.sort(key=lambda r:(bool(detail_state.get(r['id'],{}).get('data')) and detail_state.get(r['id'],{}).get('updated')==r.get('application_updated'),detail_state.get(r['id'],{}).get('checked',''),r['id']))
    for row in eligible[:60]:
        if not budget():break
        try:
            enrich_register(s,row)
            detail_state[row['id']]={'updated':row.get('application_updated'),'checked':STAMP,'data':{k:row[k] for k in ('description','detail_heading','related_records','source_urls','detail_checked_at') if k in row}}
        except Exception as e:
            row['detail_error']=str(e)[:300]
    coverage.append({'source':'register-detail-budget','status':'partial_detail_coverage',
                     'checked':min(60,len(eligible)),'deferred':max(0,len(eligible)-60)})
    for cfg in county['gis']:
        if not budget():
            coverage.append({'source':cfg['id'],'status':'failed','error':'Run budget exhausted'});continue
        try:
            batch=gis_records(s,county,cfg);rows.extend(batch)
            coverage.append({'source':cfg['id'],'status':'complete_snapshot','records':len(batch)})
        except Exception as e:
            coverage.append({'source':cfg['id'],'status':'failed','error':str(e)[:400]})
    for url in county['official_pages']:
        if not budget():
            coverage.append({'source':url,'status':'deferred','reason':'Run budget exhausted'});continue
        try:
            response=s.get(url,timeout=30);response.raise_for_status()
            soup=BeautifulSoup(response.text,'html.parser')
            for tag in soup(['script','style','nav','footer']):tag.decompose()
            text=soup.get_text(' ',strip=True)
            # Context for corroboration. Never assigns page publication year to construction.
            coverage.append({'source':url,'status':'context_only','excerpt':text[:12000],
                             'links':list(dict.fromkeys(urljoin(url,a['href']) for a in soup.select('a[href]') if re.search(r'plan|project|permit|budget|bid|capital|agenda|\.pdf',a.get_text(' ',strip=True)+' '+a['href'],re.I)))[:75]})
        except Exception as e:
            coverage.append({'source':url,'status':'failed','error':str(e)[:400]})
    for query,category in search_queries(county):
        if not budget():
            coverage.append({'source':category,'query':query,'status':'deferred','reason':'Run budget exhausted'});continue
        try:
            batch=rss_leads(s,county,query,category);leads.extend(batch)
            coverage.append({'source':category,'query':query,'status':'search_index_only','leads':len(batch)})
        except Exception as e:
            coverage.append({'source':category,'query':query,'status':'failed','error':str(e)[:400]})
    rows=deduplicate(rows)
    archive=state.setdefault('record_archive',{})
    for row in rows:
        row['in_current_snapshot']=True
        archive[row['id']]=dict(row)
    current_ids={r['id'] for r in rows}
    for key,old in archive.items():
        if key not in current_ids:
            historic=dict(old);historic['in_current_snapshot']=False
            historic['archive_note']='Retained prior observation; not a new current-status assertion'
            historic.pop('location_candidate',None)
            rows.append(historic)
    candidates=[]
    if county['gis']:
        try:
            candidates=location_candidates(s,county,rows)
            coverage.append({'source':'county-boundary','status':'checked','location_candidates':len(candidates)})
        except Exception as e:
            coverage.append({'source':'county-boundary','status':'failed','error':str(e)[:400]})
    # Preserve each search query while deduplicating by exact URL, without conflating phases.
    unique={}
    for lead in leads:
        old=unique.setdefault(lead['id'],dict(lead,queries=[]));old['queries'].append(lead['query'])
    leads=list(unique.values());correlate(rows,leads)
    out.mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,'county_id':county['id'],'checked_at':STAMP,
        'status':'partial_coverage' if county['coverage_gaps'] or any(c['status']=='failed' for c in coverage) else 'complete_configured_sources',
        'records':rows,'leads':leads,'coverage':coverage,'known_gaps':county['coverage_gaps'],
        'publication_policy':'Review only. Verify exact project geometry, jurisdiction, phase, status and dates before adding pins. News/community cannot establish these alone.',
        'search_tasks':[{'query':q,'source_class':c} for q,c in search_queries(county)]}
    (out/(county['id']+'.review.json')).write_text(json.dumps(report,indent=2)+'\n')
    (out/(county['id']+'.pin-candidates.json')).write_text(json.dumps({'county_id':county['id'],'publication_eligible':False,'candidates':candidates},indent=2)+'\n')
    state_file.write_text(json.dumps(state,indent=2)+'\n')
    print(json.dumps({'county':county['id'],'records':len(rows),'leads':len(leads),'failed_sources':sum(c['status']=='failed' for c in coverage)}))
    # Fail visibly on broken core intake, but still upload all review evidence.
    core=[c for c in coverage if c['status'] in ('complete_listing','complete_snapshot','failed') and not str(c['source']).startswith(('http','news','public-','official-'))]
    return 1 if not core or any(c['status']=='failed' for c in core) else 0


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--county',required=True);p.add_argument('--config',type=Path,default=ROOT/'config/counties.json');p.add_argument('--out',type=Path,default=ROOT/'research-output/county-discovery');p.add_argument('--state',type=Path,default=ROOT/'research-state');p.add_argument('--history-years',type=int,default=1)
    a=p.parse_args()
    if not 1<=a.history_years<=5:p.error('history-years must be 1..5')
    counties=json.loads(a.config.read_text())['counties'];county=next(c for c in counties if c['id']==a.county)
    raise SystemExit(run(county,a.out,a.state,a.history_years))
