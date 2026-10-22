"""Background job runner so everything can be triggered from the web UI.

One job at a time. Each job writes to an in-memory log the UI polls.
Jobs: florida_fetch, companies_house, check, analyse, seed_sample, delete_cohort.
"""
import threading, traceback, os, io, zipfile, tempfile
from datetime import datetime
from pathlib import Path
from .db import SessionLocal
from .models import Subject

_lock = threading.Lock()
_state = {"running": None, "started": None, "log": [], "history": []}
MAX_LOG = 400


def log(msg: str):
    line = f"{datetime.utcnow().strftime('%H:%M:%S')}  {msg}"
    _state["log"].append(line)
    if len(_state["log"]) > MAX_LOG:
        del _state["log"][: len(_state["log"]) - MAX_LOG]


def status():
    return {"running": _state["running"], "started": _state["started"],
            "log": _state["log"][-120:], "history": _state["history"][-20:]}


def start(name: str, params: dict) -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state["running"], _state["started"], _state["log"] = name, datetime.utcnow().isoformat(), []
    threading.Thread(target=_run, args=(name, params), daemon=True).start()
    return True


def _run(name, params):
    try:
        log(f"start {name} {params}")
        JOBS[name](params)
        log(f"done {name}")
        _state["history"].append({"job": name, "ok": True, "at": datetime.utcnow().isoformat()})
    except Exception as e:
        log(f"ERROR {e}")
        log(traceback.format_exc()[-1500:])
        _state["history"].append({"job": name, "ok": False, "at": datetime.utcnow().isoformat(), "error": str(e)})
    finally:
        _state["running"] = None


# ---------------- jobs ----------------

def job_florida_fetch(p):
    """Download Florida's quarterly cordata.zip over SFTP and import a random sample."""
    import paramiko
    from .collectors import florida
    n = int(p.get("n", 500))
    host, user, pw = "sftp.floridados.gov", "Public", "PubAccess1845!"   # published by the Division of Corporations
    remote = p.get("remote", "/doc/quarterly/cor/cordata.zip")
    tmp = Path(tempfile.gettempdir()) / "cordata.zip"
    if tmp.exists() and (datetime.utcnow().timestamp() - tmp.stat().st_mtime) < 7 * 86400 and not p.get("refresh"):
        log(f"using cached {tmp}")
    else:
        log(f"connecting to {host}")
        t = paramiko.Transport((host, 22)); t.connect(username=user, password=pw)
        s = paramiko.SFTPClient.from_transport(t)
        size = s.stat(remote).st_size
        log(f"downloading {remote} ({size/1e6:.0f} MB)")
        done = [0]
        def cb(x, y):
            if x - done[0] > 50e6:
                done[0] = x; log(f"  {x/1e6:.0f} MB")
        s.get(remote, str(tmp), callback=cb)
        t.close()
        log("download complete")
    log("parsing and sampling officers filed in the last year")
    with SessionLocal() as db:
        n_created = florida.collect(db, str(tmp), n_subjects=n)
    log(f"imported {n_created} Florida officers")


def job_companies_house(p):
    from .collectors import companies_house
    if not os.getenv("COMPANIES_HOUSE_API_KEY"):
        raise RuntimeError("COMPANIES_HOUSE_API_KEY is not set in the environment")
    with SessionLocal() as db:
        n = companies_house.collect(db, n_companies=int(p.get("n", 200)))
    log(f"imported {n} UK directors")


def job_check(p):
    from .checkers.presence import check_subject
    from datetime import timedelta
    if not os.getenv("DATAFORSEO_LOGIN"):
        raise RuntimeError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD are not set")
    limit, recheck, per_target = int(p.get("limit", 50)), int(p.get("recheck_days", 30)), not p.get("cheap")
    cohort = p.get("cohort") or None
    with SessionLocal() as db:
        q = db.query(Subject)
        if cohort:
            q = q.filter_by(cohort=cohort)
        cutoff = datetime.utcnow() - timedelta(days=recheck)
        due = [s for s in q.all() if not s.checks or max(c.checked_at for c in s.checks) < cutoff][:limit]
        log(f"{len(due)} subjects due ({'full' if per_target else 'cheap'} mode)")
        for i, s in enumerate(due, 1):
            try:
                found = check_subject(db, s, per_target=per_target)
                log(f"[{i}/{len(due)}] {s.full_name}: {len(found)} pages")
            except Exception as e:
                db.rollback(); log(f"[{i}/{len(due)}] {s.full_name}: ERROR {e}")


def job_analyse(p):
    from .analysis.compute import run
    with SessionLocal() as db:
        log(str(run(db)))


def job_seed_sample(p):
    from .jobs import cmd_seed_sample
    cmd_seed_sample(None); log("sample seeded")


def job_delete_cohort(p):
    cohort = p["cohort"]
    with SessionLocal() as db:
        n = db.query(Subject).filter_by(cohort=cohort).count()
        for s in db.query(Subject).filter_by(cohort=cohort).all():
            db.delete(s)
        db.commit()
    log(f"deleted cohort {cohort} ({n} subjects)")


JOBS = {"florida_fetch": job_florida_fetch, "companies_house": job_companies_house, "check": job_check,
        "analyse": job_analyse, "seed_sample": job_seed_sample, "delete_cohort": job_delete_cohort}
