"""FastAPI app. Serves JSON for the UI and the static UI itself."""
import os
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func
from .db import init_db, get_session
from .models import Subject, Target, InputType, Input, Check, Edge, TargetStat
from .analysis.compute import latest_presence
from .analysis.planner import plan as make_plan
from . import runner
from fastapi import Body

app = FastAPI(title="presence map")
WEB = Path(__file__).resolve().parent.parent / "web"


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/api/meta")
def meta(db: Session = Depends(get_session)):
    return {
        "targets": [dict(key=t.key, label=t.label, domain=t.domain, category=t.category,
                         region=t.region, also_input=t.also_input) for t in db.query(Target).filter_by(active=True)],
        "inputs": [dict(key=i.key, label=i.label, controllable=i.controllable, noise=i.noise,
                        region=i.region, description=i.description) for i in db.query(InputType)],
        "cohorts": [dict(cohort=c, n=n) for c, n in db.query(Subject.cohort, func.count()).group_by(Subject.cohort)],
        "n_checks": db.query(Check).count(),
    }


@app.get("/api/graph")
def graph(min_n: int = 5, min_lift: float = 1.2, db: Session = Depends(get_session)):
    """Nodes + edges for the board view."""
    stats = {s.target_key: s for s in db.query(TargetStat)}
    edges = [e for e in db.query(Edge) if e.n_with >= min_n and e.lift and e.lift >= min_lift]
    return {
        "targets": [dict(key=t.key, label=t.label, category=t.category, region=t.region,
                         presence_rate=stats[t.key].presence_rate if t.key in stats else 0,
                         page_one_rate=stats[t.key].page_one_rate if t.key in stats else 0,
                         median_position=stats[t.key].median_position if t.key in stats else None)
                    for t in db.query(Target).filter_by(active=True)],
        "inputs": [dict(key=i.key, label=i.label, controllable=i.controllable, noise=i.noise)
                   for i in db.query(InputType)],
        "edges": [dict(input=e.input_key, target=e.target_key, p_with=e.p_with, p_without=e.p_without,
                       lift=e.lift, n=e.n_with, lag=e.median_lag_days, n_lag=e.n_lag) for e in edges],
    }


@app.get("/api/trigger-table")
def trigger_table(db: Session = Depends(get_session)):
    return [dict(input=e.input_key, target=e.target_key, n_with=e.n_with, n_without=e.n_without,
                 p_with=e.p_with, p_without=e.p_without, lift=e.lift,
                 lag=e.median_lag_days, n_lag=e.n_lag) for e in db.query(Edge)]


@app.get("/api/subjects")
def subjects(cohort: str | None = None, q: str | None = None, limit: int = 200, offset: int = 0,
             db: Session = Depends(get_session)):
    query = db.query(Subject)
    if cohort:
        query = query.filter_by(cohort=cohort)
    if q:
        query = query.filter(Subject.full_name.ilike(f"%{q}%"))
    total = query.count()
    rows = query.order_by(Subject.id.desc()).offset(offset).limit(limit).all()
    pres = latest_presence(db)
    out = []
    for s in rows:
        p = pres.get(s.id, {})
        out.append(dict(id=s.id, name=s.full_name, country=s.country, company=s.company_name,
                        title=s.title, cohort=s.cohort, is_client=s.is_client,
                        inputs=[i.type_key for i in s.inputs if i.present],
                        pages=[k for k, c in p.items() if c.present],
                        n_checked=len(p),
                        last_checked=max((c.checked_at for c in p.values()), default=None)))
    return {"total": total, "items": out}


@app.get("/api/subjects/{sid}")
def subject(sid: int, db: Session = Depends(get_session)):
    s = db.get(Subject, sid)
    if not s:
        raise HTTPException(404)
    pres = latest_presence(db).get(sid, {})
    return dict(id=s.id, name=s.full_name, country=s.country, company=s.company_name,
                domain=s.company_domain, title=s.title, cohort=s.cohort, meta=s.meta,
                inputs=[dict(key=i.type_key, present=i.present, url=i.url, effective_at=i.effective_at)
                        for i in s.inputs],
                pages=[dict(target=k, present=c.present, url=c.url, richness=c.richness,
                            position=c.serp_position, checked_at=c.checked_at) for k, c in pres.items()],
                plan=make_plan(db, sid))


@app.get("/api/plan/{sid}")
def plan(sid: int, max_noise: int = 3, db: Session = Depends(get_session)):
    return make_plan(db, sid, max_noise=max_noise)


@app.get("/api/simulate")
def simulate(inputs: str = Query("", description="comma-separated input keys the person has"),
             pages: str = Query("", description="comma-separated target keys already present"),
             country: str | None = None, max_noise: int = 3, db: Session = Depends(get_session)):
    """Plan for a hypothetical person who isn't in the DB (the 'client with nothing' case)."""
    tmp = Subject(full_name="(hypothetical)", cohort="_tmp", country=country or None)
    db.add(tmp); db.flush()
    for k in filter(None, inputs.split(",")):
        db.add(Input(subject_id=tmp.id, type_key=k.strip(), present=True))
    for k in filter(None, pages.split(",")):
        db.add(Check(subject_id=tmp.id, target_key=k.strip(), present=True, method="manual"))
    db.flush()
    try:
        return make_plan(db, tmp.id, max_noise=max_noise)
    finally:
        db.rollback()


@app.get("/api/jobs")
def jobs_status():
    st = runner.status()
    st["env"] = {k: bool(os.getenv(k)) for k in ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "COMPANIES_HOUSE_API_KEY")}
    return st


@app.post("/api/jobs/{name}")
def jobs_start(name: str, params: dict = Body(default={})):
    if name not in runner.JOBS:
        raise HTTPException(404, "unknown job")
    if not runner.start(name, params):
        raise HTTPException(409, "a job is already running")
    return {"ok": True}


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/")
def index():
    return FileResponse(str(WEB / "index.html"))
