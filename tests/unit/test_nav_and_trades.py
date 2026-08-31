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


# ── Real-book ledger: TWR / MWR (2026-08-28) ─────────────────────────────────
#
# The defect these pin: the real curve used to project TODAY's holdings backwards over the whole
# window. A book built in tranches then showed exposure it never had, and every risk metric
# downstream described a portfolio that was never held.

def _mov(mid, on, etf, action, qty, eur, price=None):
    return {"id": mid, "executed_at": f"{on}T10:00:00Z", "action": action, "qty": qty,
            "amount_eur": eur, "price": price, "vehicle": {"etf": etf, "currency": "EUR"}}


def test_daily_ledger_steps_qty_in_on_the_execution_date():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03"])
    movs = [_mov("m1", "2026-06-02", "AAA", "open", 10.0, 1000.0)]
    qty, flow = nav.daily_ledger(movs, idx)
    assert list(qty["AAA"]) == [0.0, 10.0, 10.0]     # NOT held on day 1
    assert list(flow) == [0.0, 1000.0, 0.0]


def test_daily_ledger_sell_reduces_qty_and_flows_out():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    movs = [_mov("m1", "2026-06-01", "AAA", "open", 10.0, 1000.0),
            _mov("m2", "2026-06-02", "AAA", "trim", 4.0, 420.0)]
    qty, flow = nav.daily_ledger(movs, idx)
    assert list(qty["AAA"]) == [10.0, 6.0]
    assert list(flow) == [1000.0, -420.0]


def test_daily_ledger_weekend_trade_lands_on_next_session():
    idx = pd.to_datetime(["2026-06-05", "2026-06-08"])          # Fri, Mon
    qty, flow = nav.daily_ledger([_mov("m1", "2026-06-06", "AAA", "open", 5.0, 500.0)], idx)
    assert list(qty["AAA"]) == [0.0, 5.0]
    assert list(flow) == [0.0, 500.0]


def test_twr_neutralizes_a_contribution():
    """A €1000 top-up into a flat book is not a +100% day."""
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    value = pd.Series([1000.0, 2000.0], index=idx)
    flow = pd.Series([1000.0, 1000.0], index=idx)
    out = nav.twr_series(value, flow)
    assert out[-1]["nav"] == 100.0                              # prices never moved


def test_twr_scores_the_first_day_against_the_price_paid():
    """Start-of-day convention: money invested today is marked at today's close."""
    idx = pd.to_datetime(["2026-06-01"])
    out = nav.twr_series(pd.Series([940.0], index=idx), pd.Series([1000.0], index=idx))
    assert out[0]["nav"] == 94.0                                # −6% really happened


def test_twr_compounds_across_a_contribution():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03"])
    # d1: 1000 in, closes 1100 (+10%). d2: +1000 in, book worth 2100 (flat). d3: 2310 (+10%).
    value = pd.Series([1100.0, 2100.0, 2310.0], index=idx)
    flow = pd.Series([1000.0, 1000.0, 0.0], index=idx)
    out = nav.twr_series(value, flow)
    assert [p["nav"] for p in out] == [110.0, 110.0, 121.0]


def test_twr_ignores_days_before_the_book_exists():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    out = nav.twr_series(pd.Series([0.0, 950.0], index=idx), pd.Series([0.0, 1000.0], index=idx))
    assert out[0]["nav"] == 100.0 and out[1]["nav"] == 95.0


def test_xirr_recovers_a_known_rate():
    import datetime as dt
    flows = [(dt.date(2026, 1, 1), -1000.0), (dt.date(2027, 1, 1), 1100.0)]
    assert abs(nav.xirr(flows) - 0.10) < 1e-3


def test_xirr_none_when_flows_do_not_bracket():
    import datetime as dt
    assert nav.xirr([(dt.date(2026, 1, 1), -100.0), (dt.date(2027, 1, 1), -100.0)]) is None


def test_mwr_beats_twr_when_money_arrives_before_the_gain():
    """The whole reason both numbers exist: timing of contributions changes one, not the other."""
    import datetime as dt
    # €1000 flat for a year, then €1000 more right before a +10% move on the whole book.
    flows = [(dt.date(2026, 1, 1), -1000.0), (dt.date(2026, 12, 1), -1000.0),
             (dt.date(2027, 1, 1), 2200.0)]
    assert nav.xirr(flows) > 0.10


def test_execution_price_check_flags_a_fill_logged_at_the_prior_close():
    px = _prices({"AAA": [60.57, 56.41]}, ["2026-06-04", "2026-06-05"])
    movs = [_mov("m1", "2026-06-05", "AAA", "open", 16.5, 1000.0, price=60.62)]
    w = nav.execution_price_checks(movs, px)
    assert len(w) == 1 and w[0]["matches_prior_close"] is True
    assert w[0]["drift_pct"] > 7


def test_execution_price_check_silent_when_the_fill_matches_the_close():
    px = _prices({"AAA": [60.57, 56.41]}, ["2026-06-04", "2026-06-05"])
    movs = [_mov("m1", "2026-06-05", "AAA", "open", 17.7, 1000.0, price=56.40)]
    assert nav.execution_price_checks(movs, px) == []


def test_daily_ledger_caps_an_oversized_sale_instead_of_going_short():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    movs = [_mov("m1", "2026-06-01", "AAA", "open", 10.0, 1000.0),
            _mov("m2", "2026-06-02", "AAA", "close", 25.0, 2500.0)]     # hand-authored typo
    qty, _ = nav.daily_ledger(movs, idx)
    assert list(qty["AAA"]) == [10.0, 0.0]                              # never negative


def test_twr_holds_flat_while_the_book_is_empty_then_resumes():
    """Full exit → nothing held → re-entry. The empty stretch must not invent a return, and the
    re-entry must be scored against the price paid, not against the pre-exit NAV."""
    idx = pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"])
    value = pd.Series([1100.0, 0.0, 0.0, 950.0], index=idx)
    flow = pd.Series([1000.0, -1100.0, 0.0, 1000.0], index=idx)
    out = [p["nav"] for p in nav.twr_series(value, flow)]
    assert out == [110.0, 110.0, 110.0, 104.5]                          # 110 × 0.95


def test_twr_partial_sale_is_not_a_loss():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    value = pd.Series([1000.0, 500.0], index=idx)
    flow = pd.Series([1000.0, -500.0], index=idx)                       # sold half at the mark
    assert [p["nav"] for p in nav.twr_series(value, flow)] == [100.0, 100.0]
