"""US officer cohort via OpenCorporates.

OpenCorporates aggregates state registries and exposes officers through its API
(key required; free tier for non-commercial, paid otherwise:
https://opencorporates.com/api_accounts/new). We pull officers for recently
incorporated companies in a given US jurisdiction and write each as a subject
with a us_officer_filing input.

Jurisdiction codes: us_fl, us_ny, us_ca, us_de (DE publishes no officers), us_tx...
Florida and New York publish officer names; start there.
"""
import os, time, random
from datetime import datetime, timedelta
import httpx
from sqlalchemy.orm import Session
from ..models import Subject, Input

BASE = "https://api.opencorporates.com/v0.4"
CORPORATE_HINTS = ("LLC", "INC", "CORP", "LTD", "L.L.C", "COMPANY", "TRUST", "SERVICES", "AGENT")


def collect(db: Session, jurisdiction: str = "us_fl", n_companies: int = 100,
            incorporated_within_days: int = 365, seed: int | None = None) -> int:
    token = os.environ["OPENCORPORATES_API_TOKEN"]
    rng = random.Random(seed)
    created = 0
    since = (datetime.utcnow() - timedelta(days=incorporated_within_days)).date().isoformat()
    with httpx.Client(timeout=30) as c:
        page = rng.randint(1, 20)
        r = c.get(f"{BASE}/companies/search", params={
            "api_token": token, "jurisdiction_code": jurisdiction,
            "incorporation_date": f"{since}:", "current_status": "Active",
            "per_page": min(n_companies, 100), "page": page, "order": "incorporation_date"})
        r.raise_for_status()
        for wrap in r.json()["results"]["companies"]:
            comp = wrap["company"]
            ro = c.get(f"{BASE}/companies/{jurisdiction}/{comp['company_number']}",
                       params={"api_token": token})
            if ro.status_code != 200:
                continue
            detail = ro.json()["results"]["company"]
            for ow in detail.get("officers", []):
                off = ow["officer"]
                name = (off.get("name") or "").strip()
                if not name or off.get("inactive"):
                    continue
                if any(h in name.upper() for h in CORPORATE_HINTS) or len(name.split()) < 2:
                    continue
                ref = off.get("opencorporates_url")
                if ref and db.query(Subject).filter_by(source_ref=ref).first():
                    continue
                subj = Subject(full_name=name.title(), country="US",
                               company_name=comp.get("name"),
                               title=off.get("position") or "Officer",
                               cohort="opencorporates_us", source_ref=ref,
                               meta={"jurisdiction": jurisdiction,
                                     "company_number": comp.get("company_number"),
                                     "incorporated": comp.get("incorporation_date"),
                                     "start_date": off.get("start_date")})
                db.add(subj); db.flush()
                sd = off.get("start_date")
                db.add(Input(subject_id=subj.id, type_key="us_officer_filing", present=True,
                             url=ref, effective_at=datetime.fromisoformat(sd) if sd else None))
                created += 1
            time.sleep(1.0)
    db.commit()
    return created
