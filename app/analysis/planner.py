"""Given what a subject has, say what to do next and what it should produce.

Score for a candidate input = sum over targets not yet present of
    (p_with - p_without) * page_one_weight(target)
divided by noise. Greedy: pick the best, assume its expected pages appear,
repeat. Output is an ordered plan with expected pages and lags.
"""
from sqlalchemy.orm import Session
from ..models import Subject, Input, Check, Edge, TargetStat, InputType, Target
from .compute import latest_presence


def plan(db: Session, subject_id: int, max_steps: int = 8, max_noise: int = 3) -> dict:
    subj = db.get(Subject, subject_id)
    pres = latest_presence(db).get(subject_id, {})
    have_pages = {k for k, c in pres.items() if c.present}
    have_inputs = {i.type_key for i in db.query(Input).filter_by(subject_id=subject_id, present=True)}
    itypes = {i.key: i for i in db.query(InputType)}
    targets = {t.key: t for t in db.query(Target).filter_by(active=True)}
    stats = {s.target_key: s for s in db.query(TargetStat)}
    edges = db.query(Edge).all()
    by_input: dict[str, list[Edge]] = {}
    for e in edges:
        by_input.setdefault(e.input_key, []).append(e)

    def weight(tk: str) -> float:
        s = stats.get(tk)
        if not s:
            return 0.5
        if targets[tk].category == "people_search":
            return 0.0
        return 0.3 + 0.7 * (s.page_one_rate or 0) + 0.3 * (s.page_two_rate or 0)

    steps = []
    expected_pages = set(have_pages)
    done_inputs = set(have_inputs)
    for _ in range(max_steps):
        best = None
        for ik, es in by_input.items():
            it = itypes.get(ik)
            if not it or not it.controllable or ik in done_inputs or (it.noise or 1) > max_noise:
                continue
            if it.region and it.region != "global" and subj.country and it.region != subj.country:
                continue
            gain, pages = 0.0, []
            for e in es:
                if e.target_key in expected_pages or e.lift is None:
                    continue
                treg = targets[e.target_key].region
                if treg and treg not in ("global", "EU") and subj.country and treg != subj.country:
                    continue
                delta = (e.p_with or 0) - (e.p_without or 0)
                if delta <= 0.05 or e.n_with < 5:
                    continue
                gain += delta * weight(e.target_key)
                pages.append({"target": e.target_key, "label": targets[e.target_key].label,
                              "p": round(e.p_with, 2), "lift": round(e.lift, 1) if e.lift else None,
                              "lag_days": e.median_lag_days, "n": e.n_with,
                              "page_one_rate": round(stats[e.target_key].page_one_rate, 2) if e.target_key in stats else None})
            score = gain / (it.noise or 1)
            if pages and (best is None or score > best["score"]):
                best = {"input": ik, "label": it.label, "noise": it.noise, "score": round(score, 3),
                        "expected_pages": sorted(pages, key=lambda p: -p["p"])}
        if not best:
            break
        steps.append(best)
        done_inputs.add(best["input"])
        expected_pages.update(p["target"] for p in best["expected_pages"] if p["p"] >= 0.5)

    return {"subject": {"id": subj.id, "name": subj.full_name, "company": subj.company_name},
            "have_inputs": sorted(have_inputs), "have_pages": sorted(have_pages),
            "steps": steps,
            "predicted_pages": sorted(expected_pages - have_pages)}
