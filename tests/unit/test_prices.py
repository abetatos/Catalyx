"""Unit tests for the shared price cache (catalyx.data.prices).

Every test injects a synthetic `fetch_fn`/`ccy_fn` and a tmp lake — the cache is verified with
NO network, which is the point of the module (the pipeline must run offline and reproducibly).
"""
from __future__ import annotations

import pandas as pd
import pytest

from catalyx.data import prices


def _frame(dates: list[str], cols: dict[str, list[float]]) -> pd.DataFrame:
    return pd.DataFrame(cols, index=pd.to_datetime(dates))


@pytest.fixture
def fake_fetch():
    """Records every call so a test can assert the cache did NOT hit the backend twice."""
    calls: list[tuple] = []

    def fetch(tickers, start, end):
        calls.append((tuple(tickers), start, end))
        dates = [d for d in pd.bdate_range(start, end).strftime("%Y-%m-%d")]
        return _frame(dates, {t: [100.0 + i for i in range(len(dates))] for t in tickers})

    fetch.calls = calls
    return fetch


def _ccy(tickers):
    return {t: ("USD" if t.endswith("=X") or t == "SPY" else "EUR") for t in tickers}


# ── Pure helpers ─────────────────────────────────────────────────────────────

def test_missing_tail_uses_meta_not_row_presence():
    # A weekend has no price row; coverage is defined by fetched_through, so asking for a
    # Saturday inside the covered window must NOT count as a miss (else we refetch forever).
    assert prices.missing_tail("2026-08-25", "2026-08-22") is False
    assert prices.missing_tail("2026-08-25", "2026-08-25") is False
    assert prices.missing_tail("2026-08-25", "2026-08-26") is True
    assert prices.missing_tail(None, "2026-08-26") is True


def test_missing_head_detects_a_deeper_window():
    assert prices.missing_head("2026-01-01", "2026-02-01") is False
    assert prices.missing_head("2026-01-01", "2025-06-01") is True
    assert prices.missing_head(None, "2026-01-01") is True


def test_merge_series_lets_a_restated_close_win():
    cached = pd.DataFrame({"ticker": ["A", "A"], "date": ["2026-01-02", "2026-01-05"],
                           "close": [10.0, 11.0]})
    fresh = pd.DataFrame({"ticker": ["A"], "date": ["2026-01-05"], "close": [11.5]})
    out = prices.merge_series(cached, fresh)
    assert len(out) == 2                                    # no duplicate date
    assert out[out["date"] == "2026-01-05"]["close"].iloc[0] == 11.5   # split/div restatement wins


def test_wide_frame_shape_matches_the_price_fn_contract():
    long_df = pd.DataFrame({
        "ticker": ["A", "A", "B"],
        "date": ["2026-01-02", "2026-01-05", "2026-01-05"],
        "close": [10.0, 11.0, 50.0],
    })
    wide = prices.wide_frame(long_df, ["A", "B"], "2026-01-01", "2026-01-31")
    assert list(wide.columns) == ["A", "B"]                 # requested order preserved
    assert wide.loc[pd.Timestamp("2026-01-05"), "B"] == 50.0
    # Out-of-window rows are clipped, not returned.
    assert prices.wide_frame(long_df, ["A"], "2026-01-03", "2026-01-04").empty


def test_wide_frame_omits_uncached_tickers_instead_of_erroring():
    long_df = pd.DataFrame({"ticker": ["A"], "date": ["2026-01-05"], "close": [10.0]})
    wide = prices.wide_frame(long_df, ["A", "MISSING"], "2026-01-01", "2026-01-31")
    assert list(wide.columns) == ["A"]      # consumers treat a missing column as "hold flat"


# ── Cache behaviour ──────────────────────────────────────────────────────────

def test_refresh_then_read_serves_from_cache_without_refetching(tmp_path, fake_fetch):
    prices.refresh(["AAA", "BBB"], "2026-01-01", "2026-01-31",
                   fetch_fn=fake_fetch, ccy_fn=_ccy, lake_dir=tmp_path)
    assert len(fake_fetch.calls) == 1
    assert fake_fetch.calls[0][0] == ("AAA", "BBB")          # ONE batched call, not one per ticker

    df = prices.read(["AAA", "BBB"], "2026-01-05", "2026-01-20",
                     fetch_fn=fake_fetch, ccy_fn=_ccy, lake_dir=tmp_path)
    assert len(fake_fetch.calls) == 1                        # second read hit the cache only
    assert list(df.columns) == ["AAA", "BBB"]
    assert df.index.min() >= pd.Timestamp("2026-01-05")


def test_refresh_only_fetches_the_tickers_whose_window_moved(tmp_path, fake_fetch):
    prices.refresh(["AAA", "BBB"], "2026-01-01", "2026-01-31",
                   fetch_fn=fake_fetch, ccy_fn=_ccy, lake_dir=tmp_path)
    prices.refresh(["AAA", "CCC"], "2026-01-01", "2026-01-31",
                   fetch_fn=fake_fetch, ccy_fn=_ccy, lake_dir=tmp_path)
    assert fake_fetch.calls[-1][0] == ("CCC",)               # AAA was already covered

    # A window that reaches past fetched_through refetches — that is the only tail trigger.
    prices.refresh(["AAA"], "2026-01-01", "2026-02-10",
                   fetch_fn=fake_fetch, ccy_fn=_ccy, lake_dir=tmp_path)
    assert fake_fetch.calls[-1][0] == ("AAA",)


def test_refresh_merges_new_dates_into_the_existing_partition(tmp_path, fake_fetch):
    prices.refresh(["AAA"], "2026-01-01", "2026-01-15",
                   fetch_fn=fake_fetch, ccy_fn=_ccy, lake_dir=tmp_path)
    prices.refresh(["AAA"], "2026-01-01", "2026-02-15",
                   fetch_fn=fake_fetch, ccy_fn=_ccy, lake_dir=tmp_path)
    df = prices.read(["AAA"], "2026-01-01", "2026-02-15", allow_fetch=False, lake_dir=tmp_path)
    assert df.index.max() >= pd.Timestamp("2026-02-12")      # history extended, not replaced
    assert df.index.min() <= pd.Timestamp("2026-01-02")


def test_offline_read_never_calls_the_backend(tmp_path, fake_fetch, monkeypatch):
    prices.refresh(["AAA"], "2026-01-01", "2026-01-31",
                   fetch_fn=fake_fetch, ccy_fn=_ccy, lake_dir=tmp_path)
    n = len(fake_fetch.calls)
    monkeypatch.setenv("CATALYX_PRICES_OFFLINE", "1")
    df = prices.read(["AAA"], "2026-01-01", "2026-06-30",       # window past coverage…
                     fetch_fn=fake_fetch, ccy_fn=_ccy, lake_dir=tmp_path)
    assert len(fake_fetch.calls) == n                            # …still no fetch
    assert not df.empty                                          # serves what it has


def test_read_with_allow_fetch_false_is_cache_only(tmp_path, fake_fetch):
    df = prices.read(["NOPE"], "2026-01-01", "2026-01-31", allow_fetch=False, lake_dir=tmp_path)
    assert df.empty
    assert fake_fetch.calls == []


def test_currencies_are_cached_after_the_first_lookup(tmp_path, fake_fetch):
    seen: list[list[str]] = []

    def ccy(tickers):
        seen.append(list(tickers))
        return _ccy(tickers)

    prices.refresh(["SPY"], "2026-01-01", "2026-01-31",
                   fetch_fn=fake_fetch, ccy_fn=ccy, lake_dir=tmp_path)
    assert prices.currencies(["SPY"], ccy_fn=ccy, lake_dir=tmp_path) == {"SPY": "USD"}
    assert len(seen) == 1        # the listing currency never changes → one lookup, ever


def test_empty_backend_result_does_not_poison_the_cache(tmp_path):
    def dead_fetch(tickers, start, end):
        return pd.DataFrame()

    out = prices.refresh(["GONE"], "2026-01-01", "2026-01-31",
                         fetch_fn=dead_fetch, ccy_fn=_ccy, lake_dir=tmp_path)
    assert out["GONE"]["rows"] == 0
    assert prices.read(["GONE"], "2026-01-01", "2026-01-31",
                       allow_fetch=False, lake_dir=tmp_path).empty


def test_coverage_reports_what_is_cached(tmp_path, fake_fetch):
    prices.refresh(["AAA", "BBB"], "2026-01-01", "2026-01-31",
                   fetch_fn=fake_fetch, ccy_fn=_ccy, lake_dir=tmp_path)
    rows = {r["ticker"]: r for r in prices.coverage(lake_dir=tmp_path)}
    assert set(rows) == {"AAA", "BBB"}
    assert rows["AAA"]["fetched_through"] == "2026-01-31"
    assert rows["AAA"]["n_rows"] > 0


# ── Universe parsing (regression: the failure mode here is SILENT) ───────────

def test_universe_tickers_reads_the_v2_shape(tmp_path, monkeypatch):
    """etf_universe v2.0 is {sector_id: [ {ticker: …} ]}. Parsing it as {sector_id: {etfs: […]}}
    returns only benchmarks with NO error — the cache silently stops covering the book."""
    import yaml

    cfg = tmp_path / "catalyx" / "config"
    cfg.mkdir(parents=True)
    (cfg / "etf_universe.yaml").write_text(yaml.safe_dump({"etf_universe": {
        "eu_defense_prime_contractors": [{"ticker": "EUDF.L"}, {"ticker": "NATO.PA"}],
        "copper_miners": [{"ticker": "4COP.DE"}],
    }}), encoding="utf-8")
    monkeypatch.setattr(prices, "__file__", str(tmp_path / "catalyx" / "data" / "prices.py"))

    got = prices.universe_tickers()
    assert {"EUDF.L", "NATO.PA", "4COP.DE"} <= set(got)
    assert set(prices.BENCHMARK_TICKERS) <= set(got)     # benchmarks + FX always included
    assert set(prices.FX_TICKERS) <= set(got)
    assert len(got) == len(set(got))                     # deduped


def test_universe_tickers_tolerates_a_dict_wrapper(tmp_path, monkeypatch):
    import yaml

    cfg = tmp_path / "catalyx" / "config"
    cfg.mkdir(parents=True)
    (cfg / "etf_universe.yaml").write_text(yaml.safe_dump({"etf_universe": {
        "copper_miners": {"etfs": [{"ticker": "4COP.DE"}]},
    }}), encoding="utf-8")
    monkeypatch.setattr(prices, "__file__", str(tmp_path / "catalyx" / "data" / "prices.py"))
    assert "4COP.DE" in prices.universe_tickers()


def test_the_real_universe_file_yields_the_whole_book():
    # Guards the live config: if the shape changes again, this fails loudly instead of quietly
    # shrinking the cache to benchmarks.
    got = prices.universe_tickers()
    assert len(got) > 20, f"only {len(got)} tickers parsed from the real etf_universe.yaml"
