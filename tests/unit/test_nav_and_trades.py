"""Unit tests for the NAV engine and trade logger (Fase D.2). Network-free."""
from __future__ import annotations

import pandas as pd

from catalyx.execution import nav_engine as nav
from catalyx.execution import trade_logger as tl


# ── NAV math ─────────────────────────────────────────────────────────────────

def _prices(data: dict[str, list[float]], dates: list[str]):
    return pd.DataFrame(data, index=pd.to_datetime(dates))


def test_holdings_nav_buy_and_hold():
    px = _prices({"AAA": [100, 110], "BBB": [100, 90]}, ["2026-06-05", "2026-06-06"])
    # 50/50: day0 = 100; day1 = 0.5*1.10 + 0.5*0.90 = 1.00 → NAV 100
    series = nav.holdings_nav([{"etf": "AAA", "weight_pct": 50}, {"etf": "BBB", "weight_pct": 50}], px)
    assert series[0]["nav"] == 100.0
    assert series[1]["nav"] == 100.0


def test_holdings_nav_gain():
    px = _prices({"AAA": [100, 120]}, ["2026-06-05", "2026-06-06"])
    series = nav.holdings_nav([{"etf": "AAA", "weight_pct": 100}], px)
    assert series[1]["nav"] == 120.0  # +20%


def test_holdings_nav_cash_when_underallocated():
    # 60% in AAA (+50%), 40% cash → day1 = 0.6*1.5 + 0.4 = 1.30 → 130
    px = _prices({"AAA": [100, 150]}, ["2026-06-05", "2026-06-06"])
    series = nav.holdings_nav([{"etf": "AAA", "weight_pct": 60}], px)
    assert series[1]["nav"] == 130.0


def test_holdings_nav_missing_etf_becomes_cash():
    px = _prices({"AAA": [100, 120]}, ["2026-06-05", "2026-06-06"])
    # BBB has no price column → its 50% weight is treated as flat cash
    series = nav.holdings_nav([{"etf": "AAA", "weight_pct": 50}, {"etf": "BBB", "weight_pct": 50}], px)
    assert series[1]["nav"] == 110.0  # 0.5*1.2 + 0.5*1.0


# ── compute_model_nav with injected prices + lake ────────────────────────────

def _seed_model_holding(tmp_path):
    from catalyx.store import lake
    df = pd.DataFrame([
        {"portfolio_id": "test", "run_id": "run_20260605_120000", "config_version": "cfg1",
         "rank_in_portfolio": 1, "sector_id": "a", "primary_etf": "AAA",
         "composite": 90, "momentum": 80, "crowding_risk": 50,
         "narrative_maturity": "emerging", "weight_pct": 100.0},
    ])
    lake.append_partition("portfolio_holding", df,
                          {"portfolio_id": "test", "run_id": "run_20260605_120000"}, lake_dir=tmp_path)


def test_compute_model_nav_persists_and_dates_from_run(tmp_path, monkeypatch):
    _seed_model_holding(tmp_path)
    # profile lookup will fail (no 'test' profile) → benchmark None, which is fine
    px = _prices({"AAA": [100, 110, 121]}, ["2026-06-05", "2026-06-06", "2026-06-07"])
    res = nav.compute_model_nav("test", price_fn=lambda t, s, e: px, lake_dir=tmp_path)
    assert res["start"] == "2026-06-05"          # derived from run_id
    assert res["points"] == 3
    assert res["last_nav"] == 121.0 and res["last_return_pct"] == 21.0
    shown = nav.show_nav("test", lake_dir=tmp_path)
    assert len(shown["series"]) == 3


# ── trade logger ─────────────────────────────────────────────────────────────

def test_log_trade_and_real_holdings(tmp_path):
    tl.log_trade("real", "COPX", "buy", 10, 90.0, date="2026-06-05", fees=1.0,
                 thesis_id="thesis_x", run_id="run_y", lake_dir=tmp_path)
    tl.log_trade("real", "COPX", "buy", 10, 100.0, date="2026-06-06", lake_dir=tmp_path)
    h = tl.real_holdings("real", lake_dir=tmp_path)
    pos = h["holdings"][0]
    assert pos["etf"] == "COPX" and pos["qty"] == 20.0
    assert pos["invested_eur"] == 1901.0           # 901 + 1000
    assert h["holdings"][0]["weight_pct"] == 100.0


def test_sell_realizes_pnl_and_reduces_position(tmp_path):
    tl.log_trade("real", "GDX", "buy", 10, 50.0, date="2026-06-05", lake_dir=tmp_path)   # €500, avg 50
    tl.log_trade("real", "GDX", "sell", 4, 70.0, date="2026-06-10", lake_dir=tmp_path)   # €280, cost 200 → +80
    h = tl.real_holdings("real", lake_dir=tmp_path)
    assert h["realized_eur"] == 80.0
    pos = h["holdings"][0]
    assert pos["qty"] == 6.0 and pos["invested_eur"] == 300.0


def test_trade_lineage_is_recorded(tmp_path):
    t = tl.log_trade("real", "COPX", "buy", 5, 92.0, thesis_id="thesis_copper", run_id="run_z",
                     lake_dir=tmp_path)
    assert t["thesis_id"] == "thesis_copper" and t["run_id"] == "run_z"
    logged = tl.trades("real", lake_dir=tmp_path)
    assert logged[0]["thesis_id"] == "thesis_copper"
