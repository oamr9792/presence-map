"""Companies House cohort collector.

Pulls recently incorporated active companies via the advanced search endpoint,
then their officers, and writes each natural-person director as a subject with
a ch_appointment input (effective_at = appointed_on) and company_domain if a
website can be found from the registered name later.

Needs COMPANIES_HOUSE_API_KEY (free: https://developer.company-information.service.gov.uk/).
Rate limit is 600 requests / 5 minutes; we sleep to stay under it.
"""
import os, time, random
from datetime import datetime, timedelta
import httpx
from sqlalchemy.orm import Session
from ..models import Subject, Input

BASE = "https://api.company-information.service.gov.uk"
SIC_SKIP = {"99999", "74990"}  # dormant / non-trading


def _client():
    key = os.environ["COMPANIES_HOUSE_API_KEY"]
    return httpx.Client(base_url=BASE, auth=(key, ""), timeout=30)


def _person_name(officer: dict) -> str:
    # CH gives "SURNAME, Forenames"
    n = officer.get("name", "")
    if "," in n:
        sur, fore = [p.strip() for p in n.split(",", 1)]
        return f"{fore.title()} {sur.title()}"
    return n.title()


def collect(db: Session, n_companies: int = 200, incorporated_within_days: int = 4 * 365,
            seed: int | None = None) -> int:
    """Returns number of subjects created."""
    rng = random.Random(seed)
    created = 0
    since = (datetime.utcnow() - timedelta(days=incorporated_within_days)).date().isoformat()
    with _client() as c:
        start = rng.randint(0, 5000)  # random page so repeated runs don't fetch the same companies
        r = c.get("/advanced-search/companies", params={
            "company_status": "active", "incorporated_from": since,
            "size": n_companies, "start_index": start})
        r.raise_for_status()
        items = r.json().get("items", [])
        for comp in items:
            if any(s in SIC_SKIP for s in comp.get("sic_codes", [])):
                continue
            num = comp["company_number"]
            ro = c.get(f"/company/{num}/officers", params={"items_per_page": 50})
            if ro.status_code == 429:
                time.sleep(60); ro = c.get(f"/company/{num}/officers")
            if ro.status_code != 200:
                continue
            for off in ro.json().get("items", []):
                if off.get("officer_role") not in ("director", "llp-member", "llp-designated-member"):
                    continue
                if off.get("resigned_on") or "identification" in off:   # corporate officer
                    continue
                name = _person_name(off)
                if len(name.split()) < 2:
                    continue
                ref = off.get("links", {}).get("officer", {}).get("appointments")
                if ref and db.query(Subject).filter_by(source_ref=ref).first():
                    continue
                subj = Subject(full_name=name, country="GB",
                               company_name=comp.get("company_name"),
                               title=off.get("occupation") or "Director",
                               cohort="companies_house", source_ref=ref,
                               meta={"company_number": num, "sic": comp.get("sic_codes"),
                                     "incorporated": comp.get("date_of_creation"),
                                     "appointed_on": off.get("appointed_on"),
                                     "age_days": (datetime.utcnow() - datetime.fromisoformat(off["appointed_on"])).days if off.get("appointed_on") else None})
                db.add(subj); db.flush()
                eff = off.get("appointed_on")
                db.add(Input(subject_id=subj.id, type_key="ch_appointment", present=True,
                             url=f"https://find-and-update.company-information.service.gov.uk{ref}" if ref else None,
                             effective_at=datetime.fromisoformat(eff) if eff else None))
                created += 1
            time.sleep(0.6)
    db.commit()
    return created
