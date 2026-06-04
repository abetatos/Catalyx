"""Unit tests for the continuous intensity engine (catalyx.scorer.intensity_engine, v1.5).

Covers the two scoring paths (linear fallback + empirical percentile), the
lower_is_stronger inversion, the cliff fix that motivated the redesign, additive
trend deltas, color derivation, and the final [10, 95] clamp.

Run: uv run pytest tests/unit/test_intensity_engine.py -q
"""
from __future__ import annotations

import pytest

from catalyx.scorer import intensity_engine as ie


def _ind(**kw):
    """Build an indicator dict with sensible defaults."""
    base = {
        "id": "ind_01",
        "direction": "higher_is_stronger",
        "threshold_strong": 200,
        "threshold_weak": 80,
        "current_value": 150,
        "value_history": [],
        "unit": "x",
    }
    base.update(kw)
    return base


# ── Saturating threshold fallback (cold start, < min_history_points) ─────────

def test_fallback_midpoint_between_thresholds():
    # weak=80→50, strong=200→80. Midpoint 140 (x=0.5) → 50 + 0.5×30 = 65.
    score = ie._indicator_score(_ind(current_value=140, threshold_strong=200, threshold_weak=80))
    assert score == pytest.approx(65.0, abs=0.05)


def test_fallback_at_strong_threshold_is_80():
    # Being AT threshold_strong scores the strong anchor (80), not 100 — headroom above.
    score = ie._indicator_score(_ind(current_value=200, threshold_strong=200, threshold_weak=80))
    assert score == pytest.approx(80.0, abs=0.05)


def test_fallback_at_weak_threshold_is_50():
    score = ie._indicator_score(_ind(current_value=80, threshold_strong=200, threshold_weak=80))
    assert score == pytest.approx(50.0, abs=0.05)


def test_fallback_far_above_strong_saturates_below_100():
    # x=3.5 → 80 + 20×(1 - exp(-0.693×2.5)) ≈ 96.5. Graded, not clamped at 100.
    score = ie._indicator_score(_ind(current_value=500, threshold_strong=200, threshold_weak=80))
    assert score == pytest.approx(96.5, abs=0.1)
    assert score < 100.0


def test_cliff_fix_cofer_case():
    """The motivating bug: COFER ind_02, lower_is_stronger, strong=0.58, weak=0.62,
    value=0.582 scored 🟡=65 under the old semaphore (a cliff). The saturating fallback
    gives a continuous 78.5 — honestly 'right at the strong threshold', no discontinuity."""
    ind = _ind(
        direction="lower_is_stronger",
        threshold_strong=0.58,
        threshold_weak=0.62,
        current_value=0.582,
    )
    score = ie._indicator_score(ind)
    assert score == pytest.approx(78.5, abs=0.05)
    assert ie._color(score) == "🟡"


def test_lower_is_stronger_below_strong_scores_above_80():
    # value below the strong (low) threshold → above strong on the band → >80, graded.
    ind = _ind(direction="lower_is_stronger", threshold_strong=0.58, threshold_weak=0.62, current_value=0.55)
    score = ie._indicator_score(ind)
    assert score == pytest.approx(88.1, abs=0.2)
    assert 80.0 < score < 100.0


def test_none_value_floors():
    assert ie._indicator_score(_ind(current_value=None)) == ie._IND_CLAMP_LO


# ── Empirical percentile (>= min_history_points) ─────────────────────────────

def test_percentile_path_activates_with_enough_history():
    # 6 points incl. current → percentile path. current_value is the max.
    hist = [{"value": v} for v in [10, 20, 30, 40, 50]]
    ind = _ind(current_value=60, value_history=hist, threshold_strong=999, threshold_weak=0)
    # mean rank of the unique max among n=6: (5 + 0.5*1)/6 = 91.67
    assert ie._scoring_mode(ind) == "percentile"
    assert ie._indicator_score(ind) == pytest.approx(91.7, abs=0.1)


def test_percentile_inverts_for_lower_is_stronger():
    hist = [{"value": v} for v in [10, 20, 30, 40, 50]]
    # current=60 is the max; for lower_is_stronger that is the WEAKEST → low score.
    ind = _ind(direction="lower_is_stronger", current_value=60, value_history=hist,
               threshold_strong=0, threshold_weak=999)
    assert ie._indicator_score(ind) == pytest.approx(100.0 - 91.7, abs=0.1)


def test_percentile_median_is_50ish():
    hist = [{"value": v} for v in [10, 20, 40, 50, 60]]
    ind = _ind(current_value=30, value_history=hist)  # 30 is the middle of 6 values
    # (2 below + 0.5*1)/6 = 41.7
    assert ie._indicator_score(ind) == pytest.approx(41.7, abs=0.1)


# ── Trend deltas (additive) ──────────────────────────────────────────────────

def test_trend_delta_rising_two_consecutive():
    history = [{"score": 90}, {"score": 84}, {"score": 78}]  # most-recent-first
    delta, label = ie._trend_delta(history)
    assert delta == 5
    assert "↑↑" in label


def test_trend_delta_falling_one():
    history = [{"score": 80}, {"score": 90}]
    delta, _ = ie._trend_delta(history)
    assert delta == -3


def test_trend_delta_flat_insufficient_history():
    delta, _ = ie._trend_delta([{"score": 90}])
    assert delta == 0


# ── Color derivation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("score,expected", [(95, "🟢"), (80, "🟢"), (79.9, "🟡"), (50, "🟡"), (49.9, "🔴")])
def test_color_thresholds(score, expected):
    assert ie._color(score) == expected


# ── compute_intensity: end-to-end with clamp ─────────────────────────────────

def test_compute_intensity_clamps_to_max():
    cat = {
        "id": "struct_test",
        "indicators": [_ind(current_value=500), _ind(id="ind_02", current_value=500)],
        "intensity": {"current_score": 80, "history": [{"score": 90}, {"score": 84}, {"score": 78}]},
    }
    r = ie.compute_intensity(cat)
    # each indicator (val=500, far above strong) → ~96.5; avg ~96.5, +5 trend → clamped to 95
    assert r["indicator_avg"] == pytest.approx(96.5, abs=0.1)
    assert r["trend_delta"] == 5
    assert r["computed_score"] == 95.0


def test_compute_intensity_no_indicators_errors():
    r = ie.compute_intensity({"id": "x", "indicators": []})
    assert "error" in r
