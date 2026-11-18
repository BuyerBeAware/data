#!/usr/bin/env python3
"""BBA discovery + corroboration + cautious geolocation review pipeline.
Never publishes directly to projects.json.
"""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup
from bba_road_sources import run as discover_official_roads
from bba_rail_sources import run as discover_rail_sources
from bba_approval_sources import run as discover_approval_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'research-output'
OUT.mkdir(exist_ok=True)
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'BuyerBeAwareResearch/0.1 (+public project research; contact repository owner)'})
TIMEOUT = 18
MAX_PAGES = int(os.getenv('BBA_MAX_PAGES', '35'))
SEEDS = [
    'https://nflroads.com/ProjectList.aspx',
    'https://nflroads.com/FirstCoastExpressway.aspx',
    'https://nflroads.com/ProjectDetails?p=5605',
    'https://nflroads.com/ProjectDetails.aspx?f=4&p=5639',
]
NEWS_QUERIES = [
    'First Coast Expressway CR 2209 I-95 construction',
    'St Johns County FDOT road construction projects',
    'US 1 construction St Augustine future road projects',
    'CR 210 St Johns County future road improvements FDOT',
    'St Johns County railroad crossing improvements Florida East Coast Railway projects',
    'Jacksonville St Augustine regional rail JTA planning',
    'Shands Bridge replacement First Coast Expressway SR 23 construction',
    'First Coast Expressway St Johns River Crossing record of decision federal approval',
    'Florida highway bridge USACE Coast Guard permit environmental approval',
    'Florida FDOT highway PD&E future planned completed projects',
]
PROJECT_ID = re.compile(r'\b\d{6}-\d(?:-\d)?\b')
ROAD = re.compile(r'\b(?:SR|CR|US|I)[- ]?\d+[A-Za-z]?\b', re.I)
PROJECT_WORDS = re.compile(r'project|construction|expressway|widening|interchange|road|bridge|extension', re.I)


def fetch(url):
    try:
        r = SESSION.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        if 'html' not in r.headers.get('Content-Type', '').lower() and 'xml' not in r.headers.get('Content-Type', '').lower():
            return None
        return r.text[:1_000_000]
    except requests.RequestException as exc:
        print(f'WARN fetch {url}: {exc}')
        return None


def extract(url, html):
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer']):
        tag.decompose()
    text = ' '.join(soup.stripped_strings)[:18000]
    title = soup.title.get_text(' ', strip=True) if soup.title else url
    links = []
    for a in soup.find_all('a', href=True):
        href = urljoin(url, a['href'])
        if urlparse(href).scheme not in ('http', 'https'):
            continue
        if urlparse(href).netloc.lower() != urlparse(url).netloc.lower():
            continue
        label = a.get_text(' ', strip=True)
        if 'ProjectDetails' in href or PROJECT_WORDS.search(label):
            links.append(href)
    return {'url': url, 'title': title, 'text': text, 'links': sorted(set(links))[:80]}


def news_sources():
    """Google News RSS provides leads only, not geographic proof."""
    from xml.etree import ElementTree as ET
    leads = []
    for query in NEWS_QUERIES:
        url = 'https://news.google.com/rss/search?q=' + quote(query) + '&hl=en-US&gl=US&ceid=US:en'
        xml = fetch(url)
        if not xml:
            continue
        try:
            for item in ET.fromstring(xml).findall('.//item')[:12]:
                title = item.findtext('title', '')
                link = item.findtext('link', '')
                if title and link:
                    leads.append({'title': title, 'url': link, 'query': query, 'source_type': 'news_lead'})
        except ET.ParseError:
            pass
    return leads


def discover():
    queue = list(SEEDS)
    seen = set()
    pages = []
    while queue and len(seen) < MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        html = fetch(url)
        if not html:
            continue
        page = extract(url, html)
        pages.append(page)
        for link in page['links']:
            if link not in seen and link not in queue and len(queue) < 200:
                queue.append(link)
        time.sleep(0.25)
    return pages


def match_project(pages):
    """Group official pages by explicit FDOT ID; don't invent an ID from a name."""
    projects = {}
    for page in pages:
        ids = sorted(set(PROJECT_ID.findall(page['text'])))
        for pid in ids:
            entry = projects.setdefault(pid, {'project_id': pid, 'official_sources': [], 'road_mentions': [], 'titles': []})
            entry['official_sources'].append(page['url'])
            entry['titles'].append(page['title'])
            entry['road_mentions'].extend(ROAD.findall(page['text']))
    for entry in projects.values():
        for key in ('official_sources', 'road_mentions', 'titles'):
            entry[key] = sorted(set(entry[key]))
    return list(projects.values())


def ai_review(projects, pages, news, road_projects=None, rail_projects=None, approvals=None):
    """Optional web-enabled AI investigator. Outputs research notes, never coordinates as fact."""
    if not os.getenv('OPENAI_API_KEY') or not (projects or road_projects or rail_projects):
        return {'enabled': False, 'notes': 'AI investigation skipped (missing key or no identified project IDs)'}
    try:
        from openai import OpenAI
        client = OpenAI(timeout=75, max_retries=1)
        approval_context = {'programs': [
            {k: p[k] for k in ('id','title','source_url','historical_completion_evidence')} for p in (approvals or {}).get('programs',[])],
            'notices': [{k: n.get(k) for k in ('id','title','source_url','publication_date','approval_status','financial_project_ids','current_construction_status')} for n in (approvals or {}).get('notices',[])]}
        context = {'approval_history': approval_context, 'projects': projects[:12],
                   'official_page_excerpts': [{'url': p['url'], 'text': p['text'][:2200]} for p in pages[:15]],
                   'news_leads': news[:12], 'structured_official_road_projects': (road_projects or [])[:30],
                   'structured_official_rail_projects': (rail_projects or [])[:20]}
        response = client.responses.create(
            model=os.getenv('BBA_AI_MODEL', 'gpt-4.1-mini'),
            tools=[{'type': 'web_search'}],
            input=[
                {'role': 'system', 'content': 'Investigate Florida major roads and bridges across county boundaries, including completed, current and planned projects. Separate program, segment, approval and construction status. Never use a historic approval date or legal challenge deadline as a construction date. Never associate a multi-project notice with one project without explicit evidence. Identify corroborating primary source URLs, precise site address/road endpoints, FDOT IDs, conflicts, and missing evidence. Never guess latitude/longitude. Never claim a source was checked unless actually checked. Return concise text, clearly distinguishing verified from suggested.'},
                {'role': 'user', 'content': json.dumps(context)[:28000]},
            ],
        )
        return {'enabled': True, 'model': os.getenv('BBA_AI_MODEL', 'gpt-4.1-mini'), 'notes': response.output_text[:25000]}
    except Exception as exc:
        return {'enabled': False, 'error': str(exc)}


def main():
    approval_report = discover_approval_sources(ROOT, max_details=int(os.getenv("BBA_MAX_APPROVAL_DETAILS", "250")))
    segments = [s for p in approval_report['programs'] for s in p['segments']]
    road_report = discover_official_roads(ROOT, max_details=int(os.getenv("BBA_MAX_ROAD_DETAILS", "250")), extra_listings=segments)
    # Link by an exact source identifier only. Retain notices separately because one can cover many roads.
    for notice in approval_report['notices']:
        notice['related_road_ids'] = sorted({p['id'] for p in road_report['projects']
            if set(p.get('financial_project_ids', [])).intersection(notice['financial_project_ids'])})
    rail_report = discover_rail_sources(ROOT)
    pages = discover()
    news = news_sources()
    projects = match_project(pages)
    reviewed = []
    for p in projects:
        reviewed.append({
            **p,
            'latitude': None,
            'longitude': None,
            'geometry': None,
            'location_status': 'unverified',
            'publication_allowed': False,
            'review_reason': 'Official project geometry or parcel/site coordinates not yet corroborated',
        })
    report = {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'review_only': True,
        'publication_allowed': False,
        'official_pages_checked': len(pages),
        'project_candidates': reviewed,
        'news_leads': news,
        'road_discovery_status': road_report['status'],
        'road_projects': road_report['projects'],
        'rail_discovery_status': rail_report['status'],
        'rail_projects': rail_report['projects'],
        'approval_discovery_status': approval_report['status'],
        'road_programs': approval_report['programs'],
        'approval_notices': approval_report['notices'],
        'approval_coverage': approval_report['coverage'],
        'ai_investigation': ai_review(projects, pages, news, road_report['projects'], rail_report['projects'],
            {'programs': approval_report['programs'], 'notices': approval_report['notices'][:12]}),
    }
    (OUT / 'review_queue.json').write_text(json.dumps(report, indent=2) + '\n')
    (OUT / 'source_pages.json').write_text(json.dumps([{'url': p['url'], 'title': p['title'], 'project_ids': sorted(set(PROJECT_ID.findall(p['text'])))} for p in pages], indent=2) + '\n')
    print(f"Checked {len(pages)} official pages; {len(projects)} project IDs; {len(news)} news leads")
    print('Outputs: research-output/review_queue.json, research-output/source_pages.json')
    print('SAFETY: live projects.json was NOT modified')
    print(f"Official road discovery: {road_report['status']}; {road_report['project_count']} projects")
    print(f"Official rail discovery: {rail_report['status']}; {len(rail_report['projects'])} records")
    if approval_report['status'] != 'completed':
        raise SystemExit('Major-road approvals incomplete; inspect major_road_approvals.review.json')
    if rail_report['status'] != 'completed':
        raise SystemExit('Official rail discovery incomplete; inspect rail_projects.review.json')
    if road_report['status'] != 'completed':
        raise SystemExit('Official road discovery incomplete; inspect road_projects.review.json')


if __name__ == '__main__':
    main()
