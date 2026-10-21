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
    "momentum": 0.275,
    "flow_confirmation": 0.24,
    "crowding_risk": 0.135,
}


def composite_weights() -> dict:
    return _section("composite_weights", _COMPOSITE_DEFAULT)


# v6 H1: the composite combines standardized dimensions, so a nominal weight is the
# effective weight. See sector_scorer.commensurate().
_COMPOSITE_SCALE_DEFAULT = {
    "z_scale": 15.0,
    "winsor_z": 3.0,
    "min_universe": 5,
    "dead_dimension_sigma": 2.0,
}


def composite_scale() -> dict:
    return _section("composite_scale", _COMPOSITE_SCALE_DEFAULT)


# ── Momentum periods (momentum_engine) ───────────────────────────────────────

# v6 H4: return_1m dropped to 0 — the last month is the reversal window (see scoring_weights.yaml).
_MOMENTUM_DEFAULT = {"return_1m": 0.0, "return_3m": 0.5625, "return_6m": 0.4375}


def momentum_period_weights() -> dict:
    return _section("momentum_period_weights", _MOMENTUM_DEFAULT)


# v8 Q1/Q2 — official-dimension specs. Defaults restore the pre-v8 behavior.
_MOMENTUM_SPEC_DEFAULT = {
    "mode": "3m6m",
    "blend": {"momentum_12_1": 0.635, "near_52w_high": 0.365},
}

_CROWDING_SOURCE_DEFAULT = {"mode": "label"}


def momentum_spec() -> dict:
    return _section("momentum_spec", _MOMENTUM_SPEC_DEFAULT)


def crowding_source() -> dict:
    return _section("crowding_source", _CROWDING_SOURCE_DEFAULT)


# ── Indicator scoring (intensity_engine) ─────────────────────────────────────

_INDICATOR_SCORING_DEFAULT = {
    "method": "percentile_with_saturating_fallback",
    "min_history_points": 6,
    "percentile_method": "mean",
    "fallback_anchors": {"weak": 50, "strong": 80},
    "fallback_above_strong_decay": 0.693,
    "clamp": [0, 100],
    # v6 J4. Zeroing detrend_weight and history_blend_span reproduces the pre-v6 score exactly.
    "detrend_weight": 0.5,
    "detrend_min_tau": 0.5,
    "detrend_min_points": 6,
    "history_blend_span": 2,
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


_TAXONOMY_PATH = _WEIGHTS_PATH.with_name("catalyst_taxonomy.yaml")


@lru_cache(maxsize=1)
def catalyst_type_halflives() -> dict[str, float]:
    """`{catalyst_type: halflife_days}` from catalyst_taxonomy.yaml (v11 P3).

    The table the scan skill has told authors to use since it was written, now readable by the
    code that consumes the number. Types whose halflife is null (the structural ones — a
    `StructuralCatalyst` does not decay) are omitted rather than mapped to 0, because 0 means
    "never decays" in `_decayed_strength` and would be the opposite of a missing value.
    """
    try:
        data = yaml.safe_load(_TAXONOMY_PATH.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError):                # pragma: no cover - defensive
        return {}
    types = data.get("catalyst_types") or {}
    out: dict[str, float] = {}
    if not isinstance(types, dict):
        return out
    for name, spec in types.items():
        hl = (spec or {}).get("decay_halflife_days") if isinstance(spec, dict) else None
        if hl is not None:
            out[str(name)] = float(hl)
    return out


def halflife_source() -> str:
    """`stored` (the event's own field wins) or `taxonomy` (the per-type table wins).

    Ships as `stored`. See scoring_weights.yaml `event_catalyst_decay` for why the correct end
    state is not the safe default TODAY: adopting the table would move a held position's CA on
    the authority of a constant six reviews declined to use, which decides the open question
    instead of surfacing it."""
    decay = _section("event_catalyst_decay", {"halflife_source": "stored"})
    return str(decay.get("halflife_source", "stored"))


def event_strength_calibration() -> dict:
    """Rank calibration for the one free cardinal an LLM still assigns (v11 P4).

    `mode`: `rank_within_population` re-spaces each event's strength by its percentile inside
    the registered-event population, mapped back onto that population's own range (so the
    transform is range-preserving and monotone); `raw` restores the pre-v11 level."""
    return _section("event_strength_calibration",
                    {"mode": "rank_within_population", "min_population": 12})


def strength_score_rubric() -> list[dict]:
    """The 5 observable questions `strength_score` is derived from (v11 P2), weights summing
    to 100. Same shape as `novelty_score_rubric` — a cardinal is only reproducible across
    sessions if it decomposes into things two sessions can both observe."""
    r = _raw().get("strength_score_rubric")
    return list(r) if isinstance(r, list) else []


def strength_anchoring() -> dict:
    """Anchor-bracket enforcement for `strength_score` (v11 P2). CLAUDE.md rule 3 made
    mechanical: an event names one registered event it is stronger than and one it is weaker
    than, and the assigned score must fall inside that bracket."""
    return _section("strength_anchoring",
                    {"required": True, "enforce_bracket": True, "max_rubric_divergence": 15})


def event_tape_check() -> dict:
    """Window/benchmark config for the event tape check (v11 P6) — the abnormal move of the
    sector vehicle after an event, in units of its own vol, signed by what the event claims.
    Candidate column, weight 0; it NAMES an unconfirmed event, it never downgrades one."""
    return _section("event_tape_check",
                    {"window_days": 5, "benchmark": "SPY", "vol_lookback_days": 120,
                     "min_vol_days": 30, "max_event_age_days": 400})


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


# v7 M4 — narrative_maturity → priced-in fraction, for the `ca_unpriced` candidate column.
# Same steps as `is_priced_in_levels`; the imputation prior for a catalyst with neither
# signal is the median step (0.5), never the worst case (v6 H2 doctrine).
_MATURITY_PRICED_IN_DEFAULT = {
    "ignored": 0.0,
    "emerging": 0.25,
    "mainstream": 0.50,
    "crowded": 0.75,
    "exhausted": 1.0,
}

PRICED_IN_PRIOR = 0.5


def maturity_priced_in() -> dict:
    """Map a structural catalyst's `narrative_maturity` to a priced-in fraction [0, 1]."""
    return _section("maturity_priced_in", _MATURITY_PRICED_IN_DEFAULT)


# ── Portfolio weighting (portfolio.build_model_holdings) ─────────────────────

_PORTFOLIO_WEIGHTING_DEFAULT = {
    "transform": "proportional",
    "sharpness": 0.25,
    "rebalance_deadband_pct": 1.0,
    # Risk-budgeted sizing. 0 = the pre-v4 behaviour (vol ignored); see scoring_weights.yaml.
    "vol_tilt_alpha": 0.0,
    "vol_lookback_days": 120,
    "min_vol_pct": 5.0,
    # Skill shrinkage. False = the pre-v4 behaviour (λ=1, the tilt is taken on trust).
    "tilt_shrinkage": False,
    "tilt_ic_target": 0.20,
    "tilt_prior_windows": 3.0,
    "tilt_lambda_floor": 0.0,
    # CA conviction tilt (v10 P8). A DECLARED PRIOR, deliberately outside the λ machinery, and
    # the default is ACTIVE — the catalyst score is the core of the mandate and is never
    # disabled (user doctrine 2026-09-01): a missing YAML section must not quietly turn it off.
    "ca_tilt_delta": 0.20,
    "ca_tilt_winsor_z": 2.5,
    "ca_tilt_delta_min": 0.05,
    # Which scale the tilt z-scores (v10 P8b): "logit" (in force) | "level" (the P8 behaviour).
    "ca_tilt_scale": "logit",
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


# ── Book shape — the one knob the position cap and the tiers derive from (v6 L1) ─────

_BOOK_SHAPE_DEFAULT = {
    "n_target": 6,
    "tier_multiples": [1.4, 1.0, 0.5],
    "trade_budget": {"free_per_month": 10, "reserve_for_events": 3,
                     "planned_max_per_review": 6, "fee_after_free_eur": None,
                     "exempt_actions": ["SELL", "REDUCE"]},
}


def deploy_max() -> float:
    """Highest deployment ratio the rules can ACTUALLY ask for, as a fraction. The number the
    position cap must be feasible against: a ceiling that cannot absorb the deployment the same
    config demands resolves as a permanent shortfall or a permanent breach.

    v12 B3 — the step term was read off `intact_rank_max` (8) alone, giving 0.85. But
    `n_intact` is counted over the MODEL BOOK (`rebalance.py`), which holds at most
    `n_target` = 6 names, so `base + step·(8−5)` describes a state the counter cannot reach:
    the true ceiling is 0.75 and the live run already prints `base 0.70 + 0.05×(6−5) → 75%`,
    pinned there. `intact_rank_max: 8` is a leftover from when `max_positions` was 10.
    The number matters far beyond the deployment target, because `book_shape` reads the whole
    ladder off it: an unreachable 0.85 inflated the neutral weight, and with it every
    conviction tier and `max_position_pct` — the one constant this project insists is DERIVED.
    So the count is bounded by what can be counted. `test_config_feasibility` now asserts the
    link directly against `deploy_ratio`, which is what would have caught this.
    """
    d = rebalance_rules().get("deployment", {}) or {}
    base = float(d.get("base", 0.70))
    step = float(d.get("step_per_intact_sector", 0.05))
    intact_min = float(d.get("intact_min", 5))
    n_target = max(1, int(_section("book_shape", _BOOK_SHAPE_DEFAULT).get("n_target", 6)))
    intact_max = min(float(d.get("intact_rank_max", 8)), float(n_target))
    raw = base + step * max(0.0, intact_max - intact_min)
    return max(float(d.get("floor", 0.40)), min(float(d.get("ceiling", 1.00)), raw))


def book_shape() -> dict:
    """Position cap and conviction tiers DERIVED from `n_target` (v6 L1).

    The cap is a lower bound on the position COUNT (`n_min = deploy_max / cap`), so at 12% it
    silently demanded an 8-position book — 8 of the operator's 10 free monthly trades. Now
    `n_target` is the declared number and the rest follows; `test_config_feasibility` fails
    the suite if the triple stops being satisfiable. Percentages, not fractions.
    """
    c = _section("book_shape", _BOOK_SHAPE_DEFAULT)
    n = max(1, int(c.get("n_target", 6)))
    mult = [float(m) for m in (c.get("tier_multiples") or [1.4, 1.0, 0.5])]
    dmax = deploy_max()
    neutral = dmax * 100.0 / n
    tiers = {i: round(neutral * m, 1) for i, m in enumerate(mult, 1)}
    tb = {**_BOOK_SHAPE_DEFAULT["trade_budget"], **(c.get("trade_budget") or {})}
    return {"n_target": n, "tier_multiples": mult, "deploy_max": dmax,
            "neutral_weight_pct": round(neutral, 2), "tiers": tiers,
            "max_position_pct": float(tiers[1]),
            # The SAME ceiling expressed on the base the model weights actually live in (v13.2).
            # Model weights are fractions of the DEPLOYED book, so equal weight on n_target names
            # is 100/n there — and `deploy_max/n` of total capital. The tier-1 ceiling is
            # therefore `mult[0] × 100/n`, and `deploy_max` cancels out. Applying the
            # total-capital 17.5 to a deployed-base weight left 0.83pp of room over neutral
            # instead of 6.67pp, flattening every sizing step to equal weight by arithmetic.
            "max_position_pct_deployed": round(100.0 * mult[0] / n, 2),
            "trade_budget": tb}


def conviction_tiers() -> dict:
    """`{tier: max_position_pct}` as PERCENT, derived from `book_shape`. Multiples of the
    neutral weight, so a tier keeps meaning "1.4× a normal line" whatever `n_target` becomes;
    the old 12/8/4 absolutes all sat below the neutral weight of a 6-name book."""
    return dict(book_shape()["tiers"])


def trade_budget() -> dict:
    """Monthly trading allowance and what a scheduled review may spend of it (v6 L3).
    `reserve_for_events` is the option value of a slot made explicit — the mandate acts on
    catalysts arriving between reviews. `fee_after_free_eur` is a broker fact, None until
    observed."""
    return dict(book_shape()["trade_budget"])


def correlated_catalyst_cap() -> dict:
    """Max combined allocation across positions sharing a primary catalyst, as a PERCENT.

    The YAML stores `max_combined_pct: 0.20`, i.e. a fraction under a `_pct` name. Every caller
    that compares it to a real percentage (an exposure of 10.0 for 10%) silently never breaches.
    Normalized here, once, so the unit is the one the field name promises.
    """
    c = _section("correlated_catalyst_cap", {"max_combined_pct": 0.30, "enforcement": "warn"})
    v = float(c.get("max_combined_pct", 0.30))
    # `max_cluster_variance_pct` (v10 P6) is a SHARE OF BOOK VARIANCE and is stored as a real
    # percent — it is never a fraction, because a variance share of 0.35 would mean a third of a
    # percent and quietly never bind. It gets no <=1.0 normalization for exactly that reason:
    # the ambiguity that made `max_combined_pct` need one is not present here, and inventing it
    # would make 0.35 unwritable.
    return {"max_combined_pct": v * 100.0 if v <= 1.0 else v,
            "enforcement": str(c.get("enforcement", "warn")),
            "max_cluster_variance_pct": float(c.get("max_cluster_variance_pct", 35.0)),
            "risk_enforcement": str(c.get("risk_enforcement", "warn"))}


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


# ── Catalyst lifecycle (catalyst_lifecycle — deterministic status transitions) ──

_CATALYST_LIFECYCLE_DEFAULT = {
    "governance": "auto",
    "event_driven_update_delta_pct": 0.10,
    "event_promote_to_structural_cycles": 3,
    "event_archive_strength_below": 20,
    "event_archive_priced_in_min": 0.75,
    "structural_dormant_intensity_below": 40,
    "structural_dormant_consecutive_cycles": 2,
    "structural_dormant_if_exhausted": True,
}


def catalyst_lifecycle() -> dict:
    """Thresholds for the deterministic catalyst lifecycle (catalyst_lifecycle.py): when a spent
    event is archived, when a weak structural goes dormant (and reactivates), and the governance
    mode (`auto` applies, `ask` prompts). These rules used to be applied by LLM judgment inside
    the review skill — pure arithmetic, so they moved to Python. See scoring_weights.yaml
    `catalyst_lifecycle`."""
    return _section("catalyst_lifecycle", _CATALYST_LIFECYCLE_DEFAULT)


def entry_timing() -> dict:
    """Thresholds for the execution-timing overlay (entry_timing.py): RSI bounds, MA-stretch,
    realized-vol ratio, stabilization rule, and the near-term event-overhang window. NOT part of
    the composite — a recommend-only timing layer. See scoring_weights.yaml `entry_timing`."""
    return _section("entry_timing", _ENTRY_TIMING_DEFAULT)


# ── Exit signals (exit_watcher — Family 1 of the sell-signal layer) ──────────

_EXIT_SIGNALS_DEFAULT = {
    "lookback_days": 60,
    "approach_pct": 5.0,
    "drawdown_reduce_pct": -20.0,
    "drawdown_exit_pct": -30.0,
    "catalyst_staleness_warn_days": 30,
    "catalyst_staleness_max_days": 45,
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
    "min_opportunity_composite_z": 0.33,
    "max_diversifier_corr": 0.65,
    "min_diversifier_composite": 50.0,
    "min_diversifier_composite_z": 0.0,
}


def composite_floor(cfg: dict, z_key: str, legacy_key: str) -> float:
    """A composite floor as a level, from its z-unit key (v6 H3), falling back to the pre-v6
    absolute key for one major version. See sector_scorer.commensurate()."""
    z = cfg.get(z_key)
    if z is not None:
        return 50.0 + float(composite_scale().get("z_scale", 15.0)) * float(z)
    return float(cfg[legacy_key])


def dislocation() -> dict:
    """Thresholds for the dislocation lens (dislocation.py). OPPORTUNITY (panic-dip buy): drawdown
    floor + catalyst floor + the NON-NEGOTIABLE full-blend composite floor. DIVERSIFIER (rotation
    target): `max_diversifier_corr` (mean corr to the anchor/stressed cluster ≤ this) + a SEPARATE,
    looser `min_diversifier_composite` so more genuine diversifiers surface without weakening the
    opportunity lens. Recommend-only; NOT part of the composite. See scoring_weights.yaml
    `dislocation`."""
    return _section("dislocation", _DISLOCATION_DEFAULT)


# ── Rebalance rules (rebalance.py — the decision table, not the LLM) ─────────

_REBALANCE_RULES_DEFAULT = {
    "deadband_pp": 2.0,
    "min_ticket_eur": 150.0,
    # Derived from `book_shape` (v6 L1) = tier 1 = 1.4 × (deploy_max / n_target). Mirrored here
    # like every other default; `test_config_feasibility` asserts YAML == derived, so the
    # duplication cannot drift silently. Do not edit this without editing `book_shape.n_target`.
    "max_position_pct": 20.0,
    "spread_bps": 20.0,
    "fee_eur": 0.0,
    "add_if": {"rank_max": 5, "gap_pp_min": 3.0, "regime_not": ["breaking"]},
    "buy_if": {"rank_max": 8, "gap_pp_min": 4.0, "regime_not": ["contested", "breaking"],
               "maturity_not": ["exhausted"]},
    "trim_if": {"overweight_pp_min": 4.0},
    # ONE rung, rank-coupled, by the 2026-08-28 freeze: the default exit is trailing rank, and
    # a fixed-gain trim of a position the model still leads is the disposition effect.
    "profit_ladder": [{"gain_pct_min": 25.0, "rank_min": 6, "trim_fraction": 0.33}],
    "sell_if_any": {"exit_watcher": ["exit"], "regime": ["breaking"],
                    "catalyst_status": ["invalidated", "dormant"],
                    "rank_out_of_top": 10, "rank_out_consecutive": 2},
    "reduce_if_any": {"exit_watcher": ["reduce"], "reduce_fraction": 0.5},
    "hold_zone": {"enabled": True},
    "best_moves": {"enabled": True, "equilibrium_erp": 0.05, "market_proxy": "SPY",
                   "cov_weeks": 56, "min_weeks": 40,
                   "extra_trade_fee_eur": 1.0, "risk_price_k": [1, 1.5, 2, 3, 4, 6, 10]},
    "deployment": {"base": 0.70, "step_per_intact_sector": 0.05, "intact_min": 5,
                   "intact_rank_max": 8, "vix_ramp_start": 25.0, "vix_ramp_full": 35.0,
                   "vix_pause_above": 30.0, "vix_penalty": 0.20,
                   "floor": 0.40, "ceiling": 1.00,
                   # Persistence rule: a shortfall this large surviving this many recorded
                   # reviews must be executed or overridden in writing (plan v4 §4 C1).
                   "max_shortfall_pp": 10.0, "max_shortfall_runs": 2},
    "net_edge_gate": {"applies_to_taxable_sales": True, "applies_to_purchases": False,
                      "shrinkage_prior_windows": 6.0, "min_windows_to_gate": 3},
    "scorecard": {"horizon_days": 63, "min_n": 5, "min_effective_windows": 2},
    "overrides": {"authors_allowed": ["user", "claude", "unrecorded", "superseded", "budget"],
                  "reason_required": True,
                  "defer_is_an_override": True, "score_after_trading_days": 21,
                  "claude_suspended_if": {"min_scored": 5, "net_edge_eur_below": 0.0}},
}


def rebalance_rules() -> dict:
    """The rebalance decision table (rebalance.py): deadband, ticket size, the ADD/BUY/TRIM/
    REDUCE/SELL conditions, the deployment ratio, and the after-tax gate. It exists to take the
    position verdict OUT of LLM judgment — the model drifts to "hold / watch / consider", which
    is how a book ends up 70% in cash while its own ranking disagrees. Five actions, fixed
    precedence, every deviation logged as an override. See scoring_weights.yaml
    `rebalance_rules` + docs/PLAN_v3_lean_pipeline_rebalance.md §4.

    `buy_if`/`add_if` `rank_max` are DERIVED from `book_shape.n_target` (2026-09-02), the same
    way `max_position_pct` and the conviction tiers already are — edit `n_target`, never these.

    WHY THEY COULD NOT STAY FREE CONSTANTS. `decide_action` is handed `rank_in_portfolio`, which
    runs 1..n_target, so a ceiling BELOW n_target makes the last slot of the model book
    structurally unbuyable: a name can only occupy it by already being held. Live on
    run_20260902_064125 that was not theoretical — the selection band put
    `grid_infrastructure_utilities` in the book and the table answered HOLD on a €1,477 gap
    ("no rule condition met"), because rank 6 met a ceiling of 5. A book with six slots and a
    rank-5 buy gate is jointly infeasible in exactly the way v6 L1 found the old
    ceiling/cap/deployment triple to be, and the resolution is the same: one declared knob,
    everything else derived, and a feasibility test that fails when the two stop agreeing.

    AND THE OBJECTION AGAINST IT DOES NOT HOLD. CLAUDE.md warned that raising `buy_if` to 6
    "brings back the contradiction v12 B4b fixed" — that buy would stop being stricter than add.
    It does not: buy and add are ALREADY equal on rank (5 = 5, and the config comment says 5 was
    chosen as "the minimum that removes the contradiction"), and buy stays strictly stricter on
    the three dimensions that actually carry the claim — gap (4.0 vs 3.0), regime (also excludes
    `contested`) and maturity (excludes `exhausted`). Moving BOTH to n_target leaves that
    relation exactly as it was and makes every slot of the book reachable.
    """
    c = dict(_section("rebalance_rules", _REBALANCE_RULES_DEFAULT))
    # `book_shape()` is NOT called here: it reads `deploy_max()`, which reads this function, and
    # the cycle is a RecursionError rather than a wrong number. `n_target` is the raw declared
    # knob and needs none of book_shape's derivation to be read.
    n = max(1, int((_section("book_shape", _BOOK_SHAPE_DEFAULT) or {}).get("n_target", 6)))
    for rule in ("buy_if", "add_if"):
        if isinstance(c.get(rule), dict):
            c[rule] = {**c[rule], "rank_max": n}
    return c


def spread_bps_by_ticker() -> dict[str, float]:
    """`{ticker: spread_bps}` for every universe entry that carries an observed value.

    WHY (plan v5 §4 G1). `rebalance_rules.spread_bps` is one constant for every venue, and the
    comment beside it has said since v3 that a per-ETF value should win when the universe carries
    one. Nothing passed it, so the rebalance table printed an identical `b/e 0.20%` on seven of
    eight action rows — a column that cannot tell a liquid IUHE.AS from a thin JEDI.DE is not
    telling you anything. Absent entries inherit the global, so an unpopulated universe behaves
    exactly as before.
    """
    import yaml

    path = _WEIGHTS_PATH.with_name("etf_universe.yaml")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError):                # pragma: no cover - defensive
        return {}
    sectors = data.get("etf_universe", data)
    out: dict[str, float] = {}
    if not isinstance(sectors, dict):
        return out
    for entry in sectors.values():
        etfs = (entry if isinstance(entry, list)
                else (entry.get("etfs") or []) if isinstance(entry, dict) else [])
        for e in etfs:
            if not isinstance(e, dict):
                continue
            bps, tk = e.get("spread_bps"), e.get("ticker")
            if tk and bps is not None:
                try:
                    out[str(tk)] = float(bps)
                except (TypeError, ValueError):                # pragma: no cover - defensive
                    continue
    return out


# ── Risk-metric reliability (position_metrics, build_site) ───────────────────

_RISK_METRICS_DEFAULT = {
    "min_days_for_sharpe": 120,
    "min_days_for_vol": 30,
}


def risk_metrics() -> dict:
    """Sample-size floors below which vol/Sharpe/beta are shown as indicative, not as findings.
    See scoring_weights.yaml `risk_metrics` for why a two-month Sharpe is not a measurement."""
    return _section("risk_metrics", _RISK_METRICS_DEFAULT)


_REVIEW_TRIGGERS_DEFAULT = {
    "us10y_change_bp_20d": 40,
    "brent_change_pct_20d": 15,
    "equity_drawdown_pct_from_20d_high": 5,
}


def review_triggers() -> dict:
    """Market moves that pull a review forward (review_triggers.py). Measured on 10.8y of history;
    the share of days each fires is in scoring_weights.yaml. Not a score input, not a rule action."""
    return _section("review_triggers", _REVIEW_TRIGGERS_DEFAULT)
