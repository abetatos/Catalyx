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
from catalyx.scorer import catalyst_scorer as cs
from catalyx.scorer.catalyst_scorer import (_anchor_date, _decayed_strength, _halflife_for,
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

def _halflife_sector_impact() -> list[dict]:
    """What adopting the type table would do to each sector's `catalyst_alignment`.

    v12 — the drift table below prices the disagreement in DECAYED STRENGTH, which is not what
    anything downstream reads. That proxy is actively misleading: the copper event's decayed
    strength collapses 38.9 → 4.0, and CLAUDE.md has been quoting exactly that number as the
    reason the decision is expensive — but copper's own CA moves **0.1 points**, because the
    noisy-OR is saturated there and the event is not the carrier. The sector whose CA actually
    moves is `agriculture_soft_commodities` (−32.3), which the strength column never named.
    A decision priced in the wrong unit is not priced.

    Two full scans (stored, then taxonomy) over every sector with a study. `_HALFLIFE_SOURCE`
    is a module constant read at import, so it is swapped and restored around the second scan.
    """
    def scan() -> dict:
        return {sid: cs.compute_catalyst_alignment(sid).get("catalyst_alignment")
                for sid in cs._all_sector_ids()}
    prev = cs._HALFLIFE_SOURCE
    try:
        cs._HALFLIFE_SOURCE = "stored"
        stored = scan()
        cs._HALFLIFE_SOURCE = "taxonomy"
        table = scan()
    finally:
        cs._HALFLIFE_SOURCE = prev
    out = []
    for sid, a in stored.items():
        b = table.get(sid)
        if a is None or b is None or abs(a - b) < 0.05:
            continue
        out.append({"sector_id": sid, "ca_stored": a, "ca_taxonomy": b, "delta": round(b - a, 1)})
    return sorted(out, key=lambda r: r["delta"])


# ── v15 Phase 3 — decay warnings (informational: they never touch `verdict` or --strict) ─────────

REINFORCED_DECAYED_BELOW = 0.25   # retained fraction under which "strengthening" is not enough
STACK_MIN_RETAINED = 0.50         # both events must still carry this much to double-count


def _retained(ev: dict) -> float:
    """Fraction of its strength an event still carries — the scorer's own decay, same halflife
    and anchor, so the warning cannot disagree with the number the score uses."""
    hl, _ = _halflife_for(ev)
    return _decayed_strength(100.0, hl, _anchor_date(ev)) / 100.0


def _latest_review(ev: dict) -> dict | None:
    """Most recent review_log entry BY DATE — the list order is not a contract."""
    log = [r for r in (ev.get("review_log") or []) if isinstance(r, dict) and r.get("date")]
    return max(log, key=lambda r: str(r["date"])[:10]) if log else None


def decay_warnings(events: list[dict]) -> dict:
    active = [e for e in events if e.get("status") == "active" and e.get("id")]
    reinforced = []
    for e in sorted(active, key=lambda x: x["id"]):
        last = _latest_review(e)
        ret = _retained(e)
        if last and last.get("verdict") == "strengthening" and ret < REINFORCED_DECAYED_BELOW:
            reinforced.append({
                "event_id": e["id"], "retained_pct": round(ret * 100, 1),
                "last_review": str(last["date"])[:10],
                "note": "candidate for a new dated event (C3 novelty rule), not for a status edit"})
    stack = []
    ret = {e["id"]: _retained(e) for e in active}
    seen: set[tuple[str, str]] = set()
    for a in sorted(active, key=lambda x: x["id"]):
        for b_id in a.get("related_catalyst_ids") or []:
            b = next((e for e in active if e["id"] == b_id), None)
            if b is None:
                continue
            pair = tuple(sorted((a["id"], b_id)))
            if pair in seen:
                continue
            seen.add(pair)
            shared = sorted(set(a.get("affected_sectors") or []) & set(b.get("affected_sectors") or []))
            if shared and ret[a["id"]] >= STACK_MIN_RETAINED and ret[b_id] >= STACK_MIN_RETAINED:
                stack.append({"events": list(pair), "shared_sectors": shared,
                              "retained_pct": {k: round(ret[k] * 100, 1) for k in pair}})
    return {"reinforced_but_decayed": reinforced, "same_driver_stack": stack}


# ── v15 Phase 4 — does a registered event reach the score? ──────────────────────────────────────
# The scorer reads `affected_sectors` NOWHERE: an event enters a sector's CA only through the
# study's `active_catalyst_ids` — directly, or via a listed structural the event names in
# `related_catalyst_ids` with one of the relations below (catalyst_scorer.py, the
# `related_events` filter). The 2026-09-16 Fed hike was registered and weighed on no sector.
LINKING_RELATIONS = ("confirms", "contradicts", "independent")   # must match catalyst_scorer


def _investable_map() -> dict[str, bool]:
    import yaml
    from pathlib import Path
    tax = yaml.safe_load((Path(__file__).parents[1] / "config" / "sector_taxonomy.yaml")
                         .read_text(encoding="utf-8")) or {}
    return {s["id"]: bool(s.get("investable")) for s in tax.get("sectors") or []}


def _structural_ids() -> set[str]:
    from catalyx.store import structural_catalyst_repo as scr
    return {c.get("id") for c in scr._load_all() if c.get("id")}


def event_not_linked(events: list[dict], load_study, structural_ids: set[str],
                     investable: dict[str, bool]) -> list[dict]:
    """Active events that name a sector which will never score them. `load_study(sector_id)`
    returns the study dict or None. A sector whose study declined the event in writing
    (`declined_catalyst_ids`) is not listed — a question answered stays answered."""
    rows = []
    for e in sorted((x for x in events if x.get("status") == "active" and x.get("id")),
                    key=lambda x: x["id"]):
        via = [r for r in (e.get("related_catalyst_ids") or []) if r in structural_ids] \
            if e.get("relation_to_structural") in LINKING_RELATIONS else []
        for sid in e.get("affected_sectors") or []:
            row = {"event_id": e["id"], "sector_id": sid,
                   "investable": investable.get(sid)}
            study = load_study(sid)
            if study is None:
                rows.append({**row, "reason": "no_study"})
                continue
            listed = set(study.get("active_catalyst_ids") or [])
            declined = {d.get("catalyst_id") for d in study.get("declined_catalyst_ids") or []}
            if e["id"] in listed or e["id"] in declined or any(v in listed for v in via):
                continue
            rows.append({**row, "reason": "not_in_active_catalyst_ids"})
    return rows


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
        "halflife_sector_impact": _halflife_sector_impact() if drift else [],
        "n_events": len(rows), "n_fail": n_fail,
        "n_no_rubric": sum(1 for r in rows if r["rubric_total"] is None),
        "n_no_anchors": sum(1 for r in rows if not r["anchors"]["stronger_than"]
                            and not r["anchors"]["weaker_than"]),
        "n_halflife_drift": len(drift),
        "n_halflife_drift_longer": sum(1 for r in drift
                                       if r["halflife_drift"]["direction"] == "longer"),
        "halflife_source": weights.halflife_source(),
        "strength_calibration": weights.event_strength_calibration().get("mode"),
        **decay_warnings(events),
        "event_not_linked": event_not_linked(events, cs._load_sector_study, _structural_ids(),
                                            _investable_map()),
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
        out += ["", "  `decayed now` is what each event contributes TODAY under each halflife —",
                "  the size of the disagreement in STRENGTH, which is not the unit anything",
                "  downstream reads. The table below prices it where it lands."]

        imp = res.get("halflife_sector_impact") or []
        out += ["", f"  WHAT IT MOVES — catalyst_alignment per sector, {len(imp)} sector(s) affected:", ""]
        if not imp:
            out += ["    none — every drifting event sits under a saturated aggregate", ""]
        else:
            hh = f"  {'sector':<38} {'stored':>8} {'taxonomy':>9} {'Δ CA':>8}"
            out += [hh, "  " + "-" * (len(hh) - 2)]
            for r in imp:
                out.append(f"  {r['sector_id']:<38} {r['ca_stored']:>8.1f} "
                           f"{r['ca_taxonomy']:>9.1f} {r['delta']:>+8.1f}")
        out += ["", "  Not a recommendation: adopting the table by default would move a held",
                "  position's CA on the authority of a constant six reviews declined to use.",
                "  Flip `event_catalyst_decay.halflife_source` to `taxonomy` — or edit the",
                "  table — as a frozen-threshold decision, with this table in front of you."]
    rd, st = res.get("reinforced_but_decayed") or [], res.get("same_driver_stack") or []
    if rd:
        out += ["", f"  REINFORCED BUT DECAYED — {len(rd)} event(s) last reviewed 'strengthening' "
                    f"while retaining <{REINFORCED_DECAYED_BELOW:.0%} of their strength:", ""]
        for r in rd:
            out.append(f"    {r['event_id']:<50} {r['retained_pct']:>5.1f}%  (last review {r['last_review']})")
        out += ["", "  A strengthening verdict does not revive an event: decay is anchored on",
                "  `event_date`. A candidate for a new dated event (C3 novelty rule), not a status edit."]
    if st:
        out += ["", f"  SAME-DRIVER STACK — {len(st)} linked pair(s) both still above "
                    f"{STACK_MIN_RETAINED:.0%} of their strength on a shared sector:", ""]
        for r in st:
            a, b = r["events"]
            out.append(f"    {a} ({r['retained_pct'][a]:.0f}%) ↔ {b} ({r['retained_pct'][b]:.0f}%)"
                       f"  on {', '.join(r['shared_sectors'])}")
        out += ["", "  Real double counting if they are one driver; the review decides (CLAUDE.md:",
                "  one catalyst = one driver). Sequential guidance → decision is allowed."]
    nl = res.get("event_not_linked") or []
    if nl:
        out += ["", f"  EVENT NOT LINKED — {len(nl)} (event, sector) pair(s) the score will never read:", ""]
        for r in nl:
            inv = {True: "investable", False: "watch-only"}.get(r["investable"], "?")
            out.append(f"    {r['event_id']:<46} {r['sector_id']:<34} {inv:<10} {r['reason']}")
        out += ["", "  `affected_sectors` is not read by the scorer. Per pair: add the event id to the",
                "  study's `active_catalyst_ids`, or name a listed structural in the event's",
                "  `related_catalyst_ids`, or write it in `declined_catalyst_ids` with the reason."]
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
