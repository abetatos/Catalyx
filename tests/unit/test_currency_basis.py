"""Everything that RANKS or MEASURES across vehicles is denominated in EUR (v10.4).

Within one series the quote scale cancels in a return; between series it does not, and 15 of
the 26 scored vehicles quote outside the euro. The signal path (momentum), the TARGET path
(calibration's forward returns) and the risk path (covariance, comomentum) must all be on the
operator's currency, or a verdict prices a return nobody earned. Network-free."""
from __future__ import annotations

import pandas as pd
import pytest

from catalyx.data import prices


def _fx(dates, rate=1.25):
    """EUR per 1 unit — a CONSTANT rate, so any return difference is the conversion, not drift."""
    return pd.Series([rate] * len(dates), index=dates)


def _frame(dates):
    return pd.DataFrame({"EURFUND.DE": [100.0 + i for i in range(len(dates))],
                         "USDFUND": [200.0 + 2 * i for i in range(len(dates))],
                         "PENCE.L": [500.0 + 5 * i for i in range(len(dates))]}, index=dates)


DATES = pd.date_range("2026-01-01", periods=40, freq="B")


def test_to_eur_leaves_eur_columns_untouched():
    f = _frame(DATES)
    out = prices.to_eur(f, {"EURFUND.DE": "EUR", "USDFUND": "USD", "PENCE.L": "GBp"},
                        {"USD": _fx(DATES), "GBP": _fx(DATES, 1.15)})
    pd.testing.assert_series_equal(out["EURFUND.DE"], f["EURFUND.DE"])


def test_pence_is_scaled_by_a_hundredth_on_top_of_fx():
    f = _frame(DATES)
    out = prices.to_eur(f, {"PENCE.L": "GBp"}, {"GBP": _fx(DATES, 1.15)})
    assert out["PENCE.L"].iloc[0] == pytest.approx(500.0 * 1.15 * 0.01)


def test_a_column_with_no_fx_series_stays_native_and_is_not_dropped():
    """Dropping it would silently shrink the cross-section a ranking is computed over."""
    f = _frame(DATES)
    out = prices.to_eur(f, {"USDFUND": "USD"}, {})          # FX unavailable
    assert "USDFUND" in out.columns
    pd.testing.assert_series_equal(out["USDFUND"], f["USDFUND"])


def test_basis_by_ticker_reports_unconverted_columns_as_native():
    b = prices.basis_by_ticker(["EURFUND.DE", "USDFUND", "PENCE.L"],
                               fx_by_ccy={"GBP": _fx(DATES)},
                               ccy_map={"EURFUND.DE": "EUR", "USDFUND": "USD", "PENCE.L": "GBp"})
    assert b == {"EURFUND.DE": "EUR", "USDFUND": "USD", "PENCE.L": "EUR"}


def test_constant_fx_does_not_change_a_single_series_return():
    """The scale cancels WITHIN a series — that is why this migration changes nothing for a
    lone ticker and everything for a ranking."""
    f = _frame(DATES)
    out = prices.to_eur(f, {"USDFUND": "USD"}, {"USD": _fx(DATES)})
    native = f["USDFUND"].iloc[-1] / f["USDFUND"].iloc[0] - 1
    in_eur = out["USDFUND"].iloc[-1] / out["USDFUND"].iloc[0] - 1
    assert in_eur == pytest.approx(native)


def test_moving_fx_does_change_it():
    f = _frame(DATES)
    drift = pd.Series([1.0 + 0.002 * i for i in range(len(DATES))], index=DATES)
    out = prices.to_eur(f, {"USDFUND": "USD"}, {"USD": drift})
    native = f["USDFUND"].iloc[-1] / f["USDFUND"].iloc[0] - 1
    in_eur = out["USDFUND"].iloc[-1] / out["USDFUND"].iloc[0] - 1
    assert in_eur != pytest.approx(native)
    assert in_eur > native          # the currency appreciated against the euro


def test_fx_is_reindexed_onto_the_instruments_own_dates():
    """A union index would insert days the fund did not trade, so "N rows back" would span a
    different calendar window — measured, that confound alone doubled the apparent FX effect
    on the ranking (26 places/max 3 vs the true 12/max 2)."""
    f = _frame(DATES)
    denser = pd.date_range(DATES[0], DATES[-1], freq="D")     # FX trades on more days
    out = prices.to_eur(f, {"USDFUND": "USD"}, {"USD": _fx(denser)})
    assert list(out.index) == list(f.index)
    assert len(out) == len(f)


# ── the four consumers actually ask for EUR ──────────────────────────────────

def test_calibration_forward_returns_reads_eur(monkeypatch):
    seen = {}
    def fake(tickers, start, end, allow_fetch=True, **kw):
        seen["called"] = True
        return pd.DataFrame({"X": [100.0, 110.0]}, index=pd.date_range("2026-01-01", periods=2))
    monkeypatch.setattr(prices, "read_eur", fake)
    monkeypatch.setattr(prices, "read", lambda *a, **k: pytest.fail("must not read native"))
    from catalyx.scorer import calibration
    out = calibration.forward_returns(["X"], "2026-01-01", "2026-01-02")
    assert seen.get("called") and out["X"] == pytest.approx(0.10)


def test_covariance_reads_eur(monkeypatch):
    monkeypatch.setattr(prices, "read", lambda *a, **k: pytest.fail("must not read native"))
    monkeypatch.setattr(prices, "read_eur",
                        lambda *a, **k: pd.DataFrame({"X": [1.0, 2.0]},
                                                     index=pd.date_range("2026-01-01", periods=2)))
    from catalyx.scorer import covariance
    assert not covariance._prices(["X"], 4, "2026-02-01").empty


def test_momentum_row_declares_its_basis(monkeypatch):
    from catalyx.data import market_data as md

    class _T:
        def history(self, **_):
            idx = pd.date_range("2025-11-01", periods=300, freq="B")
            return pd.DataFrame({"Close": [100.0 + i * 0.1 for i in range(300)]}, index=idx)
        @property
        def history_metadata(self):
            return {"currency": "USD"}

    monkeypatch.setattr(md.yf, "Ticker", lambda t: _T())
    monkeypatch.setattr(prices, "fx_series",
                        lambda ccys, s, e, **kw: {"USD": pd.Series(
                            [1.1] * 400, index=pd.date_range("2025-10-01", periods=400, freq="D"))})
    m = md.fetch_metrics("USDFUND")
    assert m["currency"] == "USD" and m["return_basis"] == "EUR"


def test_momentum_row_stays_native_and_says_so_when_fx_is_unavailable(monkeypatch):
    from catalyx.data import market_data as md

    class _T:
        def history(self, **_):
            idx = pd.date_range("2025-11-01", periods=300, freq="B")
            return pd.DataFrame({"Close": [100.0 + i * 0.1 for i in range(300)]}, index=idx)
        @property
        def history_metadata(self):
            return {"currency": "USD"}

    monkeypatch.setattr(md.yf, "Ticker", lambda t: _T())
    monkeypatch.setattr(prices, "fx_series", lambda *a, **k: {})
    m = md.fetch_metrics("USDFUND")
    assert m["return_basis"] == "USD"      # NOT "EUR" — an unconverted row must not claim EUR
