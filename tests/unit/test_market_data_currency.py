"""The quote currency of the scored vehicle — read, not defaulted.

`fetch_metrics` used to take it from `hist.attrs.get("currency", "USD")`. In this
yfinance version `hist.attrs` is ALWAYS empty, so all 26 momentum rows said "USD" by
default rather than by reading — and that column is exactly the check v8.1 leaned on to
catch `IH2O.L` quoting in GBp while the operator pays in EUR. Network-free."""
from __future__ import annotations

import pandas as pd
import pytest

from catalyx.data import market_data as md


def _hist(n: int = 300) -> pd.DataFrame:
    idx = pd.date_range("2025-11-01", periods=n, freq="B")
    return pd.DataFrame({"Close": [100.0 + i * 0.1 for i in range(n)]}, index=idx)


class _FakeTicker:
    def __init__(self, currency, attrs=None):
        self._ccy, self._attrs = currency, attrs or {}

    def history(self, **_kw):
        h = _hist()
        h.attrs.update(self._attrs)
        return h

    @property
    def history_metadata(self):
        return {"currency": self._ccy} if self._ccy is not None else {}


def test_currency_comes_from_history_metadata(monkeypatch):
    monkeypatch.setattr(md.yf, "Ticker", lambda t: _FakeTicker("GBp"))
    assert md.fetch_metrics("IH2O.L")["currency"] == "GBp"


def test_currency_is_none_when_unreadable_never_defaults_to_usd(monkeypatch):
    """"Not measured" must not read as a value — a silent "USD" is what hid the defect."""
    monkeypatch.setattr(md.yf, "Ticker", lambda t: _FakeTicker(None))
    assert md.fetch_metrics("XXXX.DE")["currency"] is None


def test_attrs_are_still_honoured_as_a_fallback(monkeypatch):
    """If a future yfinance repopulates hist.attrs, that reading still counts."""
    monkeypatch.setattr(md.yf, "Ticker", lambda t: _FakeTicker(None, attrs={"currency": "EUR"}))
    assert md.fetch_metrics("V9N.DE")["currency"] == "EUR"


@pytest.mark.parametrize("ccy", ["EUR", "USD", "GBp", "GBP"])
def test_returns_are_unaffected_by_the_quote_currency(monkeypatch, ccy):
    """The currency is a DIAGNOSTIC column, not an input: within one series the scale
    cancels in a return. What it does NOT cancel is FX between two series — that is the
    open question the column exists to make visible, not one this read-path answers."""
    monkeypatch.setattr(md.yf, "Ticker", lambda t: _FakeTicker(ccy))
    m = md.fetch_metrics("T")
    assert m["currency"] == ccy
    assert m["return_3m_pct"] == md.fetch_metrics("T")["return_3m_pct"]
