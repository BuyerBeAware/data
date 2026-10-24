#!/usr/bin/env python3
"""Collect official-source links for human review; never update project data."""

import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


# Small starter scope, not comprehensive statewide coverage.
SOURCES = (
    ("FDOT", "https://www.fdot.gov/projects"),
    ("Hillsborough County", "https://hcfl.gov/government/county-projects/cip-project-resources"),
)
ALLOWED_HOSTS = {"fdot.gov", "www.fdot.gov", "hcfl.gov", "www.hcfl.gov"}
MAX_BYTES = 1_000_000
MAX_LEADS_PER_SOURCE = 20
KEYWORDS = re.compile(
    r"\b(projects?|construction|widening|intersection|resurfacing|bridge|roadway|corridor)\b",
    re.IGNORECASE,
)


def allowed_url(url):
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname in ALLOWED_HOSTS
        and parts.port in (None, 443)
        and not parts.username
        and not parts.password
    )


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise ValueError("Redirect outside approved official HTTPS hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.href = None
        self.label = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.label = []

    def handle_data(self, data):
        if self.href is not None:
            self.label.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            label = " ".join(" ".join(self.label).split())
            self.links.append((self.href, label))
            self.href = None
            self.label = []


def main():
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "review_only": True,
        "publication_allowed": False,
        "scope": "Official directory link leads; linked pages have not been verified.",
        "sources": [],
        "candidates": [],
    }
    opener = build_opener(OfficialRedirects())
    seen = set()
    for publisher, source_url in SOURCES:
        source = {"publisher": publisher, "requested_url": source_url}
        report["sources"].append(source)
        try:
            request = Request(source_url, headers={
                "User-Agent": "BuyerBeAware-Research/0.1 (review-only public-source discovery)",
                "Accept": "text/html",
            })
            with opener.open(request, timeout=20) as response:
                if response.headers.get_content_type() != "text/html":
                    raise ValueError("Expected HTML")
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("Source exceeds size limit")
                final_url = response.geturl()
                encoding = response.headers.get_content_charset() or "utf-8"
            parser = Links()
            parser.feed(body.decode(encoding, errors="replace"))
            source.update({
                "status": "fetched",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "final_url": final_url,
                "content_sha256": hashlib.sha256(body).hexdigest(),
            })
            count = 0
            for href, label in parser.links:
                target = urldefrag(urljoin(final_url, href))[0]
                if not label or not KEYWORDS.search(label) or not allowed_url(target):
                    continue
                if target == final_url or target in seen:
                    continue
                seen.add(target)
                report["candidates"].append({
                    "id": hashlib.sha256(target.encode()).hexdigest()[:16],
                    "link_text": label[:500],
                    "candidate_url": target,
                    "publisher": publisher,
                    "evidence_source_url": final_url,
                    "evidence_type": "directory_anchor_text",
                    "review_status": "unverified_link_lead",
                    "publication_allowed": False,
                    "review_required": [
                        "Open linked official source and confirm a specific project",
                        "Confirm location, scope, current status, and source date",
                        "Check existing projects.json for duplicates",
                        "Verify coordinates independently before any pin is created",
                    ],
                })
                count += 1
                if count >= MAX_LEADS_PER_SOURCE:
                    break
            source["lead_count"] = count
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, LookupError) as exc:
            # Save failures for review; never fabricate results or hide partial coverage.
            source.update({"status": "failed", "error_type": type(exc).__name__})

    fetched = [s for s in report["sources"] if s["status"] == "fetched"]
    report["coverage_status"] = (
        "complete" if len(fetched) == len(SOURCES) else "partial" if fetched else "failed"
    )
    output = Path("research-output")
    output.mkdir(exist_ok=True)
    (output / "candidates.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    print(f"Review-only report: {len(report['candidates'])} leads; coverage={report['coverage_status']}")
    # A changed/blocked source or empty extraction needs attention, even with an artifact.
    return 0 if len(fetched) == len(SOURCES) and all(s["lead_count"] for s in fetched) else 1


if __name__ == "__main__":
    raise SystemExit(main())
