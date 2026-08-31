"""Deterministic intensity scorer for StructuralCatalyst objects.

Formula (scoring_weights.yaml §STRUCTURAL CATALYST INTENSITY, v1.5):
  1. CONTINUOUS score per indicator in [0, 100] (no more 🟢/🟡/🔴 buckets):
       method = percentile_with_saturating_fallback
       • >= min_history_points values → empirical percentile of current_value
         within the indicator's own value_history (+ current). lower_is_stronger
         inverts: score = (1 - pct) × 100.
       • otherwise (cold start) → a saturating curve anchored on the thresholds
         (weak → 50, strong → 80, asymptoting to 100 far above strong), clamped.
     The 🟢/🟡/🔴 color is now DERIVED from this score and is display-only.
  2. indicator_avg = weighted mean (equal weight unless indicator_weight set)
  3. trend_delta (ADDITIVE points) from intensity.history last 2-3 periods
  4. score = round(clamp(indicator_avg + trend_delta, min, max), 1)

All constants come from scoring_weights.yaml via catalyx.config.weights.

Usage (callable from skills via Bash):
    uv run python -m catalyx.scorer.intensity_engine <path/to/catalyst.yaml>
    uv run python -m catalyx.scorer.intensity_engine --all
    uv run python -m catalyx.scorer.intensity_engine --all --write-back   # update YAMLs in place

Write-back behaviour:
  - Updates intensity.current_score and intensity.last_updated in the YAML
  - Prepends a new entry to intensity.history (computation_method: "computed")
  - Writes the derived color to indicator.semaphore and the continuous score to
    indicator.score
  - Does NOT change indicator current_value or value_history — those are the
    user/update-skill's job (the update skill appends each new observation)

Output: JSON with computed_score, stored_score, delta, and per-indicator breakdown.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import yaml

from catalyx.config import weights

# ── Config from scoring_weights.yaml (via weights.py — single source of truth) ──

_SCORING = weights.indicator_scoring()
_MIN_HISTORY_POINTS = int(_SCORING["min_history_points"])
_ANCHOR_WEAK = float(_SCORING["fallback_anchors"]["weak"])
_ANCHOR_STRONG = float(_SCORING["fallback_anchors"]["strong"])
_ABOVE_STRONG_DECAY = float(_SCORING["fallback_above_strong_decay"])
_IND_CLAMP_LO, _IND_CLAMP_HI = (float(x) for x in _SCORING["clamp"])

# v6 J4 — detrended blend + the min_history cliff. Defaults reproduce pre-v6 when zeroed.
_DETREND_WEIGHT = float(_SCORING.get("detrend_weight", 0.5))
_DETREND_MIN_TAU = float(_SCORING.get("detrend_min_tau", 0.5))
_DETREND_MIN_POINTS = int(_SCORING.get("detrend_min_points", 6))
_BLEND_SPAN = int(_SCORING.get("history_blend_span", 2))

_COLOR_GREEN, _COLOR_AMBER = weights.indicator_color_thresholds()
_TREND_DELTAS = weights.intensity_trend_deltas()
_INTENSITY_MIN, _INTENSITY_MAX = weights.intensity_bounds()

_CATALYSTS_DIR = Path(__file__).parents[1] / "config" / "structural_catalysts"


# ── Continuous indicator scoring ─────────────────────────────────────────────

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _history_for_catalyst(catalyst_id) -> dict[str, list[dict]]:
    """Indicator value-history from the parquet lake (Tier 2 truth), {ind_id: [{date,value}]}.
    Returns {} when the lake has no history for this catalyst → callers fall back to YAML."""
    if not catalyst_id:
        return {}
    try:
        from catalyx.store import indicator_history
        return indicator_history.history_for(catalyst_id)
    except Exception:
        return {}


def _indicator_values(ind: dict, ext_history: list[dict] | None = None) -> list[float]:
    """All numeric observations for an indicator: value_history + current_value.

    `ext_history` (the lake's value_history for this indicator) takes precedence; when it
    is None the deprecated inline YAML `value_history` is used as a fallback. Entries are
    {date, value}. Returned CHRONOLOGICALLY (oldest first) with the current value last —
    the level percentile does not care, but the detrended one (v6 J4) does, and a function
    whose result depends on an ordering it does not control is a trap for the next caller.
    """
    source = ext_history if ext_history is not None else (ind.get("value_history") or [])
    dated: list[tuple[str, float]] = []
    for entry in source:
        v = entry.get("value") if isinstance(entry, dict) else entry
        if isinstance(v, (int, float)):
            d = entry.get("date") if isinstance(entry, dict) else None
            dated.append((str(d or ""), float(v)))
    dated.sort(key=lambda kv: kv[0])
    values = [v for _, v in dated]
    cur = ind.get("current_value")
    if isinstance(cur, (int, float)):
        values.append(float(cur))
    return values


def _percentile_score(value: float, sample: list[float], direction: str) -> float:
    """Empirical percentile of `value` within `sample` (the 'mean' rank method),
    mapped to [0, 100]. lower_is_stronger inverts the result."""
    n = len(sample)
    n_below = sum(1 for s in sample if s < value)
    n_equal = sum(1 for s in sample if s == value)
    pct = (n_below + 0.5 * n_equal) / n * 100.0
    if direction == "lower_is_stronger":
        pct = 100.0 - pct
    return pct


def _fallback_score(value: float, ind: dict) -> float:
    """Cold-start fallback: a SATURATING curve anchored on the thresholds.

      x = (value - weak) / (strong - weak)   # 0 at weak, 1 at strong
      x ≤ 1: linear  weak_anchor → strong_anchor   (and below weak, continues down)
      x > 1: strong_anchor + (100 - strong_anchor)·(1 - exp(-decay·(x-1)))

    The signed (strong - weak) difference makes x correct for both directions
    (for lower_is_stronger, threshold_strong < threshold_weak, so the sign flips).
    Being far above threshold_strong asymptotes toward 100 instead of clamping there,
    so over-threshold values grade by margin instead of all saturating.
    """
    strong = float(ind["threshold_strong"])
    weak = float(ind["threshold_weak"])
    if strong == weak:
        # Degenerate thresholds — fall back to a direction-aware step.
        meets = value <= strong if ind["direction"] == "lower_is_stronger" else value >= strong
        return _ANCHOR_STRONG if meets else _ANCHOR_WEAK

    x = (value - weak) / (strong - weak)
    if x <= 1.0:
        return _ANCHOR_WEAK + x * (_ANCHOR_STRONG - _ANCHOR_WEAK)
    return _ANCHOR_STRONG + (100.0 - _ANCHOR_STRONG) * (1.0 - math.exp(-_ABOVE_STRONG_DECAY * (x - 1.0)))


def _kendall_tau_vs_time(values: list[float]) -> float:
    """τ between a series and its own index — how monotonically it trends. ±1 = strictly
    monotone. Rank-based on purpose: robust to the one huge print that a Pearson r would let
    masquerade as a trend."""
    n = len(values)
    if n < 3:
        return 0.0
    conc = disc = 0
    for i in range(n):
        for j in range(i + 1, n):
            if values[j] > values[i]:
                conc += 1
            elif values[j] < values[i]:
                disc += 1
    return (conc - disc) / (n * (n - 1) / 2)


def _detrended(values: list[float]) -> list[float]:
    """Residuals against an OLS line on the index.

    The plan said "residual against a rolling mean"; a rolling window over a 6-point series
    throws away most of the sample and gives back nothing for the earliest points. A linear
    detrend keeps every observation, which is the binding constraint on series that are
    observed monthly.
    """
    n = len(values)
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return list(values)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, values)) / sxx
    a = my - b * mx
    return [y - (a + b * x) for x, y in zip(xs, values)]


def _percentile_component(cur: float, sample: list[float], direction: str) -> tuple[float, dict]:
    """The percentile half of the score: the LEVEL percentile, blended with the DETRENDED one
    when the series actually trends (v6 J4).

    D5: a persistently rising series (central-bank gold buying, hyperscaler capex) sits near its
    own maximum almost every month, so the level percentile pins at ~100 and stops discriminating
    — precisely on the persistent drivers that `StructuralCatalyst` exists to model. But "at an
    all-time high" is real information, not noise, so the detrended percentile REPLACING it would
    be the opposite error: a driver at a record level would score mid-range for the crime of being
    exactly on its own trend.

    So they are BLENDED. The level says how strong this is against its own history; the residual
    says whether it is running above or below its own trajectory. Two catalysts both at record
    highs, one accelerating and one decelerating, now score differently — which is the
    discriminating power D5 was after. `detrend_weight: 0.0` reproduces the pre-v6 score exactly.
    """
    level = _percentile_score(cur, sample, direction)
    meta = {"level_percentile": round(level, 1), "detrended_percentile": None,
            "trend_tau": None, "detrend_weight_applied": 0.0}
    if _DETREND_WEIGHT <= 0 or len(sample) < _DETREND_MIN_POINTS:
        return level, meta
    tau = _kendall_tau_vs_time(sample)
    meta["trend_tau"] = round(tau, 3)
    if abs(tau) < _DETREND_MIN_TAU:
        return level, meta                      # not trending → nothing to detrend away
    resid = _detrended(sample)
    det = _percentile_score(resid[-1], resid, direction)
    meta["detrended_percentile"] = round(det, 1)
    meta["detrend_weight_applied"] = _DETREND_WEIGHT
    return (1.0 - _DETREND_WEIGHT) * level + _DETREND_WEIGHT * det, meta


def _indicator_score(ind: dict, ext_history: list[dict] | None = None) -> float:
    return _indicator_score_detail(ind, ext_history)["score"]


def _indicator_score_detail(ind: dict, ext_history: list[dict] | None = None) -> dict:
    """Continuous [0, 100] score for one indicator, with its provenance.

    v6 J4 also removes the REGIME JUMP at the `min_history_points` boundary: the score used to
    switch outright from the saturating threshold curve to the percentile on the arrival of the
    6th observation, so one new data point could move an indicator by tens of points for reasons
    that had nothing to do with the world. The two are now blended linearly across
    `[min − blend_span, min + blend_span]` — the same family of mini-cliff v1.5 removed from the
    semaphore buckets and v6 I6 removed from the VIX brake.
    """
    cur = ind.get("current_value")
    if not isinstance(cur, (int, float)):
        return {"score": _IND_CLAMP_LO, "mode": "no_data"}

    sample = _indicator_values(ind, ext_history)
    n = len(sample)
    lo, hi = _MIN_HISTORY_POINTS - _BLEND_SPAN, _MIN_HISTORY_POINTS + _BLEND_SPAN
    fallback = _fallback_score(float(cur), ind)

    if n < lo:                       # strict: at n == lo the blend weight is 0 anyway,
        raw, mode, meta = fallback, "fallback", {}   # and `<=` made span=0 skip the
                                                     # percentile at exactly n == min
    else:
        pct, meta = _percentile_component(float(cur), sample, ind["direction"])
        if n >= hi:
            raw, mode = pct, "percentile"
        else:
            t = (n - lo) / float(hi - lo)
            raw = (1.0 - t) * fallback + t * pct
            # the mode names what the score IS: at the ends of the band the blend is degenerate
            mode = "fallback" if t <= 0 else "percentile" if t >= 1 else "blended"
            meta["blend_t"] = round(t, 2)
    return {"score": round(_clamp(raw, _IND_CLAMP_LO, _IND_CLAMP_HI), 1),
            "mode": mode, "n_observations": n, **meta}


def _color(score: float) -> str:
    """Display-only color derived from a continuous score."""
    if score >= _COLOR_GREEN:
        return "🟢"
    if score >= _COLOR_AMBER:
        return "🟡"
    return "🔴"


def _scoring_mode(ind: dict, ext_history: list[dict] | None = None) -> str:
    """How the score was actually produced: fallback | blended | percentile (v6 J4)."""
    return _indicator_score_detail(ind, ext_history).get("mode", "fallback")


# ── Trend (additive delta) ───────────────────────────────────────────────────

def _trend_delta(history: list[dict]) -> tuple[float, str]:
    """Return (additive_points, label) from the two most-recent history entries.

    History is expected most-recent-first (as written in YAML files).
    Consecutive = both the most-recent AND the prior period moved in the same direction.

    ONE ENTRY PER PERIOD, and that is not a formality. `write_back` appended a fresh entry every
    time it ran, so a second review on the same day left two `2026-08-31` rows and this function
    differenced them as two consecutive PERIODS — turning "the pipeline ran twice" into a
    `↓ falling 1 period` delta on a world that had not moved. It compounds: each re-run writes a
    lower score, which the next re-run reads as a further fall. On 2026-08-31 that alone took
    `struct_cb_gold_accumulation` 78.6 → 68.5 → 64.5, and nine of thirteen catalysts carried
    duplicate periods (up to SIX rows of `2026-Q2`). Deduping on read, keeping the most recent
    computation per period, repairs the reading for every file already written that way.
    """
    seen: set[str] = set()
    deduped = []
    for h in history:
        period = h.get("period")
        # An entry with no period is not "the same period" as another one without a period —
        # it is an unlabelled observation, and collapsing those would silently eat real history.
        if period is not None:
            if str(period) in seen:
                continue
            seen.add(str(period))
        deduped.append(h)
    history = deduped
    scores = [h["score"] for h in history if isinstance(h.get("score"), (int, float))]
    if len(scores) < 2:
        return float(_TREND_DELTAS["flat"]), "→ (flat — insufficient history)"

    d1 = scores[0] - scores[1]  # most-recent period delta

    if len(scores) >= 3:
        d2 = scores[1] - scores[2]  # prior period delta
        if d1 > 2 and d2 > 2:
            return float(_TREND_DELTAS["rising_2plus"]), "↑↑ (rising 2+ consecutive)"
        if d1 < -2 and d2 < -2:
            return float(_TREND_DELTAS["falling_2plus"]), "↓↓ (falling 2+ consecutive)"

    if d1 > 2:
        return float(_TREND_DELTAS["rising_1"]), "↑ (rising 1 period)"
    if d1 < -2:
        return float(_TREND_DELTAS["falling_1"]), "↓ (falling 1 period)"
    return float(_TREND_DELTAS["flat"]), "→ (flat)"


# ── Core logic ───────────────────────────────────────────────────────────────

def compute_intensity(catalyst: dict) -> dict:
    """Compute intensity score for a parsed StructuralCatalyst dict.

    Returns a result dict suitable for JSON serialisation and skill consumption.
    """
    indicators = catalyst.get("indicators", [])
    if not indicators:
        return {
            "id": catalyst.get("id"),
            "error": "No indicators defined",
        }

    breakdown = []
    scores_weighted: list[tuple[float, float]] = []  # (score, weight)

    ext = _history_for_catalyst(catalyst.get("id"))  # lake value-history per indicator

    for ind in indicators:
        ext_h = ext.get(ind["id"])  # None → falls back to inline YAML value_history
        score = _indicator_score(ind, ext_h)
        color = _color(score)
        weight = float(ind.get("indicator_weight") or 1.0)
        scores_weighted.append((score, weight))

        stored_color = ind.get("semaphore", "")
        breakdown.append({
            "id": ind["id"],
            "name": ind.get("name", ""),
            "current_value": ind.get("current_value"),
            "unit": ind.get("unit", ""),
            "direction": ind["direction"],
            "threshold_strong": ind["threshold_strong"],
            "threshold_weak": ind["threshold_weak"],
            "scoring_mode": _scoring_mode(ind, ext_h),
            "history_points": len(_indicator_values(ind, ext_h)),
            "history_source": "lake" if ext_h is not None else "yaml",
            "indicator_score": score,
            "color_computed": color,
            "color_stored": stored_color,
            "color_drift": color != stored_color and bool(stored_color),
            "weight": weight,
        })

    total_weight = sum(w for _, w in scores_weighted)
    indicator_avg = sum(s * w for s, w in scores_weighted) / total_weight

    # The trend is measured over PRIOR periods. A history row stamped with today's date is this
    # same computation's own earlier estimate, not a period that has been through, and feeding it
    # back in makes the score a fixed-point iteration on itself: run the pipeline twice and gold
    # went 78.6 → 68.5 → 64.5 while the world stood still. Dropping it makes `compute` give the
    # same answer whether or not `write_back` has already run today — which is what lets a review
    # be re-run safely. An explicit `--period` label (e.g. `2026-Q3`) never matches and is kept.
    today_iso = date.today().isoformat()
    history = [h for h in catalyst.get("intensity", {}).get("history", [])
               if str(h.get("period")) != today_iso]
    trend_delta, trend_label = _trend_delta(history)

    raw = indicator_avg + trend_delta
    computed_score = round(_clamp(raw, _INTENSITY_MIN, _INTENSITY_MAX), 1)

    stored = catalyst.get("intensity", {}).get("current_score")
    delta = round(computed_score - stored, 1) if stored is not None else None

    return {
        "id": catalyst.get("id"),
        "computed_score": computed_score,
        "stored_score": stored,
        "delta": delta,
        "indicator_avg": round(indicator_avg, 2),
        "trend_delta": trend_delta,
        "trend_label": trend_label,
        "capped": raw != computed_score,
        "breakdown": breakdown,
    }


def compute_from_yaml(path: Path) -> dict:
    """Load a YAML file and compute intensity. Returns result dict."""
    catalyst = yaml.safe_load(path.read_text(encoding="utf-8"))
    result = compute_intensity(catalyst)
    result["_source_file"] = str(path)
    return result


# Estados que NO se recomputan (universo v2.0, 2026-08-27). Recalcular su intensidad
# era trabajo puro: ninguno llega a catalyst_alignment porque ningun sector study los
# lista en active_catalyst_ids.
#   merged        -> fusionado en otro catalizador; su fichero se conserva por historia
#   deactivated   -> invalidado
#   macro_context -> regimen que informa sizing/timing, no una posicion expresable
_SKIP_STATUS = {"merged", "deactivated"}


def compute_all(include_inactive: bool = False) -> list[dict]:
    """Compute intensity for every ACTIVE YAML in the structural_catalysts directory.

    include_inactive=True fuerza el barrido completo (auditoria, migraciones).
    """
    import yaml as _yaml
    results = []
    for f in sorted(_CATALYSTS_DIR.glob("*.yaml")):
        if not include_inactive:
            try:
                head = _yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception:
                head = {}
            if head.get("status") in _SKIP_STATUS or head.get("role") == "macro_context":
                continue
        results.append(compute_from_yaml(f))
    return results


def write_back(path: Path, result: dict, period: str | None = None) -> None:
    """Write computed score back into the YAML file in place.

    Uses ruamel.yaml to preserve original formatting, comments, and block scalars.

    Updates:
      - intensity.current_score, computation_method, last_updated
      - intensity.history — prepends new entry (skips if same period+score already logged)
      - indicators[*].semaphore — set to the derived display color
      - indicators[*].score — set to the continuous indicator score
      Does NOT touch indicator current_value or value_history.
    """
    from ruamel.yaml import YAML as RuamelYAML
    from ruamel.yaml.comments import CommentedMap

    ry = RuamelYAML()
    ry.preserve_quotes = True
    ry.width = 120

    with path.open("r", encoding="utf-8") as fh:
        catalyst = ry.load(fh)

    today = date.today().isoformat()
    new_score = result["computed_score"]
    stored_score = result.get("stored_score")
    breakdown = result.get("breakdown", [])

    intensity = catalyst["intensity"]
    intensity["current_score"] = new_score
    intensity["computation_method"] = "computed"
    intensity["last_updated"] = today

    history = intensity["history"]
    entry_period = period or today
    # One entry per period: a re-run REPLACES that period's row instead of stacking a second one
    # beside it. The old guard only skipped when period AND score matched, so a same-day re-run
    # with a moved score appended a duplicate that `_trend_delta` then read as a period change.
    existing = next((i for i, h in enumerate(history)
                     if str(h.get("period")) == str(entry_period)), None)
    already_logged = existing is not None and history[existing].get("score") == new_score
    if existing is not None and not already_logged:
        history.pop(existing)          # replace that period's row, never stack a second one
    if not already_logged:
        note = f"Computed {today}: avg={result['indicator_avg']} trend={result['trend_label']}"
        if stored_score is not None and stored_score != new_score:
            note += f" (was {stored_score})"
        new_entry = CommentedMap({
            "period": entry_period,
            "score": new_score,
            "note": note,
            "computation_method": "computed",
        })
        history.insert(0, new_entry)

    by_id = {b["id"]: b for b in breakdown}
    for ind in catalyst.get("indicators", []):
        b = by_id.get(ind["id"])
        if b is not None:
            ind["semaphore"] = b["color_computed"]
            ind["score"] = b["indicator_score"]

    with path.open("w", encoding="utf-8") as fh:
        ry.dump(catalyst, fh)


# ── CLI ────────────────────────────────────────────────────────────────────────

def _format_result(r: dict) -> str:
    """Human-readable one-line summary of a result."""
    if "error" in r:
        return f"  {r['id']}: ERROR — {r['error']}"
    delta_str = f"  Δ={r['delta']:+.1f}" if r["delta"] is not None else ""
    drift = [b for b in r.get("breakdown", []) if b.get("color_drift")]
    drift_str = f"  ⚠ color drift: {[b['id'] for b in drift]}" if drift else ""
    return (
        f"  {r['id']}: computed={r['computed_score']}  "
        f"stored={r['stored_score']}{delta_str}  "
        f"avg={r['indicator_avg']}  trend={r['trend_label']} ({r['trend_delta']:+g}){drift_str}"
    )


def compare_legacy() -> dict:
    """Every catalyst's intensity under v6 J4 against the pre-v6 formula (v6 J4 migration aid).

    J4 changes PUBLISHED scores, so the change has to be inspectable rather than asserted. Both
    halves are switched off by zeroing their config, so the legacy number is the same code path
    with `detrend_weight = 0` and `history_blend_span = 0` — not a reimplementation that could
    drift from what actually ran.

    Nothing is written. The stored `intensity.current_score` (which is what `catalyst_scorer`
    reads) is untouched until someone runs `--write-back`.
    """
    global _DETREND_WEIGHT, _BLEND_SPAN
    keep = (_DETREND_WEIGHT, _BLEND_SPAN)

    def sweep():
        out = {}
        for f in sorted(_CATALYSTS_DIR.glob("*.yaml")):
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if d.get("status") in ("merged", "deactivated") or not d.get("id"):
                continue
            try:
                r = compute_intensity(d)
            except Exception:
                continue
            out[d["id"]] = (r.get("computed_score"), r.get("stored_score"))
        return out

    try:
        _DETREND_WEIGHT, _BLEND_SPAN = 0.0, 0
        legacy = sweep()
        _DETREND_WEIGHT, _BLEND_SPAN = 0.5, 0
        detrend_only = sweep()
        _DETREND_WEIGHT, _BLEND_SPAN = 0.0, int(_SCORING.get("history_blend_span", 2))
        cliff_only = sweep()
        _DETREND_WEIGHT, _BLEND_SPAN = keep
        current = sweep()
    finally:
        _DETREND_WEIGHT, _BLEND_SPAN = keep

    rows = []
    for cid, (now, stored) in current.items():
        was = legacy.get(cid, (None, None))[0]
        if now is None or was is None:
            continue
        d_only = abs((detrend_only.get(cid, (None,))[0] or 0) - was) > 0.05
        c_only = abs((cliff_only.get(cid, (None,))[0] or 0) - was) > 0.05
        rows.append({
            "catalyst_id": cid, "stored_score": stored, "legacy_score": was,
            "new_score": now, "delta": round(now - was, 1),
            "cause": ("detrend" if d_only and not c_only
                      else "history blend" if c_only and not d_only
                      else "both" if d_only and c_only else None),
        })
    rows.sort(key=lambda r: -abs(r["delta"]))
    return {"n_catalysts": len(rows), "n_changed": sum(1 for r in rows if abs(r["delta"]) > 0.05),
            "max_abs_delta": max((abs(r["delta"]) for r in rows), default=0.0),
            "note": "stored scores are UNCHANGED until --write-back; catalyst_scorer reads stored",
            "catalysts": rows}


def render_comparison(res: dict) -> str:
    out = [f"CATALYX — intensity, v6 J4 vs the pre-v6 formula   {res['n_changed']}/"
           f"{res['n_catalysts']} catalysts change · max |Δ| {res['max_abs_delta']:.1f}", ""]
    hdr = f"  {'catalyst':<46} {'stored':>7} {'pre-v6':>7} {'now':>7} {'Δ':>7}  cause"
    out += [hdr, "  " + "-" * (len(hdr) - 2)]
    for r in res["catalysts"]:
        st = f"{r['stored_score']:.1f}" if r["stored_score"] is not None else "—"
        out.append(f"  {r['catalyst_id']:<46} {st:>7} {r['legacy_score']:>7.1f} "
                   f"{r['new_score']:>7.1f} {r['delta']:>+7.1f}  {r['cause'] or ''}")
    out += ["", "  `stored` is what catalyst_scorer reads TODAY and it does not move until "
                "--write-back.\n  `detrend` cuts both ways on purpose: it scores a series "
                "against its own trajectory, so it\n  pulls down a driver pinned at its record "
                "AND lifts one sitting above its own decline."]
    return "\n".join(out)


def main() -> None:
    # Force UTF-8 on Windows consoles
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CATALYX intensity engine — compute structural catalyst scores from continuous indicator scores"
    )
    parser.add_argument(
        "yaml_file", nargs="?", type=Path,
        help="Path to a structural catalyst YAML. Omit to use --all."
    )
    parser.add_argument(
        "--all", action="store_true",
        help=f"Compute all catalysts in {_CATALYSTS_DIR}"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON (default: human-readable summary + JSON)"
    )
    parser.add_argument(
        "--write-back", action="store_true",
        help="Write computed score back to YAML file(s) in place"
    )
    parser.add_argument(
        "--compare-legacy", action="store_true",
        help="v6 J4 migration aid: every catalyst's intensity now vs the pre-v6 formula, with "
             "the change attributed to the detrend or the history blend. Writes nothing.")
    parser.add_argument(
        "--period", type=str, default=None,
        help="Period label for history entry, e.g. '2026-Q2' (default: today's date)"
    )
    args = parser.parse_args()

    if args.compare_legacy:
        res = compare_legacy()
        print(json.dumps(res, indent=2, ensure_ascii=False) if args.json else render_comparison(res))
        return

    if args.all or args.yaml_file is None:
        results = compute_all()
    else:
        results = [compute_from_yaml(args.yaml_file)]

    if args.write_back:
        # Each result carries the file it was computed FROM. It used to be zipped against a fresh
        # `glob("*.yaml")`, but `compute_all` SKIPS inactive/macro_context catalysts — so the two
        # lists had different lengths and every result after the first skipped file was written
        # into the wrong catalyst's YAML. On 2026-08-31 that put gold's 68.5 into
        # biopharma_patent_cliff, ai_capex's 95.0 into commercial_space, and left the last five
        # files untouched. Never re-derive the path; carry it.
        for result in results:
            if "error" in result:
                continue
            path = Path(result["_source_file"])
            write_back(path, result, period=args.period)
            print(f"Updated: {path.name}  score={result['computed_score']}", file=sys.stderr)

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))
        return

    print("CATALYX — Structural Catalyst Intensity Engine\n")
    for r in results:
        print(_format_result(r))
        if "breakdown" in r:
            for b in r["breakdown"]:
                drift_flag = " ⚠ DRIFT" if b.get("color_drift") else ""
                print(
                    f"    {b['id']}: {b['color_computed']} "
                    f"score={b['indicator_score']}  val={b['current_value']} {b['unit']}"
                    f"  [{b['scoring_mode']}, n={b['history_points']}]{drift_flag}"
                )
        print()



if __name__ == "__main__":
    main()
