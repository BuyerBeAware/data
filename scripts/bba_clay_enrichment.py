"""Exact case -> official staff report -> exact parcel -> existing point verification.
No guessed tenant, construction dates, coordinate moves or approval-by-recommendation.
"""
from io import BytesIO
import re
from urllib.parse import urlencode,urlsplit
from pypdf import PdfReader
from bba_county_discovery import inside,request_json

PARCEL='https://maps.claycountygov.com/server/rest/services/EnerGov_CSS_Dev_Map/MapServer/7'

def extract(text,case):
    normalized=lambda t:re.sub(r'\s+',' ',t).strip()
    # Exact case boundary; ZON 25-0038 must never match ZON 25-00380.
    pattern=r'(?<![A-Z0-9])'+re.escape(normalized(case)).replace(r'\ ',r'\s+')+r'(?![A-Z0-9-])'
    if not re.search(pattern,normalized(text),re.I):raise ValueError('Staff report case identity mismatch')
    # Restrict to project-specific property information, excluding generic zoning code.
    section=re.search(r'Property\s+Information(.*?)(?:Introduction|Analysis of Surrounding)',text,re.I|re.S)
    if not section:raise ValueError('Missing project property section')
    value=normalized(section.group(1))
    parcels=set(re.findall(r'\b\d{2}-\d{2}-\d{2}-\d{6}-\d{3}-\d{2}\b',value))
    address=re.search(r'Parcel Address:\s*(.+?)(?:Current Zoning:|Current Land Use:|Proposed Zoning:)',value,re.I)
    if len(parcels)!=1 or not address:raise ValueError('Ambiguous/missing parcel or site address')
    zones=re.search(r'Current Zoning:\s*(\S+).*?Proposed Zoning:\s*(\S+)',value,re.I)
    if not zones:raise ValueError('Missing zoning classifications')
    return {'parcelID':next(iter(parcels)),'siteAddress':address.group(1).strip(),'currentZoning':zones.group(1),'newZoning':zones.group(2),'recommendationIsApproval':False}

def enrich(row,document,s,stamp):
    url=document['url'];parts=urlsplit(url)
    if parts.scheme!='https' or parts.netloc!='claycounty.novusagenda.com' or parts.path!='/agendapublic/AttachmentViewer.ashx':raise ValueError('Untrusted document source')
    if row['projectId']!=document['recordID'] or row.get('countyID')!='FL-12019':raise ValueError('Wrong record for document')
    response=s.get(url,timeout=(12,45));response.raise_for_status()
    if len(response.content)>10_000_000 or not response.content.startswith(b'%PDF'):raise ValueError('Invalid staff PDF')
    reader=PdfReader(BytesIO(response.content))
    if not 1<=len(reader.pages)<=100:raise ValueError('Invalid PDF page count')
    # Identity + property fields reside on the first page, not countywide regulations.
    facts=extract(reader.pages[0].extract_text(),document['caseID'])
    query={'where':"ParcelDisp='%s'"%facts['parcelID'],'outFields':'OBJECTID,ParcelDisp,HouseNo,StreetName,StreetMd,StreetCity,TotalAcres','returnGeometry':'true','outSR':4326,'f':'json'}
    data=request_json(s,PARCEL+'/query',query);features=data.get('features',[])
    if data.get('exceededTransferLimit') or len(features)!=1:raise ValueError('Missing/ambiguous exact parcel')
    feature=features[0];a=feature['attributes']
    if a['ParcelDisp']!=facts['parcelID'] or not inside((row['longitude'],row['latitude']),feature['geometry'].get('rings',[])):raise ValueError('Existing point outside exact document parcel; no automatic move')
    number=re.match(r'\d+',facts['siteAddress'])
    if not number or number.group(0)!=str(a['HouseNo']).strip():raise ValueError('Staff/parcel site-address conflict')
    def canonical(value):
        value=re.sub(r'\bCR\b','COUNTY ROAD',value.upper())
        value=re.sub(r'\bRD\b','ROAD',value)
        return re.sub(r'[^A-Z0-9]','',value)
    parcel_address=' '.join(str(a.get(k) or '') for k in ('HouseNo','StreetName','StreetMd'))
    if canonical(facts['siteAddress'])!=canonical(parcel_address):raise ValueError('Staff/parcel street-address conflict')
    # Preserve every pin coordinate/identity and unknown work schedule.
    result=dict(row)
    result.update(title=f"{facts['siteAddress']} — zoning {facts['currentZoning']} to {facts['newZoning']} ({document['caseID']})",
        recordStage='zoning_record_documented',
        details=f"County case {document['caseID']} identifies the property at {facts['siteAddress']}, {a['StreetCity']} (parcel area: {a['TotalAcres']} acres). The staff report identifies parcel {facts['parcelID']} and a zoning change from {facts['currentZoning']} to {facts['newZoning']}. A specific building, tenant and physical-work schedule have not been established by these sources.",
        status=f"Zoning change {facts['currentZoning']} to {facts['newZoning']}; construction status not verified",
        locationEstimateBasis=f"The county staff report names parcel {facts['parcelID']}. Its exact county parcel boundary contains this existing pin, and the site number matches the parcel address. The pin identifies the property, not a surveyed building/work footprint.",
        coordinateVerification='exact_case_document_parcel_contains_existing_point',
        evidenceConfidence='official_case_document_and_exact_parcel_match',locationReviewedAt=stamp,
        locationEvidenceURLs=list(dict.fromkeys([url]+row.get('locationEvidenceURLs',[])+[PARCEL+'/query?'+urlencode(query)])))
    return result
