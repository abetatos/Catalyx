"""Market review triggers (v15 Phase 5) — synthetic frames only; no test reads the real cache.

Run: uv run pytest tests/unit/test_review_triggers.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from catalyx.config import weights
from catalyx.scorer import review_triggers as rt

CFG = {"us10y_change_bp_20d": 40, "brent_change_pct_20d": 15,
       "equity_drawdown_pct_from_20d_high": 5}


def _frame(n=60, tnx=None, brent=None, spy=None, stoxx=None):
    idx = pd.bdate_range("2026-07-01", periods=n)
    def flat(v):
        return np.full(n, float(v))
    cols = {"^TNX": tnx if tnx is not None else flat(4.0),
            "BZ=F": brent if brent is not None else flat(80.0),
            "SPY": spy if spy is not None else flat(500.0),
            "^STOXX50E": stoxx if stoxx is not None else flat(5000.0)}
    return pd.DataFrame(cols, index=idx)


def _ramp(start, end, n=60, over=20):
    a = np.full(n, float(start)); a[-over - 1:] = np.linspace(start, end, over + 1)
    return a


def test_quiet_market_fires_nothing():
    assert rt.evaluate(_frame(), CFG) == []


def test_us10y_up_50bp_in_20_sessions_alerts_and_10bp_does_not():
    assert any("US 10y" in m for m in rt.evaluate(_frame(tnx=_ramp(4.0, 4.5)), CFG))
    assert rt.evaluate(_frame(tnx=_ramp(4.0, 4.1)), CFG) == []


def test_a_yield_plunge_also_alerts():
    assert any("US 10y" in m for m in rt.evaluate(_frame(tnx=_ramp(4.5, 4.0)), CFG))


def test_yield_unit_is_percent_not_basis_points():
    ms = rt.moves(_frame(tnx=_ramp(4.0, 4.5)), CFG)
    assert ms[0]["value"] == 50.0                    # 0.50 in the series = 50bp


def test_brent_down_20_pct_alerts_and_up_10_pct_does_not():
    assert any("Brent" in m for m in rt.evaluate(_frame(brent=_ramp(100, 80)), CFG))
    assert rt.evaluate(_frame(brent=_ramp(100, 110)), CFG) == []


def test_spy_6_pct_below_its_20_session_high_alerts():
    spy = np.full(60, 500.0); spy[-1] = 470.0
    ms = rt.moves(_frame(spy=spy), CFG)
    assert [m["ticker"] for m in ms] == ["SPY"] and ms[0]["value"] == -6.0


def test_a_drawdown_older_than_20_sessions_is_not_read():
    spy = np.full(60, 500.0); spy[:30] = 600.0       # the high is 30 sessions back
    assert rt.evaluate(_frame(spy=spy), CFG) == []


def test_stoxx_drawdown_alerts_independently():
    sx = np.full(60, 5000.0); sx[-1] = 4700.0
    ms = rt.moves(_frame(stoxx=sx), CFG)
    assert [m["ticker"] for m in ms] == ["^STOXX50E"]


def test_missing_ticker_is_unavailable_not_quiet():
    f = _frame().drop(columns=["BZ=F"])
    assert "BZ=F unavailable for review trigger" in rt.evaluate(f, CFG)


def test_short_series_is_unavailable():
    f = _frame(n=15)
    msgs = rt.evaluate(f, CFG)
    assert len(msgs) == 4 and all("unavailable" in m for m in msgs)


def test_none_frame_is_unavailable_for_all():
    assert len(rt.evaluate(None, CFG)) == 4


def test_key_carries_ticker_and_last_date():
    ms = rt.moves(_frame(brent=_ramp(100, 80)), CFG)
    assert ms[0]["key"] == f"market:BZ=F:{_frame().index[-1].date()}"


def test_config_section_is_read_with_the_measured_thresholds():
    cfg = weights.review_triggers()
    assert cfg["us10y_change_bp_20d"] == 40 and cfg["brent_change_pct_20d"] == 15
    assert cfg["equity_drawdown_pct_from_20d_high"] == 5


def test_macro_tickers_are_refreshed_but_not_in_the_benchmarks():
    from catalyx.data import prices
    assert set(prices.MACRO_TICKERS) <= set(prices.universe_tickers())
    assert not set(prices.MACRO_TICKERS) & set(prices.BENCHMARK_TICKERS)
