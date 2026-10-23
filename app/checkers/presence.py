"""Presence checks.

check_subject(db, subject):
  1. bare-name SERP (plus company name as a second query when known) -> SerpSnapshot.
     Any target domain seen here is recorded as present with its position. This
     is the cheap pass and covers most of the well-ranked pages in one or two calls.
  2. per-target site: query for targets not seen in step 1 -> Check rows.
  3. input discovery: a LinkedIn /in/ page or a team page on the company domain
     found in the SERP is recorded as an Input, so observational subjects get
     their inputs filled without a separate crawl.

Richness is scored from the SERP title/snippet only (fast); a later pass can
fetch the page and upgrade it.
"""
import re
from datetime import datetime
from sqlalchemy.orm import Session
from ..models import Subject, Target, Check, Input, SerpSnapshot
from . import dataforseo

NAME_TOKEN_MIN = 2


def _name_matches(name: str, text: str) -> bool:
    """Both surname and first token present in title/snippet (case-insensitive)."""
    if not text:
        return False
    toks = [t for t in re.split(r"\s+", name.lower()) if len(t) >= NAME_TOKEN_MIN]
    low = text.lower()
    return all(t in low for t in (toks[0], toks[-1]))


def _richness(target: Target, item: dict) -> int:
    text = f"{item.get('title','')} {item.get('description','')}".lower()
    score = 1
    if any(w in text for w in ("email", "phone", "contact", "@")):
        score += 1
    if any(w in text for w in ("director", "ceo", "founder", "manager", "officer", "partner", "at ")):
        score += 1
    return min(score, 3)


def _record(db: Session, subj: Subject, target: Target, item: dict | None, method: str,
            position: int | None = None):
    present = item is not None
    db.add(Check(subject_id=subj.id, target_key=target.key, present=present,
                 url=item["url"] if item else None,
                 richness=_richness(target, item) if item else 0,
                 indexed=present, serp_position=position, method=method,
                 raw={"title": item.get("title"), "description": item.get("description")} if item else None))
    if present and target.also_input:
        if not db.query(Input).filter_by(subject_id=subj.id, type_key=target.also_input, present=True).first():
            db.add(Input(subject_id=subj.id, type_key=target.also_input, present=True, url=item["url"]))


def _discover_inputs(db: Session, subj: Subject, results: list[dict]):
    for it in results:
        url = it["url"]
        if "linkedin.com/in/" in url and _name_matches(subj.full_name, it.get("title") or ""):
            if not db.query(Input).filter_by(subject_id=subj.id, type_key="linkedin", present=True).first():
                db.add(Input(subject_id=subj.id, type_key="linkedin", present=True, url=url))
        if subj.company_domain and subj.company_domain in it["domain"]:
            if not db.query(Input).filter_by(subject_id=subj.id, type_key="team_page", present=True).first():
                db.add(Input(subject_id=subj.id, type_key="team_page", present=True, url=url))


def check_subject(db: Session, subj: Subject, targets: list[Target] | None = None,
                  per_target: bool = True, depth: int = 30) -> dict:
    targets = targets or db.query(Target).filter_by(active=True).all()
    by_domain = {t.domain: t for t in targets}
    country = subj.country or "US"
    found: dict[str, tuple[dict, int]] = {}

    # 1. bare-name snapshot(s)
    queries = [subj.full_name]
    if subj.company_name:
        queries.append(f"{subj.full_name} {subj.company_name}")
    for q in queries:
        results = dataforseo.serp(q, country=country, depth=depth)
        db.add(SerpSnapshot(subject_id=subj.id, query=q,
                            location_code=dataforseo.LOCATIONS.get(country), results=results))
        _discover_inputs(db, subj, results)
        for it in results:
            for dom, t in by_domain.items():
                if dom in it["domain"] and re.search(t.url_pattern or "", it["url"]) \
                        and _name_matches(subj.full_name, it.get("title") or ""):
                    if t.key not in found or it["position"] < found[t.key][1]:
                        found[t.key] = (it, it["position"] if q == subj.full_name else None)

    # 2. per-target queries for the rest
    for t in targets:
        if t.key in found:
            it, pos = found[t.key]
            _record(db, subj, t, it, "serp", pos)
            continue
        if not per_target:
            _record(db, subj, t, None, "serp")
            continue
        q = t.serp_query.format(name=subj.full_name, company=subj.company_name or "",
                                domain=subj.company_domain or "")
        results = dataforseo.serp(q, country=country, depth=10)
        hit = next((it for it in results
                    if t.domain in it["domain"] and re.search(t.url_pattern or "", it["url"])
                    and _name_matches(subj.full_name, it.get("title") or "")), None)
        _record(db, subj, t, hit, "serp")
        if hit:
            found[t.key] = (hit, None)

    # 3. inputs we know are absent (so analysis has explicit negatives)
    db.flush()
    for key in ("linkedin", "team_page", "crunchbase_page", "theorg_page"):
        if not db.query(Input).filter_by(subject_id=subj.id, type_key=key).first():
            db.add(Input(subject_id=subj.id, type_key=key, present=False))
    db.commit()
    return {k: v[0]["url"] for k, v in found.items()}
