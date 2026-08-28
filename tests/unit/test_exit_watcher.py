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


def test_drawdown_action_folds_into_arbitration():
    # a drawdown 'exit' contribution overrides everything short of a fired full_exit stop
    assert ew.suggest_action(False, False, "intact", False, False, False,
                             drawdown_action="exit") == "exit"
    assert ew.suggest_action(False, False, "intact", False, False, False,
                             drawdown_action="reduce") == "reduce"
    # a warn contribution, or a forced re-verify, is at least a watch
    assert ew.suggest_action(False, False, "intact", False, False, False,
                             drawdown_action="warn") == "watch"
    assert ew.suggest_action(False, False, "intact", False, False, False,
                             reverify_required=True) == "watch"


# ── evaluate_drawdown (two-tier EUR floor) ─────────────────────────────────────

def test_evaluate_drawdown_tiers():
    assert ew.evaluate_drawdown(-11.0, -20.0, -30.0)["tier"] == "clear"
    assert ew.evaluate_drawdown(-21.7, -20.0, -30.0)["tier"] == "reduce"
    assert ew.evaluate_drawdown(-31.0, -20.0, -30.0)["tier"] == "exit"
    assert ew.evaluate_drawdown(-20.0, -20.0, -30.0)["tier"] == "reduce"   # boundary inclusive
    assert ew.evaluate_drawdown(None, -20.0, -30.0)["tier"] == "unknown"


# ── drawdown_overlay_action (freshness dominates) ──────────────────────────────

def test_overlay_fresh_intact_only_warns():
    # a fear selloff on a live thesis: −22% but catalyst fresh + intact → warn, no auto-action
    assert ew.drawdown_overlay_action("reduce", "fresh", False) == ("warn", False)
    assert ew.drawdown_overlay_action("exit", "fresh", False) == ("warn", False)


def test_overlay_fresh_weakening_auto_acts():
    assert ew.drawdown_overlay_action("reduce", "fresh", True) == ("reduce", False)
    assert ew.drawdown_overlay_action("exit", "fresh", True) == ("exit", False)


def test_overlay_stale_verdict_forces_reverify():
    # a real drawdown on a stale verdict can't trust 'intact' → re-verify (protective reduce on exit tier)
    assert ew.drawdown_overlay_action("reduce", "stale", False) == ("warn", True)
    assert ew.drawdown_overlay_action("exit", "very_stale", False) == ("reduce", True)


def test_overlay_no_drawdown_but_very_stale_still_flags():
    assert ew.drawdown_overlay_action("clear", "very_stale", False) == ("warn", True)
    assert ew.drawdown_overlay_action("clear", "stale", False) == ("none", True)
    assert ew.drawdown_overlay_action("clear", "fresh", False) == ("none", False)


# ── catalyst_freshness (age of status_last_reviewed, stalest driver governs) ────

def test_catalyst_freshness_stalest_driver_governs():
    def fake_get(cid):
        return {"struct_a": {"status_last_reviewed": "2026-07-20",
                             "indicators": [{"last_date": "2026-07-01"}]},
                "struct_b": {"status_last_reviewed": "2026-06-01",
                             "indicators": [{"last_date": "2026-06-02"}]}}.get(cid)
    fr = ew.catalyst_freshness(["struct_a", "struct_b"], date(2026, 8, 4), 30, 45, get_fn=fake_get)
    assert fr["last_reviewed"] == "2026-06-01"           # the older of the two
    assert fr["review_age_days"] == 64
    assert fr["status"] == "very_stale"                  # 64 > 45
    assert fr["freshest_indicator_date"] == "2026-07-01"


def test_catalyst_freshness_fresh_and_unknown():
    fresh = ew.catalyst_freshness(["x"], date(2026, 8, 4), 30, 45,
                                  get_fn=lambda c: {"status_last_reviewed": "2026-07-25"})
    assert fresh["status"] == "fresh" and fresh["review_age_days"] == 10
    unknown = ew.catalyst_freshness(["x"], date(2026, 8, 4), 30, 45, get_fn=lambda c: None)
    assert unknown["status"] == "unknown" and unknown["review_age_days"] is None


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


def test_assess_fx_marks_non_eur_vehicle_in_eur(tmp_path):
    """The 2026-08-04 fix: a GBP vehicle must be FX-converted before comparing to its EUR cost
    basis. Native price 10 GBP × 0.5 EUR/GBP × 100 = €500 vs €1000 cost → −50% (NOT the 0% a
    native-vs-EUR mark would show)."""
    import pandas as pd

    mov = {
        "$schema": "catalyx/schemas/movement.json",
        "id": "mov_20260601_gbp_x", "schema_version": "1.1",
        "executed_at": "2026-06-01T00:00:00Z", "action": "open", "sector_id": "test_gbp",
        "vehicle": {"etf": "TESTG.L", "isin": None, "currency": "GBP"},
        "amount_eur": 1000.0, "qty": 100.0, "price": 10.0, "fees": 0.0,
        "attribution": [{"catalyst_id": "struct_test", "weight": 1.0}],
        "trigger": "new_catalyst", "conviction": "medium",
        "risk_discipline": {"invalidation": [], "assumptions": []},
        "metadata": {"created_at": "2026-06-01T00:00:00Z"},
    }
    (tmp_path / "mov_20260601_gbp_x.json").write_text(json.dumps(mov), encoding="utf-8")

    def price_fn(tickers, start, end):
        idx = pd.date_range("2026-05-01", periods=20, freq="D")
        return pd.DataFrame({t: [10.0] * 20 for t in tickers}, index=idx)

    def ccy_fn(tickers):
        return {t: "GBP" for t in tickers}

    def fx_fn(currencies, start, end):
        idx = pd.date_range("2026-05-01", periods=20, freq="D")
        return {c: pd.Series([0.5] * 20, index=idx) for c in currencies}

    r = ew.assess(cfg={"lookback_days": 60, "approach_pct": APPROACH, "drawdown_reduce_pct": -20.0,
                       "drawdown_exit_pct": -30.0, "catalyst_staleness_warn_days": 30,
                       "catalyst_staleness_max_days": 45},
                  price_fn=price_fn, ccy_fn=ccy_fn, fx_fn=fx_fn, today=date(2026, 6, 7),
                  persist=False, movements_dir=tmp_path, lake_dir=tmp_path / "nolake")
    pos = r["positions"][0]
    assert pos["tax"]["unrealized_pct"] == -50.0        # FX applied — not 0.0
    assert pos["drawdown"]["tier"] == "exit"            # −50% ≤ −30% floor
    # struct_test doesn't resolve → freshness unknown → drawdown forces a protective reduce + reverify
    assert pos["catalyst_freshness"]["status"] == "unknown"
    assert pos["drawdown"]["reverify_required"] is True
    assert pos["suggested_action"] == "reduce"


def test_freshness_reads_the_survivor_not_the_merged_catalyst(monkeypatch):
    """A merged catalyst's `status_last_reviewed` was stamped BY THE MERGE — reading it gives a
    fresh-looking date for a thesis nobody re-verified, on a file compute_all() no longer scores.
    """
    from catalyx.scorer import exit_watcher as ew
    from catalyx.store import structural_catalyst_repo as scr

    monkeypatch.setattr(scr, "merged_map", lambda: {"struct_old": "struct_new"})
    catalysts = {
        "struct_old": {"id": "struct_old", "status": "merged",
                       "status_last_reviewed": "2026-08-27"},          # stamped by the merge
        "struct_new": {"id": "struct_new", "status": "active",
                       "status_last_reviewed": "2026-05-01"},          # the real, stale verdict
    }
    out = ew.catalyst_freshness(["struct_old"], date(2026, 8, 28), 30, 45,
                                get_fn=catalysts.get)
    assert out["status"] == "very_stale"                 # not the merge's fresh-looking stamp
    assert out["evaluated_ids"] == ["struct_new"]
    assert out["merged_from"] == ["struct_old"]
