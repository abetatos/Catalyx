"""The official momentum spec must reach the whole universe, not the venues with long years.

v8 Q1 promoted `0.635 x pct(12-1) + 0.365 x pct(52w-high)` on a 176-month backtest. v14.1
noticed it was not actually running: `fetch_metrics` asked yfinance for `period="1y"` and
`_safe_return(closes, 252)` needs 253 BARS, so whether a sector scored on the promoted spec
or fell to the weaker `3m6m_fallback` was decided by its exchange's trading calendar —
Xetra 251-252 bars, SIX 247, LSE 252-254, Euronext 255. Measured on the live run: 12 of 25.

Two properties are pinned here, because both failed SILENTLY and in the same direction:
  1. the lookback window is deep enough that a venue calendar cannot decide the spec, and
  2. the 52w high is a 52-WEEK high, not "the max of whatever window we fetched".
Network-free."""
from __future__ import annotations

import pandas as pd
import pytest

from catalyx.data import market_data as md


def _series(n: int, start: float = 100.0, step: float = 0.1) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.Series([start + i * step for i in range(n)], index=idx)


# ── 1. Coverage ───────────────────────────────────────────────────────────────

def test_the_requested_window_clears_the_12_1_anchor_on_the_thinnest_calendar():
    """450 calendar days must yield >253 bars even on the sparsest venue we price.

    SIX (GLUX.SW) returned 247 bars for 365 days — the thinnest measured calendar, ~247
    bars/365d. At that rate the window must still clear the anchor with room to spare."""
    bars_per_day = 247 / 365.0
    assert md.MOMENTUM_HISTORY_DAYS * bars_per_day > md.TRADING_DAYS["1y"] + 1 + 40


@pytest.mark.parametrize("n_bars", [253, 260, 280, 310])
def test_12_1_is_computable_for_every_venue_once_the_window_is_deep_enough(n_bars):
    m = md.compute_metrics("T", _series(n_bars), "EUR", "EUR")
    assert m["return_1y_pct"] is not None, "no 1y return → the row falls to 3m6m_fallback"
    assert m["return_1m_pct"] is not None


def test_a_venue_calendar_short_of_the_anchor_is_the_failure_being_prevented():
    """The regression itself: 251 bars (Xetra under `period=\"1y\"`) yields no 12-1. This is
    what the deep window exists to stop happening by venue rather than by history."""
    assert md.compute_metrics("T", _series(251), "EUR", "EUR")["return_1y_pct"] is None


def test_market_data_does_not_fetch_prices_itself():
    """It was the last module bypassing the shared cache — the bypass is what made the
    lookback a per-venue accident, so re-importing yfinance here is the regression."""
    text = open(md.__file__, encoding="utf-8").read()
    # Code lines only — the comments deliberately still describe the path being replaced.
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "import yfinance" not in code
    assert "yf.Ticker" not in code
    assert "yf.download" not in code


# ── 2. The 52-week high is 52 weeks ───────────────────────────────────────────

def test_52w_high_ignores_a_peak_older_than_the_window():
    """A high set 300 bars ago is not a 52-week high. Before v14.3 `closes.max()` ran over
    the WHOLE fetched series, so deepening the window to fix coverage would have silently
    re-defined 36.5% of the official blend for every row at the same time."""
    s = _series(310, start=100.0, step=0.0)
    s.iloc[5] = 500.0            # ancient spike, ~305 bars back
    s.iloc[-1] = 120.0           # today
    m = md.compute_metrics("T", s, "EUR", "EUR")
    assert m["high_52w"] == 120.0
    assert m["near_52w_high_pct"] == 0.0, "the ancient spike must not sit in the 52w window"


def test_52w_high_still_sees_a_peak_inside_the_window():
    s = _series(310, start=100.0, step=0.0)
    s.iloc[-100] = 200.0
    s.iloc[-1] = 100.0
    m = md.compute_metrics("T", s, "EUR", "EUR")
    assert m["high_52w"] == 200.0
    assert m["near_52w_high_pct"] == -50.0


def test_the_52w_window_is_the_same_length_as_the_12_1_anchor():
    """Both are read off one slice of one series; drifting them apart is how the high and
    the return start answering questions about different periods."""
    assert md.MOMENTUM_WINDOW_BARS == md.TRADING_DAYS["1y"]


def test_deepening_the_window_does_not_move_the_52w_high():
    """The invariant that makes the coverage fix safe to ship: same prices, deeper fetch,
    identical 52w-high reading. This is what was NOT true before."""
    s = _series(310, start=100.0, step=0.05)
    shallow = md.compute_metrics("T", s.iloc[-260:], "EUR", "EUR")
    deep = md.compute_metrics("T", s, "EUR", "EUR")
    assert shallow["high_52w"] == deep["high_52w"]
    assert shallow["near_52w_high_pct"] == deep["near_52w_high_pct"]


# ── 3. An ad-hoc run is not the day's snapshot ────────────────────────────────

def test_custom_ticker_runs_do_not_overwrite_the_days_snapshot(tmp_path, monkeypatch):
    """`--tickers` already skips the lake partition because it "would collide with the real
    daily partition" — but it wrote the dated JSON anyway, so a two-ticker smoke test replaced
    a recorded run's 25-sector snapshot under the name every reader resolves by date."""
    monkeypatch.chdir(tmp_path)
    idx = pd.date_range("2024-01-01", periods=300, freq="B")
    frame = pd.DataFrame({"SPY": [100.0 + i * 0.1 for i in range(300)]}, index=idx)
    monkeypatch.setattr(md, "price_panel",
                        lambda tks, **kw: (frame, {"SPY": "EUR"}, {"SPY": "USD"}))

    canonical = tmp_path / "data" / "snapshots" / f"momentum_snapshot_{md.date.today().isoformat()}.json"
    canonical.parent.mkdir(parents=True)
    canonical.write_text('{"sentinel": true}', encoding="utf-8")

    md.run_snapshot(tickers=["SPY"], show_table=False)
    assert '"sentinel": true' in canonical.read_text(encoding="utf-8")
