"""Unit tests for model-portfolio construction (Fase D). Network-free."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from catalyx.execution import portfolio as pf
from catalyx.store import lake


# ── water_fill ───────────────────────────────────────────────────────────────

def test_water_fill_respects_cap_and_sums_to_one():
    w = pf.water_fill([90, 80, 70, 60], max_w=0.40)
    assert all(x <= 0.40 + 1e-9 for x in w)
    assert abs(sum(w) - 1.0) < 1e-9
    assert w[0] >= w[1] >= w[2] >= w[3]  # higher score → higher (or equal) weight


def test_water_fill_all_capped_leaves_cash():
    # 3 positions, 12% cap → max 36% allocated, 64% implicit cash
    w = pf.water_fill([100, 100, 100], max_w=0.12)
    assert all(abs(x - 0.12) < 1e-9 for x in w)
    assert abs(sum(w) - 0.36) < 1e-9


def test_water_fill_empty():
    assert pf.water_fill([], 0.2) == []


# ── build_model_holdings ─────────────────────────────────────────────────────

def _seed_snapshot(tmp_path, rows):
    base = {"run_id": "run_t", "snapshot_at": datetime.now(timezone.utc),
            "catalyst_alignment": 50.0, "flow_confirmation": 50.0,
            "has_study": 1, "scoring_version": "v"}
    df = pd.DataFrame([{**base, **r} for r in rows])
    lake.append_partition("sector_snapshot", df, {"run_id": "run_t"}, lake_dir=tmp_path)


_PROFILE = {
    "portfolio_id": "test", "name": "Test", "risk_profile": "balanced",
    "construction": {"max_positions": 3, "min_composite": 55, "min_momentum": 40,
                     "max_crowding": 80, "exclude_narrative_maturity": ["exhausted"],
                     "weighting": "composite_proportional", "max_position_pct": 50},
}


def test_build_filters_dedupes_and_caps(tmp_path):
    _seed_snapshot(tmp_path, [
        {"sector_id": "a", "rank": 1, "composite": 90, "momentum": 80, "crowding_risk": 50,
         "narrative_maturity": "emerging", "primary_etf": "AAA"},
        {"sector_id": "b", "rank": 2, "composite": 85, "momentum": 70, "crowding_risk": 50,
         "narrative_maturity": "mainstream", "primary_etf": "BBB"},
        # shares ETF AAA with sector a → deduped (a wins, higher composite)
        {"sector_id": "a2", "rank": 3, "composite": 80, "momentum": 70, "crowding_risk": 50,
         "narrative_maturity": "emerging", "primary_etf": "AAA"},
        # excluded: composite below floor
        {"sector_id": "low", "rank": 4, "composite": 50, "momentum": 90, "crowding_risk": 10,
         "narrative_maturity": "emerging", "primary_etf": "LOW"},
        # excluded: exhausted narrative
        {"sector_id": "ex", "rank": 5, "composite": 88, "momentum": 90, "crowding_risk": 10,
         "narrative_maturity": "exhausted", "primary_etf": "EXH"},
        # excluded: momentum below floor
        {"sector_id": "slow", "rank": 6, "composite": 88, "momentum": 20, "crowding_risk": 10,
         "narrative_maturity": "emerging", "primary_etf": "SLW"},
        {"sector_id": "c", "rank": 7, "composite": 70, "momentum": 60, "crowding_risk": 60,
         "narrative_maturity": "mainstream", "primary_etf": "CCC"},
    ])
    res = pf.build_model_holdings("test", profile=_PROFILE, lake_dir=tmp_path)
    etfs = [h["primary_etf"] for h in res["holdings"]]
    assert etfs == ["AAA", "BBB", "CCC"]          # filtered, deduped, ranked, top-3
    assert all(h["weight_pct"] <= 50 for h in res["holdings"])
    assert abs(sum(h["weight_pct"] for h in res["holdings"]) + res["cash_pct"] - 100.0) < 0.05


def test_build_persists_and_show_reads_back(tmp_path):
    _seed_snapshot(tmp_path, [
        {"sector_id": "a", "rank": 1, "composite": 90, "momentum": 80, "crowding_risk": 50,
         "narrative_maturity": "emerging", "primary_etf": "AAA"},
        {"sector_id": "b", "rank": 2, "composite": 80, "momentum": 70, "crowding_risk": 50,
         "narrative_maturity": "mainstream", "primary_etf": "BBB"},
    ])
    pf.build_model_holdings("test", profile=_PROFILE, lake_dir=tmp_path)
    shown = pf.show_holdings("test", lake_dir=tmp_path)
    assert [h["primary_etf"] for h in shown["holdings"]] == ["AAA", "BBB"]


def test_build_errors_when_no_sector_passes(tmp_path):
    _seed_snapshot(tmp_path, [
        {"sector_id": "low", "rank": 1, "composite": 10, "momentum": 5, "crowding_risk": 99,
         "narrative_maturity": "ignored", "primary_etf": "LOW"},
    ])
    res = pf.build_model_holdings("test", profile=_PROFILE, lake_dir=tmp_path)
    assert res["holdings"] == [] and "error" in res


def test_real_profiles_load_and_are_valid():
    import json
    import jsonschema
    schema = json.loads((pf._REPO_ROOT / "schemas" / "portfolio.json").read_text(encoding="utf-8"))
    for pid in pf.list_profiles():
        jsonschema.validate(pf.load_profile(pid), schema)
    assert set(pf.list_profiles()) == {"momentum", "catalyx", "equal_weight", "low_crowding"}


# ── A4: risk-budgeted sizing ─────────────────────────────────────────────────

def test_vol_tilt_is_a_no_op_at_alpha_zero():
    # The default must leave every existing book byte-identical, or "opt in" is not opt in.
    assert pf.vol_tilt([10.0, 20.0, 30.0], [50.0, 10.0, 25.0], 0.0) == [10.0, 20.0, 30.0]


def test_vol_tilt_halves_the_risk_dispersion_at_alpha_half():
    # Equal scores, 4x the vol → sqrt(4) = 2x less weight. Full inverse-vol would give 4x, which
    # would systematically underweight exactly the high-beta sectors the mandate exists to own.
    out = pf.vol_tilt([10.0, 10.0], [64.0, 16.0], 0.5)
    assert out[1] / out[0] == pytest.approx(2.0, abs=1e-6)
    assert sum(out) == pytest.approx(20.0), "the tilt reweights; it must not change the total"


def test_a_missing_vol_takes_the_median_never_zero():
    # An unknown must not divide into an infinite weight, and must not read as risk-free either.
    out = pf.vol_tilt([10.0, 10.0, 10.0], [16.0, None, 64.0], 0.5)
    assert out[0] > out[1] > out[2], "the unknown belongs between the two knowns, not outside them"


def test_the_vol_floor_stops_a_flat_series_from_manufacturing_a_weight():
    loose = pf.vol_tilt([10.0, 10.0], [0.01, 25.0], 0.5, min_vol_pct=5.0)
    assert loose[0] / loose[1] == pytest.approx((25.0 / 5.0) ** 0.5, abs=1e-6)


def test_the_tilt_never_reorders_a_book_of_equal_vol():
    scores = [30.0, 20.0, 10.0]
    out = pf.vol_tilt(scores, [25.0] * 3, 0.5)
    assert out == sorted(out, reverse=True)
    assert [round(x, 6) for x in out] == [round(x, 6) for x in scores]


def test_the_cap_still_binds_after_the_tilt():
    # water_fill runs AFTER the tilt, so `max_position_pct` keeps meaning what it says.
    tilted = pf.vol_tilt([10.0, 10.0], [4.0, 100.0], 0.5)
    w = pf.water_fill(tilted, 0.30)
    assert max(w) <= 0.30 + 1e-9


# ── B1: the conviction tilt is shrunk by measured skill ──────────────────────
#
# The pipeline fused two decisions into one number: how much capital is at work (beta, justified
# by the equity risk premium) and how it is tilted between names (alpha, justified only by the
# ranking's measured IC). λ separates them. What must hold, always:
#   λ=1 → the pre-v4 book, byte-for-byte     λ=0 → same names, same gross, neutral sizing
# and never, at any λ, a change in how much is deployed.

def test_lambda_one_is_the_old_behaviour_exactly():
    model = [4.0, 2.0, 1.0]
    assert pf.skill_shrink(model, [1.0, 1.0, 1.0], 1.0) == model


def test_lambda_zero_sizes_the_book_neutrally():
    out = pf.skill_shrink([4.0, 2.0, 1.0], [1.0, 1.0, 1.0], 0.0)
    assert out[0] == pytest.approx(out[1]) == pytest.approx(out[2])


def test_lambda_never_changes_how_much_is_deployed():
    # The whole point of shrinking the TILT rather than holding cash: both legs carry the same
    # names at the same gross, so `deploy_ratio` is untouched whatever λ says.
    model, neutral = [40.0, 20.0, 10.0, 5.0], [1.0] * 4
    gross = [sum(pf.water_fill(pf.skill_shrink(model, neutral, lam), 0.40))
             for lam in (0.0, 0.25, 0.5, 1.0)]
    assert all(g == pytest.approx(gross[0], abs=1e-9) for g in gross)


def test_shrinkage_is_monotonic_so_it_never_reorders_the_ranking():
    out = pf.skill_shrink([40.0, 20.0, 10.0], [1.0, 1.0, 1.0], 0.4)
    assert out[0] > out[1] > out[2]
    # …and it compresses: the top/bottom ratio must move toward 1, never away from it.
    assert (out[0] / out[2]) < (40.0 / 10.0)


def test_the_regime_haircut_rides_on_the_neutral_leg_and_survives_lambda_zero():
    # A contested sector is a RISK statement, not a conviction one. If the neutral leg were flat,
    # shrinking to λ=0 would quietly undo the overlay and re-risk the name the overlay de-risked.
    out = pf.skill_shrink([4.0, 4.0], [1.0, 0.75], 0.0)
    assert out[1] / out[0] == pytest.approx(0.75, abs=1e-9)


def test_a_degenerate_leg_falls_back_to_the_model_rather_than_to_zero():
    assert pf.skill_shrink([2.0, 1.0], [0.0, 0.0], 0.0) == [2.0, 1.0]
    assert pf.skill_shrink([], [], 0.0) == []
