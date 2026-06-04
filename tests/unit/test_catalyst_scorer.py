"""Unit tests for the additive event↔structural interaction (catalyx.scorer.catalyst_scorer, v1.5).

Covers the confirms boost (with floor/cap), the contradicts penalty (with floor/cap),
the unchanged independent blend, decay monotonicity, and the noisy-OR aggregation.

Run: uv run pytest tests/unit/test_catalyst_scorer.py -q
"""
from __future__ import annotations

import pytest

from catalyx.scorer import catalyst_scorer as cs


# ── Confirms (additive boost, floored at structural, capped at independent) ──

def test_confirms_floored_at_structural_when_blend_is_lower():
    # structural=95, decayed=91: boost=10*0.91=9.1 → raw=104.1, but
    # case_c = 95*0.45 + 91*0.55 = 92.8 → result floored at max(95, min(104.1, 92.8)) = 95.
    assert cs._apply_confirms(95, 91) == 95.0


def test_confirms_adds_points_when_headroom_exists():
    # structural=60, decayed=80: boost=8 → raw=68;
    # case_c = 60*0.45 + 80*0.55 = 71 → result = max(60, min(68, 71)) = 68.
    assert cs._apply_confirms(60, 80) == 68.0


def test_confirms_never_below_structural_for_weak_event():
    # structural=88, decayed=10: boost=1 → raw=89; case_c=88*0.45+10*0.55=45.1
    # result = max(88, min(89, 45.1)) = 88  (baseline preserved).
    assert cs._apply_confirms(88, 10) == 88.0


def test_confirms_capped_at_independent_blend():
    result = cs._apply_confirms(60, 100)
    case_c = 60 * cs._STRUCTURAL_SUB_WEIGHT + 100 * cs._EVENT_SUB_WEIGHT
    assert result <= case_c + 1e-9


# ── Contradicts (additive penalty, capped at structural, floored at 0) ───────

def test_contradicts_subtracts_scaled_points():
    # structural=90, decayed=100: penalty = contradict_max_points → 90 - 15 = 75.
    expected = 90 - cs._CONTRADICT_MAX_POINTS
    assert cs._apply_contradicts(90, 100) == pytest.approx(expected)


def test_contradicts_weak_event_barely_moves():
    # decayed=10 → penalty = 1.5 → 90 - 1.5 = 88.5.
    assert cs._apply_contradicts(90, 10) == pytest.approx(90 - cs._CONTRADICT_MAX_POINTS * 0.10)


def test_contradicts_never_above_structural():
    assert cs._apply_contradicts(90, 0) == 90.0


def test_contradicts_floored_at_zero():
    assert cs._apply_contradicts(5, 100) == 0.0


# ── Independent blend (unchanged) ────────────────────────────────────────────

def test_independent_is_weighted_blend():
    expected = 80 * cs._STRUCTURAL_SUB_WEIGHT + 50 * cs._EVENT_SUB_WEIGHT
    assert cs._apply_independent(80, 50) == pytest.approx(round(expected, 2))


# ── Decay ────────────────────────────────────────────────────────────────────

def test_decay_zero_days_is_full_strength():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    assert cs._decayed_strength(80, 46, now) == pytest.approx(80, abs=0.5)


def test_decay_one_halflife_halves():
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(days=46)).isoformat()
    assert cs._decayed_strength(80, 46, past) == pytest.approx(40, abs=0.5)


# ── Noisy-OR aggregation (monotonic, bounded) ────────────────────────────────

def test_aggregate_single_score_is_itself():
    assert cs._aggregate_alignment([88]) == 88.0


def test_aggregate_adding_catalyst_never_lowers():
    one = cs._aggregate_alignment([90])
    two = cs._aggregate_alignment([90, 70])
    assert two >= one


def test_aggregate_bounded_by_100():
    assert cs._aggregate_alignment([95, 95, 95, 95]) <= 100.0
