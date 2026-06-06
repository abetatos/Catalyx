"""Single source of truth for scoring constants.

Every scorer imports its weights from here instead of hardcoding them. This file
loads `scoring_weights.yaml` once (cached) and exposes typed accessors. If a key
is missing from the YAML, the documented Phase 0.5 default is used as a fallback,
so a partial/edited YAML degrades gracefully instead of crashing a scorer.

Why this exists: the project's stability principle is "formulas in code, no drift".
Previously the weights lived BOTH in scoring_weights.yaml and as hardcoded constants
in each scorer, so recalibrating the YAML changed nothing — the code never read it.
This module makes the YAML authoritative.

Usage:
    from catalyx.config import weights
    w = weights.composite_weights()        # dict
    h = weights.event_default_halflife()   # float
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_WEIGHTS_PATH = Path(__file__).with_name("scoring_weights.yaml")


@lru_cache(maxsize=1)
def _raw() -> dict:
    try:
        return yaml.safe_load(_WEIGHTS_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def _section(name: str, default: dict) -> dict:
    """Return a YAML mapping merged over its defaults (YAML keys win)."""
    section = _raw().get(name) or {}
    merged = dict(default)
    merged.update({k: v for k, v in section.items() if v is not None})
    return merged


# ── Composite (sector_scorer) ────────────────────────────────────────────────

# v1.6: valuation_relative removed (was a constant-50 placeholder that only diluted the
# ranking; no price-derived metric earned the 15% — see scoring_weights.yaml + the backtest).
# The weight was redistributed proportionally across the survivors (each × 1/0.85).
_COMPOSITE_DEFAULT = {
    "catalyst_alignment": 0.35,
    "momentum": 0.29,
    "flow_confirmation": 0.24,
    "crowding_risk": 0.12,
}


def composite_weights() -> dict:
    return _section("composite_weights", _COMPOSITE_DEFAULT)


# ── Momentum periods (momentum_engine) ───────────────────────────────────────

_MOMENTUM_DEFAULT = {"return_1m": 0.20, "return_3m": 0.45, "return_6m": 0.35}


def momentum_period_weights() -> dict:
    return _section("momentum_period_weights", _MOMENTUM_DEFAULT)


# ── Indicator scoring (intensity_engine) ─────────────────────────────────────

_INDICATOR_SCORING_DEFAULT = {
    "method": "percentile_with_saturating_fallback",
    "min_history_points": 6,
    "percentile_method": "mean",
    "fallback_anchors": {"weak": 50, "strong": 80},
    "fallback_above_strong_decay": 0.693,
    "clamp": [0, 100],
}


def indicator_scoring() -> dict:
    """Continuous indicator scoring config (v1.5): method, min_history_points,
    percentile_method, fallback_anchors {weak, strong}, fallback_above_strong_decay,
    clamp [lo, hi]."""
    return _section("indicator_scoring", _INDICATOR_SCORING_DEFAULT)


_COLOR_THRESHOLDS_DEFAULT = {"green": 80, "amber": 50}


def indicator_color_thresholds() -> tuple[float, float]:
    """(green, amber) cutoffs for the display-only color derived from an indicator
    score. score >= green → 🟢; >= amber → 🟡; else 🔴."""
    t = _section("indicator_color_thresholds", _COLOR_THRESHOLDS_DEFAULT)
    return float(t["green"]), float(t["amber"])


_TREND_DELTAS_DEFAULT = {
    "rising_2plus": 5,
    "rising_1": 2,
    "flat": 0,
    "falling_1": -3,
    "falling_2plus": -7,
}


def intensity_trend_deltas() -> dict:
    """Additive trend adjustments in score-space (v1.5), keyed by trend label."""
    return _section("intensity_trend_deltas", _TREND_DELTAS_DEFAULT)


def intensity_bounds() -> tuple[float, float]:
    """(min, max) clamp for the final intensity.current_score."""
    b = _section("intensity_bounds", {"min": 10, "max": 95})
    return float(b["min"]), float(b["max"])


# ── Catalyst interaction (catalyst_scorer) ───────────────────────────────────

def catalyst_interaction_deltas() -> tuple[float, float]:
    """(confirm_max_points, contradict_max_points) — additive point adjustments to
    the structural score at event_strength=100 (v1.5). Replaces the multiplicative
    confirmation_amplifier()/contradiction_dampener() below."""
    d = _section(
        "catalyst_interaction_deltas",
        {"confirm_max_points": 10, "contradict_max_points": 15},
    )
    return float(d["confirm_max_points"]), float(d["contradict_max_points"])


def confirmation_amplifier() -> float:
    """LEGACY (pre-v1.5) — multiplicative. Extra above structural at event_strength=100
    (e.g. 1.12 → 0.12 delta). Retained for backward compatibility; catalyst_scorer
    now uses catalyst_interaction_deltas()."""
    factor = _section("catalyst_interaction_factors", {"confirmation_amplifier": 1.12})
    return float(factor["confirmation_amplifier"]) - 1.0


def contradiction_dampener() -> float:
    """LEGACY (pre-v1.5) — multiplicative. Reduction below structural at
    event_strength=100 (e.g. 0.82 → 0.18 delta). Retained for backward compatibility."""
    factor = _section("catalyst_interaction_factors", {"contradiction_dampener": 0.82})
    return 1.0 - float(factor["contradiction_dampener"])


def catalyst_sub_weights() -> tuple[float, float]:
    """(structural_component, event_component) for the independent/additive formula."""
    sub = _section(
        "catalyst_alignment_sub_weights",
        {"structural_component": 0.45, "event_component": 0.55},
    )
    return float(sub["structural_component"]), float(sub["event_component"])


def event_default_halflife() -> float:
    decay = _section("event_catalyst_decay", {"default_halflife_days": 46})
    return float(decay["default_halflife_days"])


def reinforce_factor() -> float:
    """Multi-catalyst aggregation reinforcement [0,1]. 0 → max-only; 1 → full noisy-OR."""
    agg = _section("multi_catalyst_aggregation", {"reinforce_factor": 0.25})
    return float(agg["reinforce_factor"])


# ── Crowding from narrative_maturity (sector_scorer / snapshot recorder) ──────

_CROWDING_FROM_MATURITY_DEFAULT = {
    "ignored": 10,
    "emerging": 25,
    "mainstream": 55,
    "crowded": 75,
    "exhausted": 90,
}


def crowding_from_maturity() -> dict:
    """Map a sector study's `narrative_maturity` enum to a crowding_risk [0-100].
    Single source of truth — previously hardcoded in the heatmap skill prose and in
    ad-hoc scripts (drift risk). Higher maturity → more crowded → bigger composite penalty."""
    return _section("crowding_from_maturity", _CROWDING_FROM_MATURITY_DEFAULT)
