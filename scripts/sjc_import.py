#!/usr/bin/env python3
"""Import official St. Johns tracker records into separate review/test files.

No AI, no API key, no edits to projects.json. Python 3.9+; standard library only.
"""
import argparse
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
LAYER = ("https://services1.arcgis.com/t2yugAJW83eUIFui/arcgis/rest/services/"
         "WATS_Project_Point/FeatureServer/0")
TRACKER = "https://www.sjcfl.us/development-tracker/"
APP = "https://sjcfl.maps.arcgis.com/apps/instant/basic/index.html"
APP_ID = "3fb1ca4c55be457bb294678067b8c9e6"
FIELDS = ("OBJECTID", "PROJECT_NUM", "PROJ_LABEL", "PROJECT_NAME", "LOCATION",
          "PROJECT_DESCRIPTION", "TYPE", "RESULT", "PZA_DATE", "NUMBER_LOTS", "SQUARE_FT")


class ImportErrorSafe(Exception):
    pass


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, text):
        self.parts.append(text)


def text(value):
    if value is None:
        return ""
    parser = TextOnly()
    parser.feed(unescape(str(value)))
    # Plain text only, including when county text has escaped markup.
    return " ".join(" ".join(parser.parts).replace("<", "").replace(">", "").split())


def allowed(url):
    p = urlsplit(url)
    return (p.scheme == "https" and p.hostname == "services1.arcgis.com"
            and p.port in (None, 443) and p.username is None and p.password is None
            and p.path.startswith("/t2yugAJW83eUIFui/arcgis/rest/services/WATS_Project_Point/"))


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise ImportErrorSafe("The GIS service redirected outside its approved source.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(params, endpoint=LAYER + "/query"):
    url = endpoint + "?" + urlencode(dict(params, f="json"))
    if not allowed(url):
        raise ImportErrorSafe("Unapproved GIS URL.")
    opener = build_opener(OfficialRedirects())
    for attempt in range(3):
        try:
            with opener.open(Request(url, headers={
                    "User-Agent": "BuyerBeAware/1.0 (public project research)",
                    "Accept": "application/json"}), timeout=30) as response:
                body = response.read(8_000_001)
            if len(body) > 8_000_000:
                raise ImportErrorSafe("GIS response exceeded the size limit.")
            data = json.loads(body)
            if not isinstance(data, dict) or "error" in data:
                raise ImportErrorSafe("GIS service returned an error instead of project data.")
            return data
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(1 + attempt)


def collect(get=fetch):
    ids_data = get({"where": "1=1", "returnIdsOnly": "true"})
    ids = ids_data.get("objectIds")
    if not isinstance(ids, list) or any(type(i) is not int for i in ids):
        raise ImportErrorSafe("GIS did not return a valid project ID list.")
    ids = sorted(set(ids))
    if len(ids) > 20000:
        raise ImportErrorSafe("Unexpectedly large project list; inspect source before importing.")
    records, returned = [], set()
    for offset in range(0, len(ids), 100):
        chunk = ids[offset:offset + 100]
        data = get({"objectIds": ",".join(map(str, chunk)), "outFields": ",".join(FIELDS),
                    "returnGeometry": "true", "outSR": "4326"})
        sr = data.get("spatialReference") or {}
        if data.get("geometryType") != "esriGeometryPoint" or sr.get("wkid") != 4326:
            raise ImportErrorSafe("GIS coordinates are not WGS84 points; no pins generated.")
        features = data.get("features")
        if not isinstance(features, list) or data.get("exceededTransferLimit"):
            raise ImportErrorSafe("GIS batch is missing or truncated.")
        batch_ids = []
        for feature in features:
            oid = (feature.get("attributes") or {}).get("OBJECTID")
            if type(oid) is not int:
                raise ImportErrorSafe("GIS record has an invalid object ID.")
            batch_ids.append(oid)
        if set(batch_ids) != set(chunk) or len(batch_ids) != len(set(batch_ids)):
            raise ImportErrorSafe("GIS batch did not match requested IDs; try again.")
        records.extend(features)
        returned.update(batch_ids)
    if returned != set(ids):
        raise ImportErrorSafe("Project download was incomplete.")
    return records


def normalize(features):
    candidates, test_projects, skipped, seen = [], [], [], {}
    for feature in features:
        a = feature.get("attributes") or {}
        number, title = text(a.get("PROJECT_NUM")), text(a.get("PROJECT_NAME"))
        oid = a.get("OBJECTID")
        if not number or not title or not text(a.get("PROJECT_DESCRIPTION")):
            skipped.append({"object_id": oid, "reason": "missing ID, name or description"})
            continue
        key = number.upper()
        if not re.fullmatch(r"[A-Z0-9()_-]{3,80}", key):
            raise ImportErrorSafe("Unexpected project number format; inspect the source.")
        geometry = feature.get("geometry") or {}
        signature = {"attributes": {k: v for k, v in a.items() if k not in {"OBJECTID", "ORIG_FID"}},
                     "geometry": geometry}
        if key in seen:
            previous, candidate = seen[key]
            if previous != signature:
                raise ImportErrorSafe("One application has conflicting records or locations; inspect before merging.")
            candidate["source_object_ids"].append(oid)
            skipped.append({"object_id": oid, "reason": "identical duplicate application row merged"})
            continue
        lng, lat = geometry.get("x"), geometry.get("y")
        valid = (type(lat) in (int, float) and type(lng) in (int, float)
                 and math.isfinite(lat) and math.isfinite(lng)
                 and 29.3 <= lat <= 30.4 and -81.8 <= lng <= -81.0)
        if not valid:
            lat = lng = None
        label = text(a.get("PROJ_LABEL")) or number
        source = APP + "?" + urlencode({"appid": APP_ID, "find": label})
        record_url = LAYER + "/query?" + urlencode({
            "where": "PROJECT_NUM='" + number + "'", "outFields": ",".join(FIELDS),
            "returnGeometry": "true", "outSR": "4326", "f": "pjson"})
        result = text(a.get("RESULT"))
        status = result or "Application listed; decision not provided"
        meetings = text(a.get("PZA_DATE"))
        description = text(a.get("PROJECT_DESCRIPTION"))
        # Preserve application wording; a request is never rewritten as construction.
        short = description if len(description) <= 600 else description[:597].rsplit(" ", 1)[0] + "..."
        candidate = {
            "id": "sjc-" + key, "project_id": number, "name": title,
            "location": text(a.get("LOCATION")), "summary": short,
            "full_description": description, "status": status,
            "application_type": text(a.get("TYPE")), "meeting_dates": meetings,
            "number_lots": text(a.get("NUMBER_LOTS")),
            "nonresidential_sqft": text(a.get("SQUARE_FT")),
            "source_url": source, "source_record_url": record_url,
            "source_directory_url": TRACKER, "source_object_id": oid, "source_object_ids": [oid],
            "lat": lat, "lng": lng,
            "coordinate_source": "official GIS point (WGS84)" if valid else None,
            "review_status": "needs_human_review", "publication_allowed": False,
        }
        candidates.append(candidate)
        seen[key] = (signature, candidate)
        if not valid:
            skipped.append({"object_id": oid, "reason": "no valid county GIS point; excluded from map test"})
            continue
        test_projects.append({
            "title": title, "type": "Development Application", "status": status,
            "plannedYear": "Not provided", "latitude": lat, "longitude": lng,
            "details": short, "schedule": "Meeting dates: " + meetings if meetings else "Not provided",
            "projectId": number, "sourceURL": source,
            "locationSourceKind": "Official county GIS representative point",
            "locationSourceRef": record_url,
            "reviewOnly": True, "publicationAllowed": False,
        })
    return candidates, test_projects, skipped


def write(path, payload):
    # All outputs have separate filenames; projects.json is never a destination.
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n", "utf-8")
    temp.replace(path)


def run(output, get=fetch):
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "scope": "St. Johns County development tracker point layer",
              "source_url": TRACKER, "source_layer": LAYER,
              "review_only": True, "publication_allowed": False,
              "status": "failed", "candidates": []}
    projects, code = [], 1
    try:
        features = collect(get)
        candidates, projects, skipped = normalize(features)
        report.update(status="completed", fetched_count=len(features), candidates=candidates,
                      map_test_count=len(projects), skipped=skipped,
                      coverage="Complete ID list returned by this point layer at retrieval; "
                               "not all county projects or all application history.")
        code = 0
    except Exception as exc:
        report["error"] = str(exc) if isinstance(exc, ImportErrorSafe) else type(exc).__name__
        print("St. Johns import failed: " + report["error"], file=sys.stderr)
    write(output / "sjc_candidates.json", report)
    write(output / "sjc_projects.test.json", projects)
    print(f"St. Johns import: {report['status']}; {len(projects)} map test records")
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "research-output")
    args = parser.parse_args()
    sys.exit(run(args.output_dir))
