"""Unit tests for the NAV engine and movement repo (positions/ledger). Network-free."""
from __future__ import annotations

import json

import pandas as pd

from catalyx.execution import nav_engine as nav
from catalyx.store import movement_repo as mr


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


# ── movement repo: positions + catalyst ledger ───────────────────────────────

def _write_mov(d, mid, etf, action, qty, amount_eur, sector_id="a",
               attribution=None, fees=0.0, executed_at="2026-06-05T00:00:00Z"):
    doc = {
        "$schema": "catalyx/schemas/movement.json", "id": mid, "schema_version": "1.0",
        "executed_at": executed_at, "action": action, "sector_id": sector_id,
        "vehicle": {"etf": etf, "currency": "EUR"}, "amount_eur": amount_eur,
        "qty": qty, "price": (amount_eur / qty if qty else None), "fees": fees,
        "attribution": attribution or [{"catalyst_id": "struct_x", "weight": 1.0}],
        "trigger": "new_catalyst", "conviction": "medium",
        "metadata": {"created_at": executed_at},
    }
    (d / f"{mid}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_positions_net_from_movement_files(tmp_path):
    _write_mov(tmp_path, "mov_20260605_a_one", "COPX", "open", 10, 901.0, fees=1.0)
    _write_mov(tmp_path, "mov_20260606_a_two", "COPX", "add", 10, 1000.0)
    p = mr.positions(movements_dir=tmp_path)
    pos = p["holdings"][0]
    assert pos["etf"] == "COPX" and pos["qty"] == 20.0
    assert pos["invested_eur"] == 1901.0           # (901+1) + 1000
    assert pos["weight_pct"] == 100.0


def test_close_realizes_pnl_and_reduces_position(tmp_path):
    _write_mov(tmp_path, "mov_20260605_a_open", "GDX", "open", 10, 500.0)   # avg 50
    _write_mov(tmp_path, "mov_20260610_a_close", "GDX", "close", 4, 280.0)  # cost 200 → +80
    p = mr.positions(movements_dir=tmp_path)
    assert p["realized_eur"] == 80.0
    pos = p["holdings"][0]
    assert pos["qty"] == 6.0 and pos["invested_eur"] == 300.0


def test_catalyst_ledger_splits_by_attribution_weight(tmp_path):
    _write_mov(tmp_path, "mov_20260605_a_split", "IQQH", "open", 1, 500.0,
               attribution=[{"catalyst_id": "struct_grid", "weight": 0.7},
                            {"catalyst_id": "struct_ai", "weight": 0.3}])
    led = {e["catalyst_id"]: e for e in mr.catalyst_ledger(movements_dir=tmp_path)}
    assert led["struct_grid"]["invested_eur"] == 350.0
    assert led["struct_ai"]["invested_eur"] == 150.0
