"""DataForSEO SERP client. Needs DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD.

Live advanced endpoint, one query per call. Cost is per SERP; at ~40 targets
per subject this is the main running cost, so results are cached in the DB and
a subject is only re-checked on the schedule in jobs.py.
"""
import os
from urllib.parse import urlparse
import httpx

ENDPOINT = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
LOCATIONS = {"GB": 2826, "US": 2840, "IL": 2376, "DE": 2276, "FR": 2250}


def _auth():
    return (os.environ["DATAFORSEO_LOGIN"], os.environ["DATAFORSEO_PASSWORD"])


def serp(keyword: str, country: str = "US", depth: int = 20) -> list[dict]:
    """Returns [{position, url, domain, title, description}] for organic results."""
    payload = [{"keyword": keyword, "location_code": LOCATIONS.get(country, 2840),
                "language_code": "en", "device": "desktop", "depth": depth}]
    with httpx.Client(timeout=60) as c:
        r = c.post(ENDPOINT, auth=_auth(), json=payload)
        r.raise_for_status()
        data = r.json()
    out = []
    for task in data.get("tasks", []):
        for res in task.get("result") or []:
            for item in res.get("items") or []:
                if item.get("type") != "organic":
                    continue
                url = item.get("url") or ""
                out.append({"position": item.get("rank_absolute"), "url": url,
                            "domain": urlparse(url).netloc.replace("www.", ""),
                            "title": item.get("title"), "description": item.get("description")})
    return out
