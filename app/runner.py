"""Background job runner so everything can be triggered from the web UI.

One job at a time. Each job writes to an in-memory log the UI polls.
Jobs: florida_fetch, companies_house, check, analyse, seed_sample, delete_cohort.
"""
import threading, traceback, os, io, re, zipfile, tempfile
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

def _find_remote(s, preferred: str, depth: int = 4) -> str:
    """Return a path to the corporate data file. Try the preferred path, then walk the tree
    (logging it) looking for cordata*.zip, case-insensitively."""
    try:
        s.stat(preferred); return preferred
    except IOError:
        log(f"{preferred} not found; listing server")
    found = []
    def walk(path, d):
        try:
            entries = s.listdir_attr(path)
        except IOError as e:
            log(f"  cannot list {path}: {e}"); return
        for a in entries:
            full = (path.rstrip("/") + "/" + a.filename) if path != "/" else "/" + a.filename
            isdir = bool(a.st_mode & 0o40000)
            log(f"  {'[d]' if isdir else '   '} {full}{'' if isdir else f'  {a.st_size/1e6:.1f} MB'}")
            if isdir and d < depth and a.filename.lower() not in ("flr", "fic", "gen", "trademarks", "tm"):
                walk(full, d + 1)
            elif not isdir and a.filename.lower().startswith("cordata") and a.filename.lower().endswith(".zip"):
                found.append(full)
    walk("/", 0)
    if not found:
        raise FileNotFoundError("no cordata*.zip found on the server; see the listing above")
    log(f"using {found[0]}")
    return found[0]


def job_florida_fetch(p):
    """Download Florida's recent daily corporate files over SFTP and import a random sample of officers.
    Daily files (~1 MB each, one per working day) live in /Public/doc/cor/ as MMDDYYc.dat.
    The quarterly cordata.zip is 1.8 GB and too large for this instance."""
    import paramiko
    from .collectors import florida
    n = int(p.get("n", 500))
    years = float(p.get("years", 4))      # sample daily files spread across this many years
    n_files = int(p.get("files", 200))    # how many daily files to pull in total
    recent_days = 40                      # always include the most recent files for the lag cohort
    host, user, pw = "sftp.floridados.gov", "Public", "PubAccess1845!"   # published by the Division of Corporations
    cache = Path(tempfile.gettempdir()) / "fl_daily"; cache.mkdir(exist_ok=True)
    log(f"connecting to {host}")
    t = paramiko.Transport((host, 22)); t.connect(username=user, password=pw)
    s = paramiko.SFTPClient.from_transport(t)
    folder = "/Public/doc/cor"
    files = [a.filename for a in s.listdir_attr(folder)
             if re.fullmatch(r"\d{6}c\.dat", a.filename, re.I)]
    def key(f):  # MMDDYY -> sortable YYMMDD
        return f[4:6] + f[0:2] + f[2:4]
    files = sorted(files, key=key)
    cutoff = datetime.utcnow().timestamp() - years * 365 * 86400
    def fdate(f):
        return datetime.strptime(f[:6], "%m%d%y")
    pool = [f for f in files if fdate(f).timestamp() >= cutoff]
    recent = pool[-recent_days:]
    older = pool[:-recent_days]
    import random as _r
    rng = _r.Random()
    picked = recent + rng.sample(older, min(len(older), max(0, n_files - len(recent))))
    files = sorted(picked, key=key)
    log(f"{len(pool)} daily files in the last {years:g} years; using {len(files)} ({files[0] if files else ''} .. {files[-1] if files else ''})")
    paths = []
    for i, f in enumerate(files, 1):
        local = cache / f
        if not local.exists():
            with s.open(f"{folder}/{f}", "rb") as rf, open(local, "wb") as wf:
                while True:
                    chunk = rf.read(65536)
                    if not chunk:
                        break
                    wf.write(chunk)
        paths.append(local)
        if i % 10 == 0:
            log(f"  {i}/{len(files)} downloaded")
    t.close()
    log("parsing officers")
    total = 0
    with SessionLocal() as db:
        total = florida.collect_many(db, [str(x) for x in paths], n_subjects=n, filed_within_days=int(years * 365) + 30)
    log(f"imported {total} Florida officers")


def job_companies_house(p):
    from .collectors import companies_house
    if not os.getenv("COMPANIES_HOUSE_API_KEY"):
        raise RuntimeError("COMPANIES_HOUSE_API_KEY is not set in the environment")
    with SessionLocal() as db:
        n = companies_house.collect(db, n_companies=int(p.get("n", 200)), log=log)
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
