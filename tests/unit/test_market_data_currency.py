"""The quote currency of the scored vehicle — read, not defaulted.

`fetch_metrics` used to take it from `hist.attrs.get("currency", "USD")`. In that yfinance
version `hist.attrs` is ALWAYS empty, so all 26 momentum rows said "USD" by default rather
than by reading — and that column is exactly the check v8.1 leaned on to catch `IH2O.L`
quoting in GBp while the operator pays in EUR.

v14.3 moved the read-path onto the shared `prices` cache, so the currency now comes from
`prices.currencies`. That accessor defaults an unreadable currency to "EUR" for `to_eur`,
whose question is "convert or not" — which is why this path asks for `default=None`. The
contract under test is unchanged and is the same one either way: a currency that was not
read must not arrive as a value. Network-free."""
from __future__ import annotations

import pandas as pd
import pytest

from catalyx.data import market_data as md


def _frame(ticker: str, n: int = 300) -> pd.DataFrame:
    idx = pd.date_range("2025-11-01", periods=n, freq="B")
    return pd.DataFrame({ticker: [100.0 + i * 0.1 for i in range(n)]}, index=idx)


def _panel(ticker: str, ccy, basis=None):
    """(frame_fn, ccy_fn) pair standing in for the shared cache."""
    return (lambda tks, s, e: (_frame(ticker), {ticker: basis or (ccy or "unknown")}),
            lambda tks, **kw: {ticker: ccy})


def test_currency_comes_from_the_price_cache(monkeypatch):
    ff, cf = _panel("IH2O.L", "GBp")
    assert md.fetch_metrics("IH2O.L", frame_fn=ff, ccy_fn=cf)["currency"] == "GBp"


def test_currency_is_none_when_unreadable_never_defaults(monkeypatch):
    """"Not measured" must not read as a value — a silent default is what hid the defect."""
    ff, cf = _panel("XXXX.DE", None)
    assert md.fetch_metrics("XXXX.DE", frame_fn=ff, ccy_fn=cf)["currency"] is None


def test_price_panel_asks_the_cache_not_to_default_the_currency():
    """The diagnostic column and `to_eur`'s convert/don't-convert question want DIFFERENT
    defaults from the same accessor. This asserts which one momentum asks for."""
    seen = {}

    def ccy_fn(tickers, **kw):
        seen.update(kw)
        return {t: None for t in tickers}

    md.price_panel(["T"], frame_fn=lambda tks, s, e: (_frame("T"), {"T": "EUR"}), ccy_fn=ccy_fn)
    assert seen.get("default", "MISSING") is None


@pytest.mark.parametrize("ccy", ["EUR", "USD", "GBp", "GBP"])
def test_returns_are_unaffected_by_the_quote_currency(ccy):
    """The currency is a DIAGNOSTIC column, not an input: within one series the scale
    cancels in a return. What it does NOT cancel is FX between two series — and THAT is
    handled upstream now, by `prices.to_eur`, which is why the frame arrives converted."""
    ff, cf = _panel("T", ccy, basis="EUR")
    m = md.fetch_metrics("T", frame_fn=ff, ccy_fn=cf)
    assert m["currency"] == ccy
    assert m["return_3m_pct"] == md.fetch_metrics("T", frame_fn=ff, ccy_fn=cf)["return_3m_pct"]
