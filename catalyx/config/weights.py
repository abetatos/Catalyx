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


# ── Portfolio weighting (portfolio.build_model_holdings) ─────────────────────

_PORTFOLIO_WEIGHTING_DEFAULT = {
    "transform": "proportional",
    "sharpness": 0.25,
    "rebalance_deadband_pct": 1.0,
}


def portfolio_weighting() -> dict:
    """Conviction-sizing config: `transform` (proportional|softmax), `sharpness` (softmax
    dispersion dial), `rebalance_deadband_pct` (turnover guard). Single source of truth;
    a portfolio profile's `construction` may override any of these per book. See
    scoring_weights.yaml `portfolio_weighting`."""
    return _section("portfolio_weighting", _PORTFOLIO_WEIGHTING_DEFAULT)


# ── Track record inception (nav_engine live curve) ───────────────────────────

_TRACK_RECORD_PATH = _WEIGHTS_PATH.with_name("track_record.yaml")


@lru_cache(maxsize=1)
def track_record() -> dict:
    """Inception anchor for the FORWARD (live, no-look-ahead) track record. Read from
    catalyx/config/track_record.yaml — versioned so the start date is traceable to a
    release. Keys: `inception_date` (YYYY-MM-DD), `inception_release`. Empty if absent."""
    try:
        return yaml.safe_load(_TRACK_RECORD_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def track_record_inception() -> str | None:
    """Inception date (YYYY-MM-DD) for the live track record, or None if not set."""
    d = track_record().get("inception_date")
    return str(d) if d else None


def total_capital_eur() -> float | None:
    """Total capital committed to the real book (deployed progressively as catalysts
    fire; the rest is cash). From track_record.yaml `total_capital_eur`, or None."""
    v = track_record().get("total_capital_eur")
    return float(v) if v is not None else None


# ── Entry-timing overlay (entry_timing) ──────────────────────────────────────

_ENTRY_TIMING_DEFAULT = {
    "lookback_days": 150,
    "rsi_period": 14,
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "ma_stretch_window": 20,
    "stretch_overbought_pct": 8.0,
    "vol_ratio_window_short": 10,
    "vol_ratio_window_long": 90,
    "vol_ratio_elevated": 1.5,
    "short_trend_window": 5,
    "trend_deadband_k": 0.6,
    "drawdown_local_high_window": 20,
    "stabilization_up_closes": 2,
    "stabilization_reclaim_ma": 5,
    "overhang_window_days": 21,
    "overhang_magnitudes": ["high", "extreme"],
    "overhang_min_strength": 60,
    "market_tension_ticker": "^VIX",
    "benchmark": "SPY",
}


def entry_timing() -> dict:
    """Thresholds for the execution-timing overlay (entry_timing.py): RSI bounds, MA-stretch,
    realized-vol ratio, stabilization rule, and the near-term event-overhang window. NOT part of
    the composite — a recommend-only timing layer. See scoring_weights.yaml `entry_timing`."""
    return _section("entry_timing", _ENTRY_TIMING_DEFAULT)


# ── Exit signals (exit_watcher — Family 1 of the sell-signal layer) ──────────

_EXIT_SIGNALS_DEFAULT = {
    "lookback_days": 60,
    "approach_pct": 5.0,
}


def exit_signals() -> dict:
    """Thresholds for the exit watcher (exit_watcher.py, Family 1): `lookback_days` of price history
    to pull, `approach_pct` (a not-yet-breaching stop within this % of its threshold reads as
    'approaching'). Recommend-only sell-side layer; NOT part of the composite. See
    scoring_weights.yaml `exit_signals` + docs/DESIGN_sell_signals.md."""
    return _section("exit_signals", _EXIT_SIGNALS_DEFAULT)


# ── Deep technical study (technical_study — opt-in pre-open TA dossier) ───────

_TECHNICAL_STUDY_DEFAULT = {
    "lookback_days": 420,
    "min_history": 30,
    "slope_lag": 5,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "bollinger_window": 20,
    "bollinger_k": 2.0,
    "atr_period": 14,
    "pivot_left": 3,
    "pivot_right": 3,
    "obv_window": 20,
    "volume_window": 20,
    "volume_surge_mult": 1.5,
    "range_window": 252,
    "posture_margin": 2,
}


def technical_study() -> dict:
    """Periods/thresholds for the deep technical study (technical_study.py): MA/MACD/Bollinger/ATR
    windows, swing-pivot fractal size, volume/OBV windows, 52-week range window, and the posture
    margin. Opt-in, recommend-only, ephemeral — NOT part of the composite. See scoring_weights.yaml
    `technical_study`."""
    return _section("technical_study", _TECHNICAL_STUDY_DEFAULT)


# ── Dislocation lens (dislocation.py — opportunities + rotation targets) ──────

_DISLOCATION_DEFAULT = {
    "drawdown_threshold_pct": -3.0,
    "min_catalyst_alignment": 70.0,
    "min_opportunity_composite": 55.0,
    "max_diversifier_corr": 0.65,
    "min_diversifier_composite": 50.0,
}


def dislocation() -> dict:
    """Thresholds for the dislocation lens (dislocation.py). OPPORTUNITY (panic-dip buy): drawdown
    floor + catalyst floor + the NON-NEGOTIABLE full-blend composite floor. DIVERSIFIER (rotation
    target): `max_diversifier_corr` (mean corr to the anchor/stressed cluster ≤ this) + a SEPARATE,
    looser `min_diversifier_composite` so more genuine diversifiers surface without weakening the
    opportunity lens. Recommend-only; NOT part of the composite. See scoring_weights.yaml
    `dislocation`."""
    return _section("dislocation", _DISLOCATION_DEFAULT)
