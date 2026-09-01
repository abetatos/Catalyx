"""Unit tests for the additive event↔structural interaction (catalyx.scorer.catalyst_scorer, v1.5).

Covers the confirms boost (with floor/cap), the contradicts penalty (with floor/cap),
the unchanged independent blend, decay monotonicity, and the noisy-OR aggregation.

Run: uv run pytest tests/unit/test_catalyst_scorer.py -q
"""
from __future__ import annotations

import pytest

from catalyx.scorer import catalyst_scorer as cs


# ── Confirms (additive boost, floored at structural, capped at 100) ──────────

def test_confirms_adds_points_when_headroom_exists():
    # structural=60, decayed=80: boost = 10 × 0.80 = 8 → 68.
    assert cs._apply_confirms(60, 80) == 68.0


def test_confirms_never_below_structural():
    # A confirm is evidence FOR the thesis: the no-event case is its floor.
    assert cs._apply_confirms(88, 0) == 88.0


def test_confirms_capped_at_one_hundred():
    assert cs._apply_confirms(98, 100) == 100.0


def test_confirms_boost_survives_when_the_event_is_weaker_than_the_structural():
    """v12 B1 — THE regression. The old cap was the independent blend, which sits below its own
    floor whenever `decayed <= structural`, so `max(S, min(S+boost, 0.45S+0.55D))` collapsed to
    `S` and the boost vanished. Live that was every single one of the 32 linked `confirms` in
    the registry (structurals 77–95, decayed strengths 14–45): the event layer could only ever
    subtract. This asserts the boost is actually delivered in that regime."""
    for structural, decayed in ((95, 91), (88, 10), (95, 45), (77.7, 24.57)):
        blend = structural * cs._STRUCTURAL_SUB_WEIGHT + decayed * cs._EVENT_SUB_WEIGHT
        assert blend < structural                      # the old cap was below the floor
        expected = min(structural + cs._CONFIRM_MAX_POINTS * decayed / 100.0, 100.0)
        assert cs._apply_confirms(structural, decayed) == pytest.approx(round(expected, 2))


def test_confirms_is_monotone_in_decayed_strength():
    prev = -1.0
    for d in range(0, 101, 5):
        cur = cs._apply_confirms(70, d)
        assert cur >= prev
        prev = cur


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


# ── Independent blend, floored at the no-event case (v12 B2) ─────────────────

def test_independent_is_weighted_blend_when_the_event_dominates():
    expected = 80 * cs._STRUCTURAL_SUB_WEIGHT + 100 * cs._EVENT_SUB_WEIGHT
    assert cs._apply_independent(80, 100) == pytest.approx(round(expected, 2))


def test_independent_never_lowers_the_structural():
    """v12 B2 — the bare blend had no floor, so a DECAYING independent event ate the structural
    it never contradicted: `consumer_india_em` read 64.5 → 61.81, and the D→0 limit was
    `0.45 × 64.5` = 29.0. The correct D→0 limit is the no-event case."""
    assert cs._apply_independent(64.5, 59.6) == 64.5
    assert cs._apply_independent(64.5, 0) == 64.5
    for d in range(0, 101, 5):
        assert cs._apply_independent(80, d) >= 80.0


def test_only_contradicts_may_lower_a_structural():
    for d in range(0, 101, 10):
        assert cs._apply_interaction(80, d, "confirms") >= 80.0
        assert cs._apply_interaction(80, d, "independent") >= 80.0
        assert cs._apply_interaction(80, d, "contradicts") <= 80.0


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


# ── Netting the two event directions on one structural (v12 B2b) ─────────────

def test_net_event_impact_is_the_old_rule_when_one_direction():
    """With events pulling one way only, netting reduces to "strongest event wins" — so this
    change cannot move any structural that does not carry BOTH directions."""
    assert cs._net_event_impact(90.0, [92.0, 94.5, 90.0]) == 94.5
    assert cs._net_event_impact(90.0, [88.0, 85.0]) == 85.0
    assert cs._net_event_impact(90.0, []) == 90.0


def test_a_confirm_cannot_silence_a_live_contradict():
    """THE defect. Under `max(..., key=|impact|)` a +4.5 confirm outvoted a −3.0 contradict and
    the disconfirming evidence left the score entirely. Both are applied now."""
    assert cs._net_event_impact(90.0, [94.5, 87.0]) == 91.5


def test_net_event_impact_is_clamped_to_the_score_range():
    assert cs._net_event_impact(98.0, [100.0, 99.5]) == 100.0
    assert cs._net_event_impact(4.0, [0.0]) == 0.0


# ── Direction of a DIRECT event on the sector it is listed against (v12 B5) ──

def test_a_direct_events_direction_defaults_to_supportive_and_says_so():
    """Absence is reported, never guessed. The pre-1.4 behaviour is preserved exactly, so
    adding the field moved no live score — but `sector_direction_stated: False` is what lets
    the audit count how much `catalyst_alignment` rests on an assumption nobody made."""
    d, stated = cs._sector_direction({}, "gold_miners", "independent")
    assert (d, stated) == ("supportive", False)


def test_a_stated_direction_wins():
    ev = {"sector_impact": {"gold_miners": "adverse", "eu_defense": "supportive"}}
    assert cs._sector_direction(ev, "gold_miners", "independent") == ("adverse", True)
    assert cs._sector_direction(ev, "eu_defense", "independent") == ("supportive", True)


def test_a_direct_contradicts_is_adverse_not_discarded():
    """It used to be dropped outright (`contributes = relation in ("independent","confirms")`),
    which made the direct-event channel monotone INCREASING — it could only ever raise CA, in
    a registry already holding 31 confirms against 2 contradicts."""
    assert cs._sector_direction({}, "s", "contradicts") == ("adverse", True)
