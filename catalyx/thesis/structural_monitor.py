"""Structural catalyst health monitor — the "bridge" of the noise-vs-regime design.

A `contradicts` event tells you the market's *opinion* turned. This module asks the
orthogonal question — did the *fundamentals* turn? — by reading the structural catalyst's
own `indicators[]` and `intensity` history, independently of any event. That second gate is
what lets the pipeline tell a transient shock (noise, decays) apart from the first tremor of a
structural break (regime change). See docs/DESIGN_catalyst_regime_discrimination.md.

A structural is **degrading** (→ pushes a sector toward `breaking`) when ANY:
  (i)   >= N_IND indicators are below their `threshold_weak` (direction-aware)
  (ii)  `intensity.current_score` fell more than DELTA_INT since the previous cycle
  (iii) a deactivation_condition evaluates true  — free-text today, so NOT auto-evaluated;
        surfaced for the human (the Claude review step). Marked `requires_human`.

Regime states (combining this fundamental gate with the event/decay gate):
  intact     — no live contradict, fundamentals healthy
  contested  — a live contradict (decayed strength >= TAU_EVT) but fundamentals still healthy
               → the DEFAULT for any single fresh contradict (a lone event can't reach breaking)
  breaking   — fundamentals degrading (OBJECTIVE/measured). Python does NOT auto-escalate off an
               event count: whether several contested developments are a regime change is judged
               by Claude in the skill (spacing + macro context) via persistence_evidence().

Thresholds are module-level defaults here (the regime signal is an additive annotation — it
does NOT enter the composite formula, so it never bumps `scoring_version`). They should move to
`scoring_weights.yaml` (`regime_discrimination:`) once calibrated against the backtest harness.

CLI:
    uv run python -m catalyx.thesis.structural_monitor <structural_id>
    uv run python -m catalyx.thesis.structural_monitor --all
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parents[2]
_STRUCTURAL_DIR = _REPO_ROOT / "catalyx" / "config" / "structural_catalysts"

# ── Tunable thresholds (defaults — see module docstring) ─────────────────────────
TAU_EVT = 50.0        # min decayed strength for a contradict to count as "live"
N_IND = 2             # >= this many weak indicators → degrading
DELTA_INT = 15.0      # intensity drop (points, vs previous cycle) → degrading
MATERIAL_MARGIN = 10.0  # a structural drives a sector if modified_score >= anchor - margin

# Persistence (Layer 2) — TIME-INDEPENDENT CONTEXT, not an automatic verdict. Everything here is
# computed over a calendar window from event timestamps (never counted across runs), so the result
# is identical whether the analysis runs daily, weekly, or monthly — the pipeline is a stateless
# render of timestamped world-state. Python surfaces the EVIDENCE; the escalation judgement (is this
# contested actually a regime change?) is LEFT TO CLAUDE in the skill: "two drops on consecutive
# days confirm nothing" — spacing and macro context decide, and that is reasoning, not arithmetic.
# News-based: 1 CatalystEvent = 1 development (article volume is deduped upstream at the scan).
WINDOW_DAYS = 45          # relevance window for a contradicting development to still "count"
CLUSTER_DAYS = 5          # distinct developments within this span = likely ONE shock reverberating
DISPERSION_MIN_DAYS = 10  # only ADVISE a review when developments are spread WIDER than this

_SEVERITY = {"intact": 0, "contested": 1, "breaking": 2}


def _parse_dt(date_str):
    if not date_str:
        return None
    s = str(date_str).replace("Z", "+00:00")
    for cand in (s, s[:10]):
        try:
            d = datetime.fromisoformat(cand)
            return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
        except ValueError:
            continue
    return None


def within_window(date_str, window_days: int = WINDOW_DAYS) -> bool:
    """True if `date_str` is within the trailing `window_days`. The clock is the WORLD's (event
    timestamps), not ours (run count) — this is what makes the read run-frequency independent.

    Event timestamps are day-granular in intent (a catalyst happened "on date X"), but may carry an
    intraday time (e.g. '...T09:00:00Z'). Comparing fractional-day deltas against `now` then made the
    verdict depend on the TIME OF DAY the watcher ran: an event stamped today@09:00Z read as OUT of
    window when evaluated before 09:00Z (days < 0). That intraday dependence contradicts this
    module's whole "run-frequency independent" design. Fix: a 1-day future grace on the lower bound
    absorbs the intraday/timezone slack of a same-day stamp (a genuinely future-scheduled event,
    >1 day out, is still excluded)."""
    d = _parse_dt(date_str)
    if d is None:
        return False
    days = (datetime.now(timezone.utc) - d).total_seconds() / 86400
    return -1.0 <= days <= window_days


def persistence_evidence(contradicts: list[dict]) -> dict:
    """Contextual dossier for Claude to judge whether a `contested` is a genuine regime shift.

    Python reports the FACTS — how many DISTINCT developments, over what calendar span, whether
    they are clustered (one shock) or dispersed (recurrence), how live they still are. It does NOT
    decide. `review_recommended` is an advisory ROUTER (worth a closer look in the monthly review),
    NOT a verdict; the call is Claude's, with macro context from WebSearch.
    `contradicts` = [{event_id, event_date, strength_decayed}] (already filtered to live + in-window).
    """
    by_id = {e.get("event_id"): e for e in contradicts}
    items = list(by_id.values())
    dts = sorted(d for d in (_parse_dt(e.get("event_date")) for e in items) if d)
    n = len(items)
    span_days = (dts[-1] - dts[0]).days if len(dts) >= 2 else 0
    clustered = len(dts) >= 2 and span_days <= CLUSTER_DAYS
    return {
        "distinct_developments": n,
        "span_days": span_days,
        "clustered_one_shock": clustered,
        # advisory only: several developments SPREAD OUT (not a single clustered shock) deserve a look
        "review_recommended": n >= 2 and span_days >= DISPERSION_MIN_DAYS,
        "events": [{"id": e.get("event_id"),
                    "date": (str(e.get("event_date"))[:10] if e.get("event_date") else None),
                    "decayed_strength": e.get("strength_decayed")} for e in items],
        "note": ("advisory — Claude makes the escalation call with macro context; clustered "
                 "developments likely reflect one shock, not persistence"),
    }


# ── Indicator / intensity reads ──────────────────────────────────────────────────

def _indicator_is_weak(ind: dict) -> bool:
    """True if the indicator's current value is on the weak side of `threshold_weak`,
    respecting `direction` (higher_is_stronger vs lower_is_stronger)."""
    cv = ind.get("current_value")
    tw = ind.get("threshold_weak")
    if cv is None or tw is None:
        return False
    try:
        cv = float(cv)
        tw = float(tw)
    except (TypeError, ValueError):
        return False
    if ind.get("direction") == "lower_is_stronger":
        return cv > tw
    return cv < tw


def _intensity_drop(sc: dict) -> float:
    """Points the intensity fell vs the previous distinct cycle (positive = falling)."""
    intn = sc.get("intensity", {}) or {}
    current = intn.get("current_score")
    hist = intn.get("history", []) or []
    scores = [h.get("score") for h in hist if isinstance(h.get("score"), (int, float))]
    if current is None or not scores:
        return 0.0
    prev = None
    for s in scores[:4]:
        if abs(float(s) - float(current)) > 1e-9:
            prev = float(s)
            break
    if prev is None:
        return 0.0
    return round(prev - float(current), 2)


def evaluate_structural(sc: dict) -> dict:
    """Fundamental-health verdict for a loaded structural catalyst YAML dict."""
    indicators = sc.get("indicators", []) or []
    weak = [i.get("id", "?") for i in indicators if _indicator_is_weak(i)]
    drop = _intensity_drop(sc)
    deacts = [d for d in (sc.get("deactivation_conditions") or [])]

    reasons = []
    if len(weak) >= N_IND:
        reasons.append(f"{len(weak)}/{len(indicators)} indicators below threshold_weak ({','.join(weak)})")
    if drop > DELTA_INT:
        reasons.append(f"intensity fell {drop} (> {DELTA_INT}) since prev cycle")

    degrading = (len(weak) >= N_IND) or (drop > DELTA_INT)
    return {
        "structural_id": sc.get("id"),
        "intensity": (sc.get("intensity", {}) or {}).get("current_score"),
        "n_indicators": len(indicators),
        "weak_indicators": weak,
        "n_weak": len(weak),
        "intensity_drop": drop,
        "degrading": degrading,
        "reasons": reasons,
        "deactivation_conditions": [d.get("condition") for d in deacts],
        "requires_human": bool(deacts),  # free-text conditions need the skill to judge
    }


# ── State classification ─────────────────────────────────────────────────────────

def classify_structural(degrading: bool, n_live_contradicts: int) -> str:
    """Python labels only the OBJECTIVE states; the interpretive escalation is Claude's.

      breaking  ← fundamentals degrading (indicators measured below threshold) — an objective fact
      contested ← >= 1 live contradicting development — a flag to WATCH, not a verdict
      intact    ← otherwise

    Python NEVER escalates to `breaking` from an event COUNT. Whether several contested
    developments constitute a regime change is a judgement — it depends on their spacing (clustered
    = one shock; dispersed = recurrence) and the macro backdrop — and that is handled by Claude in
    the skill via `persistence_evidence()`. "Two consecutive-day drops confirm nothing."
    """
    if degrading:
        return "breaking"
    if n_live_contradicts >= 1:
        return "contested"
    return "intact"


def sector_state(structural_states: list[dict], anchor_score: float | None) -> str:
    """Worst state among the structurals that *materially drive* the sector's alignment.

    Material = modified_score within MATERIAL_MARGIN of the alignment anchor. This is why a
    contradict on a non-anchoring structural (e.g. AI capex inside a grid-anchored sector) does
    NOT flag the sector, while a pure-play whose only structural is contradicted does.
    """
    if not structural_states or anchor_score is None:
        return "intact"
    material = [
        s for s in structural_states
        if s.get("modified_score") is not None
        and s["modified_score"] >= anchor_score - MATERIAL_MARGIN
    ]
    if not material:
        return "intact"
    return max((s.get("regime_state", "intact") for s in material), key=lambda st: _SEVERITY[st])


# ── CLI ──────────────────────────────────────────────────────────────────────────

def _all_structurals() -> list[Path]:
    return sorted(_STRUCTURAL_DIR.glob("*.yaml"))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX structural health monitor (noise-vs-regime bridge)")
    p.add_argument("structural_id", nargs="?", help="e.g. struct_ai_capex_supercycle (omit with --all)")
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.all or not args.structural_id:
        paths = _all_structurals()
    else:
        stem = args.structural_id.removeprefix("struct_")
        paths = [_STRUCTURAL_DIR / f"{stem}.yaml"]

    results = []
    for path in paths:
        if not path.exists():
            results.append({"structural_id": path.stem, "error": "not found"})
            continue
        sc = yaml.safe_load(path.read_text(encoding="utf-8"))
        results.append(evaluate_structural(sc))

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))
        return

    print("CATALYX — Structural Health Monitor (fundamentals gate)\n")
    for r in results:
        if r.get("error"):
            print(f"  {r['structural_id']}: {r['error']}")
            continue
        flag = "DEGRADING" if r["degrading"] else "healthy"
        # intensity_drop > 0 means the score FELL (positive = falling); degrades when > DELTA_INT
        print(f"  {r['structural_id']:<40} intensity={r['intensity']}  "
              f"weak={r['n_weak']}/{r['n_indicators']}  drop={r['intensity_drop']:+} (>{DELTA_INT}→degrade)  → {flag}")
        for reason in r["reasons"]:
            print(f"      ! {reason}")


if __name__ == "__main__":
    main()
