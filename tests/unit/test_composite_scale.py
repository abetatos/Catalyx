"""v6 Fase H — the composite means what its weights say, and says when it doesn't.

The property under test is not a number, it is a guarantee: in a weighted sum of scales that
are not commensurable, the effective weight of a dimension is w·σ_cross. That is how a
constant-50 placeholder sat under a 0.15 weight for months moving nothing (valuation_relative,
v1.6) — the instance was removed, the mechanism was not. These tests fix the mechanism.
"""
from __future__ import annotations

import pytest

from catalyx.config import weights
from catalyx.scorer.sector_scorer import commensurate


def _result(sid, ca, mom, flow, crowd, ca_imputed=False):
    return {
        "sector_id": sid,
        "composite": 0.0,
        "ca_imputed": ca_imputed,
        "score_breakdown": {"catalyst_alignment": ca, "momentum": mom,
                            "flow_confirmation": flow, "crowding_risk": crowd},
    }


def _universe(n=10, **const):
    """n sectors with momentum spread across the range; any dimension in `const` is pinned."""
    out = []
    for i in range(n):
        out.append(_result(f"s{i}", const.get("ca", 50 + i), const.get("mom", 10 + 8 * i),
                           const.get("flow", 40 + 2 * i), const.get("crowd", 55 - i)))
    return out


# ── H1: a dead dimension cannot move the ranking, and cannot do it in silence ──

def test_a_constant_dimension_cannot_change_the_ranking():
    a = _universe(flow=50.0)
    b = _universe(flow=50.0)
    for r in b:  # a different constant — still constant
        r["score_breakdown"]["flow_confirmation"] = 88.0
    commensurate(a)
    commensurate(b)
    assert [r["sector_id"] for r in sorted(a, key=lambda x: -x["composite"])] == \
           [r["sector_id"] for r in sorted(b, key=lambda x: -x["composite"])]
    assert [r["composite"] for r in a] == [r["composite"] for r in b]


def test_a_constant_dimension_is_named_by_the_lint():
    scale = commensurate(_universe(flow=50.0))
    assert "flow_confirmation" in scale["dead_dimensions"]
    assert any("flow_confirmation" in line for line in scale["lint"])


def test_a_live_dimension_is_not_flagged_dead():
    scale = commensurate(_universe())
    assert scale["dead_dimensions"] == []
    assert scale["lint"] == []


def test_the_nominal_weight_becomes_the_effective_weight():
    """The point of H1: after standardization, moving a sector 1σ on a dimension moves the
    composite by exactly w × z_scale — whatever that dimension's raw spread happens to be."""
    z_scale = weights.composite_scale()["z_scale"]
    w = weights.composite_weights()
    for spread in (1.0, 40.0):  # a tight dimension and a wide one must price the same
        rows = [_result(f"s{i}", 50.0, 50.0, 50.0, 50.0) for i in range(21)]
        for i, r in enumerate(rows):  # symmetric ±spread around the mean on momentum only
            r["score_breakdown"]["momentum"] = 50.0 + spread * (i - 10) / 10.0
        commensurate(rows)
        assert abs(rows[10]["composite"] - 50.0) < 0.05
        top = rows[20]
        per_sigma = (top["composite"] - 50.0) / top["dimension_z"]["momentum"]
        assert per_sigma == pytest.approx(w["momentum"] * z_scale, abs=0.05)


def test_z_is_winsorized_so_one_outlier_cannot_own_the_ranking():
    winsor = weights.composite_scale()["winsor_z"]
    rows = [_result(f"s{i}", 50.0, 50.0, 50.0, 50.0) for i in range(30)]
    rows[0]["score_breakdown"]["momentum"] = 1e6
    for i, r in enumerate(rows[1:], 1):
        r["score_breakdown"]["momentum"] = 40.0 + i
    commensurate(rows)
    assert rows[0]["dimension_z"]["momentum"] == pytest.approx(winsor)


def test_crowding_enters_with_the_sign_the_doctrine_requires():
    """Crowding risk is a penalty, not a reward — more crowded must score lower."""
    rows = _universe()
    commensurate(rows)
    crowded = max(rows, key=lambda r: r["score_breakdown"]["crowding_risk"])
    assert crowded["dimension_z"]["crowding_risk"] < 0


def test_a_universe_too_small_to_standardize_says_so_instead_of_inventing_z():
    rows = [_result("a", 90, 90, 90, 10), _result("b", 10, 10, 10, 90)]
    scale = commensurate(rows)
    assert scale["applied"] is False
    assert all(r["composite_z"] is None for r in rows)
    assert all(r["composite_absolute"] == r["composite"] for r in rows)


def test_the_absolute_composite_is_kept_for_read_back():
    rows = _universe()
    for r in rows:
        r["composite"] = 61.5
    commensurate(rows)
    assert all(r["composite_absolute"] == 61.5 for r in rows)
    assert any(r["composite"] != 61.5 for r in rows)


# ── H2: not measured is imputed to the prior, never to the worst case ─────────

def test_an_unmeasured_catalyst_alignment_lands_at_the_prior_not_at_zero():
    rows = _universe(n=12)
    newcomer = _result("newcomer", 0.0, 95.0, 80.0, 30.0, ca_imputed=True)
    rows.append(newcomer)
    commensurate(rows)
    assert newcomer["dimension_z"]["catalyst_alignment"] == 0.0
    # with strong momentum and flow and a neutral CA it must clear the universe average
    assert newcomer["composite_z"] > 0


def test_an_imputed_value_does_not_drag_the_moments_it_is_measured_against():
    """Excluding it from the mean/σ is what makes z=0 mean 'the median of what was measured'."""
    base = _universe(n=12)
    commensurate(base)
    ref = {r["sector_id"]: r["dimension_z"]["catalyst_alignment"] for r in base}

    with_imputed = _universe(n=12) + [_result("newcomer", 0.0, 50.0, 50.0, 50.0, ca_imputed=True)]
    commensurate(with_imputed)
    for r in with_imputed:
        if r["sector_id"] in ref:
            assert r["dimension_z"]["catalyst_alignment"] == pytest.approx(ref[r["sector_id"]])


def test_a_study_that_measured_zero_catalysts_keeps_its_zero():
    """`no_active_catalysts` is a finding, not a gap — only `no_study` is imputed."""
    rows = _universe(n=12)
    measured_zero = _result("barren", 0.0, 50.0, 50.0, 50.0, ca_imputed=False)
    rows.append(measured_zero)
    commensurate(rows)
    assert measured_zero["dimension_z"]["catalyst_alignment"] < -1.0


def test_the_imputation_qualifies_the_row_and_does_not_gate_it():
    """v5 doctrine, still test-enforced: a data flag is a column, never a veto."""
    rows = _universe(n=12)
    newcomer = _result("newcomer", 0.0, 99.0, 99.0, 5.0, ca_imputed=True)
    rows.append(newcomer)
    scale = commensurate(rows)
    ranked = sorted(rows, key=lambda r: -r["composite"])
    assert ranked[0]["sector_id"] == "newcomer"
    assert scale["ca_imputed_n"] == 1


def test_a_new_sector_with_real_signals_can_now_clear_the_flagship_floor():
    """D3: pre-v6 the best a studyless sector could reach was 48.8 against min_composite 55 —
    it could not enter the book however good its momentum and flow were."""
    from catalyx.execution.portfolio import _composite_floor, load_profile

    floor = _composite_floor(load_profile("catalyx")["construction"])
    rows = _universe(n=12)
    newcomer = _result("newcomer", 0.0, 92.0, 85.0, 25.0, ca_imputed=True)
    rows.append(newcomer)
    commensurate(rows)
    assert newcomer["composite"] >= floor


# ── H3: the floor means the same thing in every run ───────────────────────────

def test_the_floor_is_read_from_the_z_key_when_present():
    z_scale = weights.composite_scale()["z_scale"]
    from catalyx.execution.portfolio import _composite_floor

    assert _composite_floor({"min_composite_z": 0.0, "min_composite": 55}) == 50.0
    assert _composite_floor({"min_composite_z": 1.0}) == pytest.approx(50.0 + z_scale)


def test_an_unmigrated_profile_still_reads_its_pre_v6_floor():
    from catalyx.execution.portfolio import _composite_floor

    assert _composite_floor({"min_composite": 55}) == 55.0


def test_every_shipped_profile_declares_the_floor_in_z_units():
    from catalyx.execution.portfolio import list_profiles, load_profile

    for pid in list_profiles():
        c = load_profile(pid)["construction"]
        assert c.get("min_composite_z") is not None, f"{pid} was not migrated to z-units"


def test_the_dislocation_lenses_read_the_same_scale():
    cfg = weights.dislocation()
    opp = weights.composite_floor(cfg, "min_opportunity_composite_z", "min_opportunity_composite")
    div = weights.composite_floor(cfg, "min_diversifier_composite_z", "min_diversifier_composite")
    assert opp > div, "the opportunity lens must stay stricter than the diversifier lens"


# ── H4: the momentum blend does not pay for the reversal window ──────────────

def test_the_momentum_blend_carries_no_one_month_leg():
    """The last month is the short-term REVERSAL window (Jegadeesh 1990, Lehmann 1990); the
    standard 12-1 signal skips it, and this repo's own v1.6 backtest measured negative monthly
    IC on the short leg while the 1m still entered with a positive sign."""
    w = weights.momentum_period_weights()
    assert w["return_1m"] == 0.0
    assert w["return_3m"] + w["return_6m"] == pytest.approx(1.0)
    assert w["return_3m"] / w["return_6m"] == pytest.approx(45 / 35, abs=0.01)
