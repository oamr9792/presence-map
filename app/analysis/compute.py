"""Turn the ledger into edges, target stats and lags.

For each subject, the latest check per target decides presence.
For each (input, target): P(page | input) vs P(page | no input), lift, n.
Lag: for subjects with a dated input and a target first seen afterwards,
median days between effective_at and first present check.
"""
from collections import defaultdict
from datetime import datetime
from statistics import median
from sqlalchemy import func
from sqlalchemy.orm import Session
from ..models import Subject, Input, Check, Target, InputType, Edge, TargetStat, SerpSnapshot


def latest_presence(db: Session) -> dict[int, dict[str, Check]]:
    """subject_id -> target_key -> latest Check"""
    out: dict[int, dict[str, Check]] = defaultdict(dict)
    for c in db.query(Check).order_by(Check.checked_at).all():
        out[c.subject_id][c.target_key] = c
    return out


def first_seen(db: Session) -> dict[tuple[int, str], datetime]:
    rows = (db.query(Check.subject_id, Check.target_key, func.min(Check.checked_at))
            .filter(Check.present.is_(True)).group_by(Check.subject_id, Check.target_key).all())
    return {(s, t): d for s, t, d in rows}


def subject_inputs(db: Session) -> dict[int, dict[str, Input]]:
    out: dict[int, dict[str, Input]] = defaultdict(dict)
    for i in db.query(Input).order_by(Input.observed_at).all():
        out[i.subject_id][i.type_key] = i
    return out


def run(db: Session, min_n: int = 3, log=lambda m: None) -> dict:
    pres = latest_presence(db)
    inputs = subject_inputs(db)
    fs = first_seen(db)
    tgt_rows = db.query(Target).filter_by(active=True).all()
    targets = [t.key for t in tgt_rows]
    trivial = {(t.also_input, t.key) for t in tgt_rows if t.also_input}  # a crunchbase page "causing" crunchbase
    input_keys = [i.key for i in db.query(InputType)]
    subjects = [s.id for s in db.query(Subject) if s.id in pres]   # only checked subjects
    log(f"{len(subjects)} subjects have been checked; {len(input_keys)} input kinds, {len(targets)} targets")
    skipped = []

    db.query(Edge).delete()
    n_edges = 0
    for ik in input_keys:
        with_ = [s for s in subjects if inputs[s].get(ik) and inputs[s][ik].present]
        without = [s for s in subjects if ik in inputs[s] and not inputs[s][ik].present]
        # subjects where the input was never recorded are excluded: unknown, not absent
        if len(with_) < min_n or len(without) < min_n:
            skipped.append(f"{ik} (with={len(with_)}, without={len(without)})")
            continue
        for tk in targets:
            if (ik, tk) in trivial:
                continue
            pw = sum(1 for s in with_ if pres[s].get(tk) and pres[s][tk].present) / len(with_)
            po = sum(1 for s in without if pres[s].get(tk) and pres[s][tk].present) / len(without)
            lags = []
            for s in with_:
                inp = inputs[s][ik]
                seen = fs.get((s, tk))
                if inp.effective_at and seen and seen > inp.effective_at:
                    lags.append((seen - inp.effective_at).days)
            db.add(Edge(input_key=ik, target_key=tk, n_with=len(with_), n_without=len(without),
                        p_with=pw, p_without=po, lift=(pw / po) if po > 0 else (None if pw == 0 else 99.0),
                        median_lag_days=median(lags) if lags else None, n_lag=len(lags)))
            n_edges += 1

    if skipped:
        log("inputs with too few subjects on one side to compare (need " + str(min_n) + " each): " + ", ".join(skipped))
    log(f"{n_edges} input->target comparisons computed")
    # target stats from bare-name snapshots
    db.query(TargetStat).delete()
    tmap = {t.domain: t.key for t in db.query(Target)}
    positions: dict[str, list[int]] = defaultdict(list)
    checked_subjects = set(subjects)
    snaps = db.query(SerpSnapshot).all()
    latest_snap: dict[int, SerpSnapshot] = {}
    for sn in snaps:
        if sn.query and " " in sn.query and sn.subject_id in checked_subjects:
            # keep latest bare-name snapshot per subject (query == full name has no company appended; heuristic: shortest)
            cur = latest_snap.get(sn.subject_id)
            if cur is None or len(sn.query) < len(cur.query) or (len(sn.query) == len(cur.query) and sn.fetched_at > cur.fetched_at):
                latest_snap[sn.subject_id] = sn
    for sid, sn in latest_snap.items():
        best: dict[str, int] = {}
        for it in sn.results or []:
            for dom, tk in tmap.items():
                if dom in it["domain"]:
                    best[tk] = min(best.get(tk, 999), it["position"] or 999)
        for tk, p in best.items():
            positions[tk].append(p)
    for tk in targets:
        n_present = sum(1 for s in subjects if pres[s].get(tk) and pres[s][tk].present)
        ps = positions.get(tk, [])
        db.add(TargetStat(target_key=tk, n_subjects_checked=len(subjects),
                          presence_rate=(n_present / len(subjects)) if subjects else 0,
                          page_one_rate=(sum(1 for p in ps if p <= 10) / n_present) if n_present else 0,
                          page_two_rate=(sum(1 for p in ps if 10 < p <= 20) / n_present) if n_present else 0,
                          median_position=median(ps) if ps else None))
    present_counts = {tk: sum(1 for s in subjects if pres[s].get(tk) and pres[s][tk].present) for tk in targets}
    top = sorted(present_counts.items(), key=lambda kv: -kv[1])[:8]
    log("most common pages: " + ", ".join(f"{k} {v}" for k, v in top if v))
    db.commit()
    return {"subjects": len(subjects), "edges": n_edges, "targets": len(targets)}
