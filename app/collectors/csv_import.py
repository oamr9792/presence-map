"""Import any cohort from CSV.

Columns: full_name, country, company_name, company_domain, title, [is_client], [consented],
then any input keys as columns with 1/0 or a URL (inputs.yaml keys), optionally
<key>_date for effective_at. Example:

full_name,country,company_name,company_domain,title,linkedin,team_page,team_page_date
Jane Doe,GB,Acme Ltd,acme.co.uk,Director,https://linkedin.com/in/janedoe,1,2026-09-01
"""
import csv
from datetime import datetime
from sqlalchemy.orm import Session
from ..models import Subject, Input, InputType

BASE_COLS = {"full_name", "country", "company_name", "company_domain", "title", "is_client", "consented"}


def collect(db: Session, path: str, cohort: str) -> int:
    keys = {k for (k,) in db.query(InputType.key).all()}
    created = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            subj = Subject(full_name=row["full_name"].strip(), country=row.get("country"),
                           company_name=row.get("company_name"), company_domain=row.get("company_domain"),
                           title=row.get("title"), cohort=cohort,
                           is_client=row.get("is_client", "0") in ("1", "true", "yes"),
                           consented=row.get("consented", "0") in ("1", "true", "yes"))
            db.add(subj); db.flush()
            for k in keys:
                v = (row.get(k) or "").strip()
                if not v or v == "0":
                    continue
                d = (row.get(f"{k}_date") or "").strip()
                db.add(Input(subject_id=subj.id, type_key=k, present=True,
                             url=v if v.startswith("http") else None,
                             effective_at=datetime.fromisoformat(d) if d else None))
            if row.get("company_domain"):
                db.add(Input(subject_id=subj.id, type_key="company_domain", present=True))
            created += 1
    db.commit()
    return created
