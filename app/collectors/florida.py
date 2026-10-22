"""US officer cohort from Florida's free bulk corporate data.

Florida publishes its whole register, officers included, as fixed-width files:
  https://dos.fl.gov/sunbiz/other-services/data-downloads/
  SFTP host sftp.floridados.gov, user Public, password PubAccess1845! (published on that page)
  Quarterly: doc/quarterly/cor/cordata.zip   (all active records, split into 10 files)
  Daily:     doc/daily/cor/<date>c.txt        (that day's new filings)

Usage: download one of those files (the browser works on the SFTP host), then
    python -m app.jobs collect-fl path/to/file [n]

Layout follows the Division's corporate data definitions. The positions below
are the published ones; run `collect-fl --probe` on a file first and check that
the printed sample record reads correctly (name, file date, officers) before
importing. If a field is off, adjust LAYOUT here.
"""
import random, zipfile, io
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session
from ..models import Subject, Input

# (name, start_1based, length)
LAYOUT = [
    ("cor_number", 1, 12), ("cor_name", 13, 192), ("status", 205, 1), ("filing_type", 206, 15),
    ("princ_addr1", 221, 42), ("princ_addr2", 263, 42), ("princ_city", 305, 28),
    ("princ_state", 333, 2), ("princ_zip", 335, 10), ("princ_country", 345, 2),
    ("mail_addr1", 347, 42), ("mail_addr2", 389, 42), ("mail_city", 431, 28),
    ("mail_state", 459, 2), ("mail_zip", 461, 10), ("mail_country", 471, 2),
    ("file_date", 473, 8), ("fei_number", 481, 14), ("more_than_six_officers", 495, 1),
    ("last_trans_date", 496, 8), ("state_country", 504, 2),
    ("ra_name", 545, 42), ("ra_name_type", 587, 1),
]
OFFICER_START, OFFICER_COUNT = 669, 6
OFFICER_FIELDS = [("title", 4), ("type", 1), ("name", 42), ("addr1", 42), ("city", 28), ("state", 2), ("zip5", 5), ("zip4", 4)]
OFFICER_LEN = sum(l for _, l in OFFICER_FIELDS)   # 128

ACTIVE = {"A"}
TITLE_MAP = {"P": "President", "VP": "Vice President", "S": "Secretary", "T": "Treasurer", "D": "Director",
             "MGR": "Manager", "MGRM": "Managing Member", "AMBR": "Authorized Member", "CEO": "CEO",
             "CFO": "CFO", "COO": "COO", "PRES": "President", "CHMN": "Chairman", "AP": "Authorized Person"}
CORP_HINTS = ("LLC", "INC", "CORP", "L.L.C", "LTD", "TRUST", "COMPANY", "SERVICES", "GROUP", "ENTERPRISES", "HOLDINGS")


def _field(line: str, start: int, length: int) -> str:
    return line[start - 1: start - 1 + length].strip()


def parse_record(line: str) -> dict:
    rec = {name: _field(line, s, l) for name, s, l in LAYOUT}
    officers = []
    for i in range(OFFICER_COUNT):
        base = OFFICER_START + i * OFFICER_LEN
        off, pos = {}, base
        for fname, flen in OFFICER_FIELDS:
            off[fname] = _field(line, pos, flen); pos += flen
        if off["name"]:
            officers.append(off)
    rec["officers"] = officers
    return rec


def _lines(path: str):
    p = Path(path)
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                with z.open(n) as f:
                    for raw in io.TextIOWrapper(f, encoding="latin-1", errors="replace"):
                        yield raw.rstrip("\r\n")
    else:
        with open(p, encoding="latin-1", errors="replace") as f:
            for raw in f:
                yield raw.rstrip("\r\n")


def _person_name(raw: str) -> str | None:
    """Officer names are 'SURNAME, FIRST MIDDLE' or sometimes 'FIRST LAST'."""
    if any(h in raw.upper() for h in CORP_HINTS):
        return None
    if "," in raw:
        sur, fore = [x.strip() for x in raw.split(",", 1)]
        name = f"{fore} {sur}"
    else:
        name = raw
    name = " ".join(w.capitalize() for w in name.split())
    return name if len(name.split()) >= 2 else None


def probe(path: str, n: int = 3):
    for i, line in enumerate(_lines(path)):
        if i >= n:
            break
        r = parse_record(line)
        print({k: r[k] for k in ("cor_number", "cor_name", "status", "filing_type", "file_date", "princ_city")})
        for o in r["officers"]:
            print("   ", o["title"], o["type"], o["name"], o["city"], o["state"])


def collect(db: Session, path: str, n_subjects: int = 500, filed_within_days: int = 365,
            seed: int | None = None) -> int:
    rng = random.Random(seed)
    created = 0
    cutoff = datetime.utcnow().timestamp() - filed_within_days * 86400
    candidates = []
    for line in _lines(path):
        if len(line) < OFFICER_START:
            continue
        r = parse_record(line)
        if r["status"] not in ACTIVE:
            continue
        try:
            fd = datetime.strptime(r["file_date"], "%m%d%Y")
        except ValueError:
            continue
        if fd.timestamp() < cutoff:
            continue
        for o in r["officers"]:
            if o["type"] and o["type"] != "P":      # P = person, C = corporate officer
                continue
            name = _person_name(o["name"])
            if not name:
                continue
            candidates.append((r, o, name, fd))
    rng.shuffle(candidates)
    seen = set()
    for r, o, name, fd in candidates:
        ref = f"fl:{r['cor_number']}:{name.lower()}"
        if ref in seen or db.query(Subject).filter_by(source_ref=ref).first():
            continue
        seen.add(ref)
        subj = Subject(full_name=name, country="US", company_name=r["cor_name"].title(),
                       title=TITLE_MAP.get(o["title"], o["title"] or "Officer"),
                       cohort="florida", source_ref=ref,
                       meta={"cor_number": r["cor_number"], "filing_type": r["filing_type"],
                             "file_date": fd.date().isoformat(), "city": o["city"], "state": o["state"]})
        db.add(subj); db.flush()
        db.add(Input(subject_id=subj.id, type_key="us_officer_filing", present=True, effective_at=fd,
                     url=f"https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults?inquiryType=EntityName&searchTerm={r['cor_name']}"))
        created += 1
        if created >= n_subjects:
            break
    db.commit()
    return created
