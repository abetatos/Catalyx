"""Lint the two free cardinals an author still assigns per event (v11 P2/P3).

The whole point of this project's scoring layer is that a formula computes what a formula can
compute. `intensity.current_score` is derived from indicators. `novelty_score` is a rubric
count. `catalyst_alignment` is a noisy-OR over those. But two numbers on a `CatalystEvent` are
still typed in freehand by whoever ran the scan, and both feed straight into the alignment:

  `strength_score`      — how hard the event hits. Prose said "magnitude × surprise ×
                          confirmation"; that is a formula only if all three are defined, and
                          none were. The 16 active events land in [55, 91] with σ=10 and
                          cluster on round numbers: three 72s, two 80s, two 82s, two 75s.
  `decay_halflife_days` — how long it keeps hitting. `catalyst_taxonomy.yaml` has carried a
                          value per `catalyst_type` from the start, and `catalyx-scan.md` says
                          in prose "always set decay_halflife_days from catalyst_taxonomy.yaml".
                          Six of sixteen active events disagree with it, five toward LONGER
                          persistence — the direction that keeps a spent event scoring.

This module does not fix either number. It makes them inspectable, which is the thing that was
missing: prose lost to a field because nothing ever printed the disagreement. Three checks:

  RUBRIC   `strength_rubric_scores` present, and its weighted total within
           `strength_anchoring.max_rubric_divergence` of the assigned score.
  ANCHOR   the score falls inside [weaker_than, stronger_than] — CLAUDE.md rule 3 ("anchor a
           new catalyst to an existing one") made mechanical. This is the check that persists
           across sessions; the absolute number never did.
  HALFLIFE the stored value against the per-type table, with the decay consequence priced so
           the disagreement is a magnitude and not a footnote.

Usage:
    uv run python -m catalyx.scorer.catalyst_audit
    uv run python -m catalyx.scorer.catalyst_audit --json
    uv run python -m catalyx.scorer.catalyst_audit --strict     # exit 1 if any FAIL
"""
from __future__ import annotations

import argparse
import json
import sys

from catalyx.config import weights
from catalyx.scorer.catalyst_scorer import (_anchor_date, _decayed_strength,
                                            _load_all_event_catalysts, calibrate_strength)

_RUBRIC = weights.strength_score_rubric()
_ANCHORING = weights.strength_anchoring()
_TYPE_HALFLIVES = weights.catalyst_type_halflives()
_DEFAULT_HALFLIFE = weights.event_default_halflife()


# ── Pure checks (unit-tested, no I/O) ────────────────────────────────────────

def rubric_total(scores: dict | None, rubric: list[dict] | None = None) -> float | None:
    """Weighted total of the answered rubric booleans, or None if nothing was answered.

    An unanswered question counts as `false`, not as missing: the rubric's questions are
    OBSERVABLE, so "we did not check whether a second source corroborates it" and "no second
    source corroborates it" are the same state as far as the evidence goes. That is the
    opposite of the `ca_imputed` rule, and deliberately so — there, the missing thing was a
    measurement nobody took; here, it is a question the author was asked and skipped.
    """
    rubric = rubric if rubric is not None else _RUBRIC
    if not isinstance(scores, dict) or not scores or not rubric:
        return None
    return float(sum(float(q.get("weight", 0)) for q in rubric if scores.get(q["id"]) is True))


def anchor_bracket(score: float, weaker: float | None, stronger: float | None) -> tuple[bool, str]:
    """(ok, reason) — does `score` sit inside the bracket its anchors declare?

    `weaker_than` names an event this one is WEAKER than, so it is the UPPER bound; `stronger_than`
    is the lower. Getting that backwards is the easy mistake, so both directions are named in the
    failure text rather than left to the reader.
    """
    if weaker is None and stronger is None:
        return False, "no anchors"
    # Inversion is checked FIRST because an empty bracket fails a bound check for EVERY score —
    # so the bound message would always win the race and this one would be unreachable. That is
    # not cosmetic: "score below its stronger_than anchor" sends the author to change the SCORE,
    # when the thing that is wrong is which anchor went in which field.
    if stronger is not None and weaker is not None and stronger > weaker:
        return False, (f"anchors inverted: stronger_than={stronger:g} exceeds "
                       f"weaker_than={weaker:g} — the bracket is empty")
    if stronger is not None and score < stronger:
        return False, f"score {score:g} below its stronger_than anchor ({stronger:g})"
    if weaker is not None and score > weaker:
        return False, f"score {score:g} above its weaker_than anchor ({weaker:g})"
    return True, "in bracket"


# ── Audit ────────────────────────────────────────────────────────────────────

def audit(active_only: bool = False) -> dict:
    events = _load_all_event_catalysts()
    by_id = {e.get("id"): e for e in events if e.get("id")}
    pop = [e["strength_score"] for e in events
           if isinstance(e.get("strength_score"), (int, float))]
    max_div = float(_ANCHORING.get("max_rubric_divergence", 15))
    enforce = bool(_ANCHORING.get("enforce_bracket", True))

    rows = []
    for e in sorted(events, key=lambda x: str(x.get("id"))):
        if active_only and e.get("status") != "active":
            continue
        eid = e.get("id")
        score = e.get("strength_score")
        issues: list[str] = []

        # ── RUBRIC ────────────────────────────────────────────────────────────
        total = rubric_total(e.get("strength_rubric_scores"))
        if total is None:
            issues.append("no strength_rubric_scores — the score is a bare cardinal")
        elif isinstance(score, (int, float)) and abs(total - float(score)) > max_div:
            issues.append(f"rubric total {total:g} vs assigned {score:g} "
                          f"(|Δ| {abs(total - float(score)):g} > {max_div:g})")

        # ── ANCHOR ────────────────────────────────────────────────────────────
        anchors = e.get("strength_anchors") or {}
        st_id, wk_id = anchors.get("stronger_than"), anchors.get("weaker_than")
        st = (by_id.get(st_id) or {}).get("strength_score") if st_id else None
        wk = (by_id.get(wk_id) or {}).get("strength_score") if wk_id else None
        for label, aid in (("stronger_than", st_id), ("weaker_than", wk_id)):
            if aid and aid not in by_id:
                issues.append(f"{label} anchor '{aid}' is not a registered event")
        if enforce and isinstance(score, (int, float)):
            ok, why = anchor_bracket(float(score), wk, st)
            if not ok:
                issues.append(why)

        # ── HALFLIFE ──────────────────────────────────────────────────────────
        stored = e.get("decay_halflife_days")
        table = _TYPE_HALFLIVES.get(str(e.get("catalyst_type") or ""))
        hl_drift = None
        if stored is not None and table is not None and float(stored) != float(table):
            anchor_d = _anchor_date(e)
            cal, _ = calibrate_strength(float(score or 0), pop)
            d_stored = _decayed_strength(cal, float(stored), anchor_d)
            d_table = _decayed_strength(cal, float(table), anchor_d)
            hl_drift = {"stored": float(stored), "table": float(table),
                        "decayed_stored": d_stored, "decayed_table": d_table,
                        "direction": "longer" if float(stored) > float(table) else "shorter"}
            if e.get("halflife_override_reason"):
                issues.append(f"halflife {stored:g} overrides the {e['catalyst_type']} table "
                              f"({table:g}) — reason given")
            else:
                issues.append(f"halflife {stored:g} disagrees with the {e['catalyst_type']} "
                              f"table ({table:g}) and no halflife_override_reason is set")

        cal, cal_applied = calibrate_strength(float(score or 0), pop)
        rows.append({
            "event_id": eid, "status": e.get("status"), "catalyst_type": e.get("catalyst_type"),
            "strength_score": score, "strength_calibrated": cal,
            "strength_rank_calibrated": cal_applied,
            "rubric_total": total, "anchors": {"stronger_than": st_id, "weaker_than": wk_id,
                                               "stronger_score": st, "weaker_score": wk},
            "halflife_drift": hl_drift,
            "issues": issues, "verdict": "FAIL" if issues else "OK",
        })

    n_fail = sum(1 for r in rows if r["verdict"] == "FAIL")
    drift = [r for r in rows if r["halflife_drift"]]
    return {
        "n_events": len(rows), "n_fail": n_fail,
        "n_no_rubric": sum(1 for r in rows if r["rubric_total"] is None),
        "n_no_anchors": sum(1 for r in rows if not r["anchors"]["stronger_than"]
                            and not r["anchors"]["weaker_than"]),
        "n_halflife_drift": len(drift),
        "n_halflife_drift_longer": sum(1 for r in drift
                                       if r["halflife_drift"]["direction"] == "longer"),
        "halflife_source": weights.halflife_source(),
        "strength_calibration": weights.event_strength_calibration().get("mode"),
        "events": rows,
    }


def render(res: dict) -> str:
    out = [f"CATALYX — catalyst event audit   {res['n_fail']}/{res['n_events']} events with issues",
           f"  strength calibration: {res['strength_calibration']}   "
           f"halflife source: {res['halflife_source']}", ""]
    hdr = f"  {'event':<50} {'str':>4} {'cal':>6} {'rubric':>7} {'anchors':>8}  verdict"
    out += [hdr, "  " + "-" * (len(hdr) - 2)]
    for r in res["events"]:
        rub = f"{r['rubric_total']:g}" if r["rubric_total"] is not None else "—"
        anc = "yes" if (r["anchors"]["stronger_than"] or r["anchors"]["weaker_than"]) else "—"
        out.append(f"  {r['event_id']:<50} {(r['strength_score'] or 0):>4.0f} "
                   f"{r['strength_calibrated']:>6.1f} {rub:>7} {anc:>8}  {r['verdict']}")
        for i in r["issues"]:
            out.append(f"        · {i}")

    if res["n_halflife_drift"]:
        out += ["", f"  HALFLIFE DRIFT — {res['n_halflife_drift']} events disagree with their type's "
                    f"table, {res['n_halflife_drift_longer']} toward LONGER persistence:", ""]
        h = f"  {'event':<50} {'stored':>7} {'table':>7} {'decayed now':>22}"
        out += [h, "  " + "-" * (len(h) - 2)]
        for r in res["events"]:
            d = r["halflife_drift"]
            if not d:
                continue
            out.append(f"  {r['event_id']:<50} {d['stored']:>7.0f} {d['table']:>7.0f} "
                       f"{d['decayed_stored']:>10.1f} → {d['decayed_table']:<9.1f}")
        out += ["", "  `decayed now` is what each event contributes TODAY under each halflife. The",
                "  column is the size of the disagreement, not a recommendation: adopting the table",
                "  by default would move a held position's CA on the authority of a constant six",
                "  reviews declined to use. Flip `event_catalyst_decay.halflife_source` to",
                "  `taxonomy` — or edit the table — as a frozen-threshold decision, with this",
                "  table in front of you."]
    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="CATALYX — lint the free cardinals on CatalystEvents")
    p.add_argument("--json", action="store_true", help="Raw JSON output")
    p.add_argument("--active", action="store_true", help="Only events with status=active")
    p.add_argument("--strict", action="store_true", help="Exit 1 if any event FAILs")
    args = p.parse_args()
    res = audit(active_only=args.active)
    print(json.dumps(res, indent=2, ensure_ascii=False) if args.json else render(res))
    if args.strict and res["n_fail"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
