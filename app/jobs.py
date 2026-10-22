"""Command-line jobs. Run with: python -m app.jobs <command> [args]

  init                                 create tables, seed targets/inputs
  collect-ch   [n_companies]           Companies House cohort
  collect-fl   <file> [n] [--probe]    Florida officers from a Sunbiz bulk file (free)
  collect-oc   [jurisdiction] [n]      OpenCorporates (paid API; optional)
  import-csv   <path> <cohort>         any cohort from CSV
  check        [--cohort X] [--limit N] [--recheck-days D] [--no-per-target]
                                       presence checks for subjects due
  analyse                              rebuild edges and target stats
  plan         <subject_id>            print a plan
  seed-sample                          synthetic subjects + checks for UI testing (no API calls)
"""
import sys, argparse, random
from datetime import datetime, timedelta
from .db import init_db, SessionLocal
from .models import Subject, Target, Check, Input, InputType, SerpSnapshot


def cmd_check(args):
    from .checkers.presence import check_subject
    with SessionLocal() as db:
        q = db.query(Subject)
        if args.cohort:
            q = q.filter_by(cohort=args.cohort)
        cutoff = datetime.utcnow() - timedelta(days=args.recheck_days)
        due = []
        for s in q.all():
            last = max((c.checked_at for c in s.checks), default=None)
            if last is None or last < cutoff:
                due.append(s)
        due = due[: args.limit]
        print(f"{len(due)} subjects due")
        for i, s in enumerate(due, 1):
            try:
                found = check_subject(db, s, per_target=not args.no_per_target)
                print(f"[{i}/{len(due)}] {s.full_name}: {len(found)} pages")
            except Exception as e:  # keep going; one bad subject must not stop a batch
                db.rollback()
                print(f"[{i}/{len(due)}] {s.full_name}: ERROR {e}")


def cmd_seed_sample(args):
    """Synthetic data shaped like real results so the UI can be built and demoed.
    Replaced by real checks as soon as DataForSEO runs."""
    rng = random.Random(7)
    with SessionLocal() as db:
        if db.query(Subject).filter_by(cohort="sample").count():
            print("sample already seeded"); return
        targets = db.query(Target).filter_by(active=True).all()
        itypes = [i.key for i in db.query(InputType)]
        # ground-truth rules the sample follows (so the analysis has something to find)
        rules = {  # target: {input: p_with}, base p_without
            "rocketreach": ({"linkedin": 0.85, "team_page": 0.7, "company_domain": 0.5}, 0.08),
            "zoominfo": ({"linkedin": 0.6, "team_page": 0.75, "press_release": 0.5, "zoominfo_lite": 0.9}, 0.1),
            "apollo": ({"linkedin": 0.8, "team_page": 0.6}, 0.1),
            "signalhire": ({"linkedin": 0.7}, 0.05),
            "wiza": ({"linkedin": 0.65}, 0.03),
            "contactout": ({"linkedin": 0.55}, 0.04),
            "datanyze": ({"team_page": 0.5, "company_domain": 0.3}, 0.05),
            "companies_house": ({"ch_appointment": 1.0}, 0.0),
            "opencorporates": ({"ch_appointment": 0.9, "us_officer_filing": 0.7}, 0.02),
            "endole": ({"ch_appointment": 0.9}, 0.0),
            "companycheck": ({"ch_appointment": 0.85}, 0.0),
            "northdata": ({"ch_appointment": 0.6}, 0.02),
            "corporationwiki": ({"us_officer_filing": 0.8}, 0.03),
            "bizapedia": ({"us_officer_filing": 0.7}, 0.03),
            "dnb": ({"duns": 0.9, "company_domain": 0.3}, 0.05),
            "crunchbase": ({"crunchbase_page": 1.0, "press_release": 0.4}, 0.05),
            "pitchbook": ({"crunchbase_page": 0.5, "press_release": 0.55, "news_mention": 0.4}, 0.03),
            "theorg": ({"theorg_page": 1.0}, 0.02),
            "owler": ({"company_domain": 0.4, "press_release": 0.3}, 0.05),
            "muckrack": ({"byline": 0.9}, 0.02),
            "marketscreener": ({"press_release": 0.3, "news_mention": 0.4}, 0.02),
            "linkedin": ({"linkedin": 1.0}, 0.0),
        }
        page1 = {"linkedin": .95, "companies_house": .6, "crunchbase": .7, "rocketreach": .45,
                 "zoominfo": .35, "apollo": .3, "theorg": .5, "muckrack": .6, "endole": .3,
                 "opencorporates": .4, "pitchbook": .5, "dnb": .3, "signalhire": .2}
        now = datetime.utcnow()
        for i in range(400):
            uk = rng.random() < 0.55
            s = Subject(full_name=f"Sample Person {i:03d}", country="GB" if uk else "US",
                        company_name=f"Sample Co {i % 60} Ltd", title="Director", cohort="sample",
                        company_domain=f"sampleco{i % 60}.com" if rng.random() < 0.6 else None,
                        consented=i < 20, is_client=i < 20)
            db.add(s); db.flush()
            have = set()
            probs = {"linkedin": .7, "team_page": .35, "company_domain": .6, "ch_appointment": 1.0 if uk else 0.0,
                     "us_officer_filing": 0.0 if uk else 1.0, "crunchbase_page": .15, "theorg_page": .08,
                     "duns": .2, "zoominfo_lite": .05, "press_release": .12, "byline": .07,
                     "news_mention": .1, "conference": .1, "signature_outreach": .05}
            for k in itypes:
                p = probs.get(k, 0.05)
                present = rng.random() < p
                eff = now - timedelta(days=rng.randint(30, 400)) if present else None
                db.add(Input(subject_id=s.id, type_key=k, present=present, effective_at=eff))
                if present:
                    have.add((k, eff))
            havek = {k for k, _ in have}
            results = []
            for t in targets:
                rule = rules.get(t.key)
                if rule:
                    pw, base = rule
                    p = max([base] + [v for k, v in pw.items() if k in havek])
                else:
                    p = 0.03
                present = rng.random() < p
                pos = None
                if present:
                    pr = page1.get(t.key, 0.15)
                    pos = rng.randint(1, 10) if rng.random() < pr else rng.randint(11, 40)
                    results.append({"position": pos, "url": f"https://{t.domain}/x/{i}", "domain": t.domain,
                                    "title": s.full_name})
                lag = rng.randint(3, 60)
                earliest = max((e for k, e in have if k in (rules.get(t.key, ({},0))[0])), default=None)
                checked = (earliest + timedelta(days=lag)) if (present and earliest) else now
                db.add(Check(subject_id=s.id, target_key=t.key, present=present, checked_at=min(checked, now),
                             url=f"https://{t.domain}/x/{i}" if present else None,
                             richness=rng.randint(1, 3) if present else 0, indexed=present,
                             serp_position=pos, method="serp"))
            db.add(SerpSnapshot(subject_id=s.id, query=s.full_name, location_code=2826,
                                results=sorted(results, key=lambda r: r["position"])))
        db.commit()
        print("seeded 400 sample subjects")


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("collect-ch"); a.add_argument("n", type=int, nargs="?", default=200)
    a = sub.add_parser("collect-fl"); a.add_argument("path"); a.add_argument("n", type=int, nargs="?", default=500); a.add_argument("--probe", action="store_true")
    a = sub.add_parser("collect-oc"); a.add_argument("jurisdiction", nargs="?", default="us_fl"); a.add_argument("n", type=int, nargs="?", default=100)
    a = sub.add_parser("import-csv"); a.add_argument("path"); a.add_argument("cohort")
    a = sub.add_parser("check"); a.add_argument("--cohort"); a.add_argument("--limit", type=int, default=50)
    a.add_argument("--recheck-days", type=int, default=30); a.add_argument("--no-per-target", action="store_true")
    sub.add_parser("analyse")
    a = sub.add_parser("plan"); a.add_argument("subject_id", type=int)
    sub.add_parser("seed-sample")
    args = p.parse_args(argv)

    init_db()
    if args.cmd == "init":
        print("ok")
    elif args.cmd == "collect-ch":
        from .collectors import companies_house
        with SessionLocal() as db:
            print(companies_house.collect(db, n_companies=args.n), "subjects")
    elif args.cmd == "collect-fl":
        from .collectors import florida
        if args.probe:
            florida.probe(args.path)
        else:
            with SessionLocal() as db:
                print(florida.collect(db, args.path, n_subjects=args.n), "subjects")
    elif args.cmd == "collect-oc":
        from .collectors import opencorporates
        with SessionLocal() as db:
            print(opencorporates.collect(db, jurisdiction=args.jurisdiction, n_companies=args.n), "subjects")
    elif args.cmd == "import-csv":
        from .collectors import csv_import
        with SessionLocal() as db:
            print(csv_import.collect(db, args.path, args.cohort), "subjects")
    elif args.cmd == "check":
        cmd_check(args)
    elif args.cmd == "analyse":
        from .analysis.compute import run
        with SessionLocal() as db:
            print(run(db))
    elif args.cmd == "plan":
        import json
        from .analysis.planner import plan
        with SessionLocal() as db:
            print(json.dumps(plan(db, args.subject_id), indent=2, default=str))
    elif args.cmd == "seed-sample":
        cmd_seed_sample(args)


if __name__ == "__main__":
    main()
