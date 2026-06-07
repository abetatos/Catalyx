"""Exit watcher — Family 1 of the sell-signal layer. Pure stop evaluation + roll-ups + the severity
arbitration, plus one engine test with an injected price_fn and a tmp movements dir (network-free)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from catalyx.scorer import exit_watcher as ew

APPROACH = 5.0


# ── trailing_breach_count ─────────────────────────────────────────────────────

def test_trailing_breach_count_counts_only_the_recent_run():
    # below 100: ...,105(ok),99,98,97 → last 3 breach
    closes = [110, 108, 105, 99, 98, 97]
    assert ew.trailing_breach_count(closes, "below", 100) == 3


def test_trailing_breach_count_zero_when_last_close_safe():
    closes = [99, 98, 101]  # last close back above → run broken
    assert ew.trailing_breach_count(closes, "below", 100) == 0


def test_trailing_breach_count_above():
    closes = [340000, 351000, 352000]  # above 350000 for last 2
    assert ew.trailing_breach_count(closes, "above", 350000) == 2


# ── evaluate_stop ──────────────────────────────────────────────────────────────

def test_evaluate_stop_fires_when_window_met():
    closes = [120, 110, 99, 98, 97, 96]  # 4 consecutive below 100, need 3
    ev = ew.evaluate_stop(closes, "below", 100, 3, APPROACH)
    assert ev["status"] == "fired"
    assert ev["consecutive_breaching"] == 4
    assert ev["consecutive_days_required"] == 3


def test_evaluate_stop_approaching_when_breaching_but_short_of_window():
    closes = [120, 110, 105, 99, 98]  # 2 below, need 10
    ev = ew.evaluate_stop(closes, "below", 100, 10, APPROACH)
    assert ev["status"] == "approaching"
    assert ev["consecutive_breaching"] == 2


def test_evaluate_stop_approaching_when_hugging_the_line_from_safe_side():
    closes = [120, 110, 102]  # not breaching, but within 5% of 100
    ev = ew.evaluate_stop(closes, "below", 100, 10, APPROACH)
    assert ev["status"] == "approaching"
    assert ev["consecutive_breaching"] == 0


def test_evaluate_stop_clear_when_comfortably_safe():
    closes = [120, 118, 115]  # 15% above a below-stop at 100
    ev = ew.evaluate_stop(closes, "below", 100, 10, APPROACH)
    assert ev["status"] == "clear"


def test_evaluate_stop_unknown_without_fields():
    assert ew.evaluate_stop([], "below", 100, 10, APPROACH)["status"] == "unknown"
    assert ew.evaluate_stop([100], None, 100, 10, APPROACH)["status"] == "unknown"
    assert ew.evaluate_stop([100], "below", None, 10, APPROACH)["status"] == "unknown"


# ── roll_up_assumptions ────────────────────────────────────────────────────────

def test_roll_up_assumptions_counts_and_flags():
    asm = [
        {"id": "asm_01", "current_status": "holding"},
        {"id": "asm_02", "current_status": "weakening"},
        {"id": "asm_03", "current_status": "violated"},
        {"id": "asm_04"},  # missing → unverified
    ]
    r = ew.roll_up_assumptions(asm)
    assert r["total"] == 4
    assert r["holding"] == 1 and r["weakening"] == 1 and r["violated"] == 1 and r["unverified"] == 1
    assert r["violated_ids"] == ["asm_03"]
    assert r["weakening_ids"] == ["asm_02"]


# ── suggest_action (severity arbitration §5) ───────────────────────────────────

def test_full_exit_stop_overrides_everything():
    assert ew.suggest_action(True, False, "intact", False, False, False) == "exit"
    # even if other things would say less, full_exit wins
    assert ew.suggest_action(True, True, "breaking", True, True, True) == "exit"


def test_reduce_on_breaking_or_violated_or_fired_reduce():
    assert ew.suggest_action(False, True, "intact", False, False, False) == "reduce"
    assert ew.suggest_action(False, False, "breaking", False, False, False) == "reduce"
    assert ew.suggest_action(False, False, "intact", True, False, False) == "reduce"


def test_watch_on_contested_or_weakening_or_approaching():
    assert ew.suggest_action(False, False, "contested", False, False, False) == "watch"
    assert ew.suggest_action(False, False, "intact", False, True, False) == "watch"
    assert ew.suggest_action(False, False, "intact", False, False, True) == "watch"


def test_hold_when_nothing_fires():
    assert ew.suggest_action(False, False, "intact", False, False, False) == "hold"


# ── engine (injected price_fn, tmp movements dir, no lake) ─────────────────────

def _fake_prices(tickers, start, end):
    import pandas as pd
    idx = pd.date_range("2026-04-01", periods=20, freq="D")
    data = {}
    for t in tickers:
        if t == "EURUSD=X":
            data[t] = [1.15] * 20          # ~13% above the 1.02 below-stop → comfortably clear
        elif t == "TESTV.DE":
            data[t] = [10.0] * 19 + [12.0]  # vehicle mark → +20% vs €10 cost
        else:
            data[t] = [100.0] * 20
    return pd.DataFrame(data, index=idx)


def _write_movement(d: Path):
    mov = {
        "$schema": "catalyx/schemas/movement.json",
        "id": "mov_20260601_test_sector_x", "schema_version": "1.1",
        "executed_at": "2026-06-01T00:00:00Z", "action": "open", "sector_id": "test_sector",
        "vehicle": {"etf": "TESTV.DE", "isin": None, "currency": "EUR"},
        "amount_eur": 1000.0, "qty": 100.0, "price": 10.0, "fees": 0.0,
        "attribution": [{"catalyst_id": "struct_test", "weight": 1.0}],
        "trigger": "new_catalyst", "conviction": "medium",
        "risk_discipline": {
            "invalidation": [
                {"id": "inv_01", "condition": "EURUSD below 1.02 for 10d", "severity": "review_and_reduce",
                 "source": "market_data", "comparator": "below", "threshold": 1.02,
                 "consecutive_days": 10, "eval_ticker": "EURUSD=X"},
                {"id": "inv_02", "condition": "LME inventory above 350kt", "severity": "review_and_reduce",
                 "source": "market_data", "comparator": "above", "threshold": 350000,
                 "consecutive_days": None, "eval_ticker": None},
            ],
            "assumptions": [{"id": "asm_01", "statement": "x", "monitoring_source": "market_data",
                             "check_frequency": "monthly", "current_status": "holding"}],
        },
        "metadata": {"created_at": "2026-06-01T00:00:00Z"},
    }
    (d / "mov_20260601_test_sector_x.json").write_text(json.dumps(mov), encoding="utf-8")


def test_assess_end_to_end(tmp_path):
    _write_movement(tmp_path)
    r = ew.assess(cfg={"lookback_days": 60, "approach_pct": APPROACH}, price_fn=_fake_prices,
                  today=date(2026, 6, 7), persist=False, movements_dir=tmp_path,
                  lake_dir=tmp_path / "nolake")
    assert r["n_positions"] == 1
    pos = r["positions"][0]
    # EURUSD stop clear, no breaking regime, holding assumption → hold
    assert pos["suggested_action"] == "hold"
    # the machine-checkable stop was evaluated; the null-eval one routed to Claude-check
    assert len(pos["stops_checked"]) == 1 and pos["stops_checked"][0]["status"] == "clear"
    assert len(pos["stops_claude_check"]) == 1 and pos["stops_claude_check"][0]["id"] == "inv_02"
    # mark-to-market: 100 units × €12 = €1200 vs €1000 cost → +€200 gain, CGT applied
    assert pos["tax"]["unrealized_eur"] == 200.0
    assert pos["tax"]["tax_due_eur"] == 38.0          # 19% of €200 (first bracket)
    assert pos["tax"]["net_proceeds_eur"] == 1162.0
