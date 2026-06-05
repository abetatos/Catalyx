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
            "valuation_relative": 50.0, "has_study": 1, "scoring_version": "v"}
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
    assert set(pf.list_profiles()) == {"aggressive", "balanced", "conservative"}
