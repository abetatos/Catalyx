"""Unit tests for position & book metrics (catalyx.execution.position_metrics).

This module only MEASURES, so the tests pin the two things a measurement can get wrong without
anyone noticing: an identity that does not hold (the price/FX/residual split must sum to the actual
EUR P&L, or one of the three terms is quietly absorbing an error), and a statistic that prints a
confident number from an insufficient or degenerate sample instead of admitting it does not know.

The NAV-mode test pins a real bug: `portfolio_nav` stores backtest/live/forward rows under one
portfolio_id, and reading them all sorted by date splices two curves into one, manufacturing ±18%
daily moves. The first live run reported a 95% tracking error that way.
"""
from __future__ import annotations

import pandas as pd
import pytest

from catalyx.execution import position_metrics as pm


# ── The P&L split: an identity, not an estimate ──────────────────────────────

def test_the_split_sums_exactly_to_the_eur_pnl():
    # 100 units bought at 10 USD when EUR/USD gave 0.90 EUR per USD → €900 basis.
    # Now 12 USD at 0.95 → €1140. P&L €240, of which price and FX are both positive.
    out = pm.pnl_split(100, 10.0, 12.0, 0.90, 0.95, 240.0)
    assert out["pnl_price_eur"] == pytest.approx(180.0)     # 100 × 2 × 0.90
    assert out["pnl_fx_eur"] == pytest.approx(60.0)         # 100 × 12 × 0.05
    assert out["basis_residual_eur"] == pytest.approx(0.0)
    assert (out["pnl_price_eur"] + out["pnl_fx_eur"]
            + out["basis_residual_eur"]) == pytest.approx(240.0)


def test_a_currency_loss_can_hide_inside_a_price_gain():
    # The whole reason this split exists: the position is UP on the thesis and DOWN in euros.
    out = pm.pnl_split(100, 10.0, 11.0, 1.00, 0.85, -65.0)
    assert out["pnl_price_eur"] > 0 and out["pnl_fx_eur"] < 0
    assert out["pnl_price_eur"] + out["pnl_fx_eur"] + out["basis_residual_eur"] \
        == pytest.approx(-65.0)


def test_fees_land_in_the_named_residual_not_in_the_thesis():
    # €5 of fees are inside the cost basis but not inside entry_price × qty, so the identity
    # leaves them in the residual. Crediting them to "price" would flatter the thesis.
    out = pm.pnl_split(100, 10.0, 11.0, 1.00, 1.00, 95.0)
    assert out["pnl_price_eur"] == pytest.approx(100.0)
    assert out["basis_residual_eur"] == pytest.approx(-5.0)


def test_an_unpriceable_position_splits_into_nothing_rather_than_zeros():
    out = pm.pnl_split(100, None, 11.0, 1.0, 1.0, 50.0)
    assert out == {"pnl_price_eur": None, "pnl_fx_eur": None, "basis_residual_eur": None}


# ── Statistics that refuse to guess ──────────────────────────────────────────

def test_short_samples_return_none_not_a_confident_number():
    assert pm.annualized_vol([0.01, -0.01]) is None
    assert pm.sharpe([0.01, -0.01]) is None
    assert pm.beta([0.01, 0.02], [0.01, 0.02]) is None
    assert pm.tracking_error([0.01, 0.02], [0.01, 0.02]) is None
    assert pm.correlation([0.01, 0.02], [0.01, 0.02]) is None


def test_a_flat_series_has_no_sharpe_rather_than_a_sharpe_of_zero():
    # sd == 0 → the ratio is undefined. Printing 0.0 would read as "measured, and mediocre".
    assert pm.sharpe([0.0, 0.0, 0.0, 0.0]) is None
    assert pm.beta([0.01, 0.02, 0.03], [0.0, 0.0, 0.0]) is None


def test_beta_and_correlation_are_reported_together_because_beta_alone_misleads():
    # A book twice as volatile and half as correlated has beta exactly 1.00 — which reads as
    # "we track the index" and is the opposite of the truth. This is the real book's case.
    bench = [0.01, -0.01, 0.02, -0.02, 0.01, -0.01]
    book = [0.04, 0.00, 0.00, -0.04, 0.04, 0.00]
    b, c = pm.beta(book, bench), pm.correlation(book, bench)
    assert b is not None and c is not None
    assert abs(c) < 0.9                      # not tracking
    assert pm.annualized_vol(book) > pm.annualized_vol(bench)


def test_max_drawdown_measures_from_the_peak_not_from_the_start():
    # Up 40%, back to +5%: a −25% round trip that a cost-basis view reports as a healthy +5%.
    dd = pm.max_drawdown([100.0, 140.0, 105.0, 110.0])
    assert dd["max_drawdown_pct"] == pytest.approx(-25.0)
    assert dd["peak_idx"] == 1 and dd["trough_idx"] == 2


def test_a_monotonic_series_has_no_drawdown():
    assert pm.max_drawdown([100.0, 101.0, 102.0])["max_drawdown_pct"] == pytest.approx(0.0)


# ── Shape of the book ────────────────────────────────────────────────────────

def test_active_share_counts_undeployed_capital_as_a_difference():
    # The model wants 40% of capital across two names; we hold one at 10% and the rest is cash.
    # The cash IS an active bet against the model, so it must not net out to zero.
    assert pm.active_share({"a": 10.0}, {"a": 20.0, "b": 20.0}) == pytest.approx(15.0)
    assert pm.active_share({"a": 20.0, "b": 20.0}, {"a": 20.0, "b": 20.0}) == pytest.approx(0.0)


def test_model_overlap_answers_the_question_active_share_cannot():
    # "How much of the model do we actually own?" — 10 of the 40 points it wants.
    assert pm.model_overlap({"a": 10.0}, {"a": 20.0, "b": 20.0}) == pytest.approx(25.0)
    assert pm.model_overlap({"a": 20.0, "b": 20.0}, {"a": 20.0, "b": 20.0}) == pytest.approx(100.0)
    # Overweighting one name does not buy credit for the one we do not hold.
    assert pm.model_overlap({"a": 90.0}, {"a": 20.0, "b": 20.0}) == pytest.approx(50.0)
    assert pm.model_overlap({"a": 10.0}, {}) is None


def test_hhi_reads_as_concentration():
    assert pm.hhi([100.0]) == pytest.approx(10000.0)
    assert pm.hhi([25.0, 25.0, 25.0, 25.0]) == pytest.approx(2500.0)
    assert pm.hhi([]) is None


def test_fx_exposure_is_a_share_of_the_marked_book_and_folds_pence_into_gbp():
    rows = [{"currency": "EUR", "market_value_eur": 600.0},
            {"currency": "USD", "market_value_eur": 300.0},
            {"currency": "GBX", "market_value_eur": 100.0}]
    assert pm.fx_exposure(rows) == {"EUR": 60.0, "USD": 30.0, "GBP": 10.0}
    assert pm.fx_exposure([]) == {}


# ── Score drift ──────────────────────────────────────────────────────────────

def test_drift_is_signed_so_positive_always_means_worse_than_at_entry():
    d = pm.score_drift({"composite": 90.0, "rank": 2}, {"composite": 70.0, "rank": 9})
    assert d["composite_drift"] == pytest.approx(-20.0)     # composite fell
    assert d["rank_drift"] == 7                             # rank number grew
    assert d["drift_note"] is None


def test_a_missing_side_names_which_side_is_missing():
    # Two different problems were both printing as "—", and only one is fixable by re-ingesting.
    assert "score_context at entry" in pm.score_drift(None, {"composite": 70.0})["drift_note"]
    assert "not in the current run" in pm.score_drift({"composite": 70.0}, None)["drift_note"]
    assert pm.score_drift(None, None)["composite_drift"] is None


# ── The NAV-mode bug ─────────────────────────────────────────────────────────

def test_nav_series_never_splices_backtest_and_live_into_one_curve(tmp_path):
    from catalyx.store import lake

    rows = [
        {"portfolio_id": "catalyx", "kind": "model", "mode": "backtest", "date": "2026-06-05",
         "nav": 100.0, "computed_at": "2026-06-05"},
        {"portfolio_id": "catalyx", "kind": "model", "mode": "live", "date": "2026-06-05",
         "nav": 82.0, "computed_at": "2026-06-05"},
        {"portfolio_id": "catalyx", "kind": "model", "mode": "backtest", "date": "2026-06-08",
         "nav": 101.0, "computed_at": "2026-06-08"},
        {"portfolio_id": "catalyx", "kind": "model", "mode": "live", "date": "2026-06-08",
         "nav": 83.0, "computed_at": "2026-06-08"},
    ]
    lake.append_partition("portfolio_nav", pd.DataFrame(rows), {"portfolio_id": "catalyx"},
                          overwrite=True, lake_dir=tmp_path)
    live = pm._nav_series("catalyx", mode="live", lake_dir=tmp_path)
    assert [r["nav"] for r in live] == [82.0, 83.0]
    # Unfiltered, the two curves interleave and manufacture a ~22% daily move out of nothing.
    mixed = pm.daily_returns([float(r["nav"])
                              for r in pm._nav_series("catalyx", lake_dir=tmp_path)])
    assert max(abs(r) for r in mixed) < 0.05, "dedupe by date must not leave both modes in"


def test_nav_series_keeps_the_last_write_per_date(tmp_path):
    from catalyx.store import lake

    rows = [{"portfolio_id": "real", "kind": "real", "mode": None, "date": "2026-06-05",
             "nav": 90.0, "computed_at": "2026-06-05T10:00"},
            {"portfolio_id": "real", "kind": "real", "mode": None, "date": "2026-06-05",
             "nav": 91.0, "computed_at": "2026-06-05T18:00"}]
    lake.append_partition("portfolio_nav", pd.DataFrame(rows), {"portfolio_id": "real"},
                          overwrite=True, lake_dir=tmp_path)
    series = pm._nav_series("real", lake_dir=tmp_path)
    assert len(series) == 1 and series[0]["nav"] == 91.0
