
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from openai import OpenAI

OUTPUT_FILE = Path("research_candidates.json")
MODEL = "gpt-4.1-mini"

SYSTEM_PROMPT = """
You are the BuyerBeAware construction research assistant.

Research proposed, approved, and active construction projects
in St. Johns County, Florida.

Prioritize:
- County development and planning announcements
- FDOT road and bridge projects
- Municipal construction announcements
- Commercial and residential developments
- Local news reporting on upcoming construction

Use web search to find current public information.

Rules:
1. Never invent a project, address, coordinate, or source.
2. Every candidate must include a supporting source URL.
3. Prefer official government or developer sources.
4. If coordinates cannot be verified, return null.
5. Do not treat approximate geographic locations as verified.
6. Do not publish anything to the live app.
7. Return JSON only, using this structure:

{
  "projects": [
    {
      "title": "Project name",
      "type": "Project category",
      "status": "Proposed, Approved, or Under Construction",
      "location": "Address or descriptive location",
      "latitude": null,
      "longitude": null,
      "sourceURL": "https://...",
      "summary": "Short factual description",
      "verificationStatus": "Needs Review"
    }
  ]
}
"""

def main():
    api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    client = OpenAI(api_key=api_key)

    response = client.responses.create(
        model=MODEL,
        tools=[{"type": "web_search"}],
        input=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": (
                    "Find up to 10 recent construction projects "
                    "in St. Johns County, Florida. "
                    "Prioritize projects with identifiable locations "
                    "and authoritative public sources."
                )
            }
        ]
    )

    text = response.output_text.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0].strip()

    data = json.loads(text)
    projects = data.get("projects", [])

    if not isinstance(projects, list):
        raise ValueError("Invalid projects list.")

    reviewed = []

    for project in projects:
        if not isinstance(project, dict):
            continue

        source = project.get("sourceURL", "")
        parsed = urlparse(source)

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue

        reviewed.append({
            "title": project.get("title", ""),
            "type": project.get("type", ""),
            "status": project.get("status", ""),
            "location": project.get("location", ""),
            "latitude": None,
            "longitude": None,
            "sourceURL": source,
            "summary": project.get("summary", ""),
            "verificationStatus": "Needs Review"
        })

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "region": "St. Johns County, Florida",
        "projects": reviewed
    }

    OUTPUT_FILE.write_text(
        json.dumps(output, indent=2),
        encoding="utf-8"
    )

    print(f"Research completed: {len(reviewed)} candidates.")
    print(f"Review file: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
