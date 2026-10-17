"""Shared price cache — ONE network fetch per run, for every module that needs prices.

WHY THIS EXISTS (v3 Phase 1, docs/PLAN_v3_lean_pipeline_rebalance.md §2.1):
    Before this module, a single `/catalyx-review` hit yfinance ~15 separate times:
    `market_data`, `flow_data`, `dislocation` (twice — `--window` and `--anchor-sectors`),
    `entry_timing --all`, `exit_watcher`, `nav_engine` ×9 (4 backtest + 4 live + real) and
    `technical_study`. Three costs came out of that:
      • WALL-CLOCK — the network round-trips were most of the run's minutes.
      • REPRODUCIBILITY — two modules in the same run saw DIFFERENT closes if a print landed
        between them, so the recorded run was not a consistent snapshot of one price date.
      • OFFLINE — any fetch failure silently degraded a scorer (a live `exit_watcher` run
        printed `pnl_pct_eur: None` for the whole book because its fetch came back empty).

    The fix is a cache in the lake, not a smarter fetch: `refresh()` pulls the union window
    ONCE and writes it; every module reads from parquet through the SAME `price_fn` contract
    it already accepts (`price_fn(tickers, start, end) -> DataFrame[date × ticker]`), so this
    is a drop-in default, not a rewrite. yfinance becomes the cache's backend, not every
    module's dependency.

CONTRACT — identical to the `yfinance_prices` it replaces: adjusted close in the ticker's
NATIVE listing currency, index = date, one column per ticker. FX conversion stays where it
already lives (`nav_engine._eur_prices`), which now reads its FX pairs from this same cache.

COVERAGE — the cache never re-fetches history it already holds. A ticker's `price_meta` row
records `fetched_through` (the `end` of its last fetch), so a trailing gap is fetched only
when the caller asks for a date beyond it. Weekends/holidays inside the window are NOT holes:
the meta row, not the presence of rows, defines coverage — otherwise every Sunday would look
like a miss and refetch forever.

OFFLINE — `CATALYX_PRICES_OFFLINE=1` (or `allow_fetch=False`) serves cache-only and never
touches the network: deterministic re-runs, and a usable pipeline on a plane.

CLI:
    uv run python -m catalyx.data.prices refresh [--tickers A,B] [--days 400]
    uv run python -m catalyx.data.prices show <ticker> [--limit 10]
    uv run python -m catalyx.data.prices coverage
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from catalyx.store import lake

_PRICES = "prices"
_META = "price_meta"

# Default history depth pulled on a cold fetch. Must cover the longest lookback any consumer
# asks for (technical_study's 200-day SMA + dislocation's 90d + nav since inception).
DEFAULT_HISTORY_DAYS = 400

# FX pairs the EUR conversion needs; cached like any other ticker so `nav_engine` and
# `exit_watcher` stop fetching them independently.
FX_TICKERS = ("EURUSD=X", "EURGBP=X")
BENCHMARK_TICKERS = ("SPY", "^VIX")
# v15 Phase 5 — review-trigger inputs (rates, oil, EU equity). NATIVE series, never EUR-converted:
# ^TNX is a YIELD in percent (5.1 = 5.1%), not a price, and BZ=F is a continuous future. Read with
# `allow_fetch=False`; `catalyx.scorer.review_triggers` is the only consumer. Not in any return panel.
MACRO_TICKERS = ("^TNX", "BZ=F", "^STOXX50E")


def _offline() -> bool:
    return os.environ.get("CATALYX_PRICES_OFFLINE", "").strip().lower() in ("1", "true", "yes")


# ── Pure helpers (unit-tested, no network, no lake) ───────────────────────────

def missing_tail(fetched_through: str | None, end: str) -> bool:
    """Does the cache need a fetch to answer up to `end`?

    Coverage is defined by the META row, not by whether rows exist on `end` — a weekend or
    holiday legitimately has no row and must not count as a miss.
    """
    if not fetched_through:
        return True
    return str(end)[:10] > str(fetched_through)[:10]


def missing_head(fetched_from: str | None, start: str) -> bool:
    """Does the cache need a fetch to answer back to `start`?"""
    if not fetched_from:
        return True
    return str(start)[:10] < str(fetched_from)[:10]


def merge_series(cached: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Union of two long-form [ticker, date, close] frames; `fresh` wins on a collision.

    A restated close (yfinance revises after a split/dividend) must overwrite the cached one,
    so dedupe keeps the LAST occurrence with `fresh` concatenated second.
    """
    if cached is None or cached.empty:
        return fresh.copy() if fresh is not None else pd.DataFrame()
    if fresh is None or fresh.empty:
        return cached.copy()
    both = pd.concat([cached, fresh], ignore_index=True)
    both = both.drop_duplicates(subset=["ticker", "date"], keep="last")
    return both.sort_values(["ticker", "date"], ignore_index=True)


def wide_frame(long_df: pd.DataFrame, tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """Long [ticker, date, close] → wide DataFrame[date × ticker] clipped to [start, end].

    This is the shape every consumer's `price_fn` expects. Columns are ordered as requested;
    a ticker with no cached rows is simply absent (consumers already treat a missing column
    as "hold flat / skip", so a partial cache degrades gracefully instead of erroring).
    """
    if long_df is None or long_df.empty:
        return pd.DataFrame()
    df = long_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    lo, hi = pd.Timestamp(str(start)[:10]), pd.Timestamp(str(end)[:10])
    df = df[(df["date"] >= lo) & (df["date"] <= hi)]
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot_table(index="date", columns="ticker", values="close", aggfunc="last")
    cols = [t for t in tickers if t in wide.columns]
    return wide[cols].sort_index() if cols else pd.DataFrame()


# ── Lake I/O ─────────────────────────────────────────────────────────────────

def _read_meta(lake_dir: Path | None = None) -> pd.DataFrame:
    df = lake.read_table(_META, lake_dir=lake_dir)
    return df if not df.empty else pd.DataFrame(
        columns=["ticker", "currency", "fetched_from", "fetched_through", "fetched_at", "n_rows"])


def _meta_row(meta: pd.DataFrame, ticker: str) -> dict | None:
    if meta.empty or "ticker" not in meta.columns:
        return None
    hit = meta[meta["ticker"] == ticker]
    return None if hit.empty else hit.iloc[-1].to_dict()


def _read_ticker(ticker: str, lake_dir: Path | None = None) -> pd.DataFrame:
    """Cached long-form rows for ONE ticker (its own partition file — no full-table scan)."""
    fp = lake.table_dir(_PRICES, lake_dir) / f"ticker={_safe(ticker)}.parquet"
    if not fp.exists():
        return pd.DataFrame(columns=["ticker", "date", "close"])
    return pd.read_parquet(fp)


def _safe(ticker: str) -> str:
    s = str(ticker)
    for bad in ("/", "\\", "=", ":", " "):
        s = s.replace(bad, "_")
    return s


# ── Fetch backend (injectable) ───────────────────────────────────────────────

def yfinance_fetch(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """Backend: adjusted closes for `tickers` over [start, end] INCLUSIVE, wide frame.

    yfinance's `end` is exclusive, which silently drops the final day of every window (the
    bug `nav_engine.yfinance_prices` documents), so it is pushed out one calendar day here —
    once, in the only place that still talks to yfinance for prices.
    """
    import yfinance as yf

    end_excl = (date.fromisoformat(str(end)[:10]) + timedelta(days=1)).isoformat()
    data = yf.download(list(tickers), start=str(start)[:10], end=end_excl,
                       progress=False, auto_adjust=True)
    if data is None or len(data) == 0:
        return pd.DataFrame()
    closes = data["Close"] if (isinstance(data.columns, pd.MultiIndex) or "Close" in getattr(data, "columns", [])) else data
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(list(tickers)[0])
    return closes


def yfinance_currencies(tickers: list[str]) -> dict[str, str]:
    """{ticker: listing currency}; unknown → 'EUR' (treated as no conversion needed)."""
    import yfinance as yf

    out: dict[str, str] = {}
    for t in tickers:
        try:
            out[t] = (yf.Ticker(t).fast_info.get("currency") or "EUR")
        except Exception:
            out[t] = "EUR"
    return out


# ── Public API ───────────────────────────────────────────────────────────────

def _clamp_through(fetched_through: str | None) -> str | None:
    """Coverage date to trust. One past today is an artefact (a future `end` was once recorded
    as fetched) and says nothing about what was really downloaded, so it reads as UNKNOWN (None):
    `missing_tail` then refetches, and the rewritten row is honest. Repairs rows already written
    without a migration."""
    if not fetched_through:
        return None
    t = str(fetched_through)[:10]
    return t if t <= date.today().isoformat() else None


def refresh(tickers: list[str], start: str | None = None, end: str | None = None,
            fetch_fn=None, ccy_fn=None, lake_dir: Path | None = None,
            force: bool = False) -> dict:
    """Bring the cache up to [start, end] for `tickers`, fetching ONLY what is missing.

    Returns a per-ticker summary {ticker: {"rows": n, "fetched": bool, "currency": ccy}}.
    """
    end = str(end or date.today().isoformat())[:10]
    start = str(start or (date.fromisoformat(end) - timedelta(days=DEFAULT_HISTORY_DAYS)).isoformat())[:10]
    fetch_fn = fetch_fn or yfinance_fetch
    ccy_fn = ccy_fn or yfinance_currencies

    meta = _read_meta(lake_dir)
    stale = []
    for t in tickers:
        row = _meta_row(meta, t)
        if force or row is None or missing_tail(_clamp_through(row.get("fetched_through")), end) \
                or missing_head(row.get("fetched_from"), start):
            stale.append(t)

    summary: dict[str, dict] = {}
    for t in tickers:
        if t not in stale:
            row = _meta_row(meta, t) or {}
            summary[t] = {"rows": int(row.get("n_rows") or 0), "fetched": False,
                          "currency": row.get("currency")}

    if not stale:
        return summary
    if _offline():
        for t in stale:
            summary[t] = {"rows": 0, "fetched": False, "currency": None, "skipped": "offline"}
        return summary

    # ONE batched call for every stale ticker over the union window — the whole point of the
    # cache. Per-ticker windows would restore the N-round-trip problem this module removes.
    wide = fetch_fn(stale, start, end)
    fetched_at = datetime.now(timezone.utc).isoformat()

    known_ccy = {r["ticker"]: r.get("currency") for _, r in meta.iterrows()} if not meta.empty else {}
    need_ccy = [t for t in stale if not known_ccy.get(t)]
    fresh_ccy = ccy_fn(need_ccy) if need_ccy else {}

    meta_rows = []
    for t in stale:
        col = wide[t] if (wide is not None and not wide.empty and t in getattr(wide, "columns", [])) else None
        fresh = pd.DataFrame(columns=["ticker", "date", "close"])
        if col is not None:
            s = col.dropna()
            if len(s):
                fresh = pd.DataFrame({
                    "ticker": t,
                    "date": [d.date().isoformat() if hasattr(d, "date") else str(d)[:10] for d in s.index],
                    "close": [float(v) for v in s.values],
                })
        merged = merge_series(_read_ticker(t, lake_dir), fresh)
        if not merged.empty:
            lake.append_partition(_PRICES, merged, {"ticker": t}, overwrite=True, lake_dir=lake_dir)

        prior = _meta_row(meta, t) or {}
        ccy = known_ccy.get(t) or fresh_ccy.get(t) or "EUR"
        fetched_from = min([x for x in (prior.get("fetched_from"), start) if x] or [start])
        # Coverage can never reach past TODAY: a request with a future `end` (2026-12-25 on both FX
        # pairs) used to be recorded as covered through that date, so `missing_tail` never fired
        # again and the series sat on 2026-09-01 for 46 days while every USD/GBP vehicle was
        # converted with it.
        covered_end = min(end, date.today().isoformat())
        fetched_through = max([x for x in (_clamp_through(prior.get("fetched_through")), covered_end)
                               if x] or [covered_end])
        meta_rows.append({"ticker": t, "currency": ccy, "fetched_from": fetched_from,
                          "fetched_through": fetched_through, "fetched_at": fetched_at,
                          "n_rows": int(len(merged))})
        summary[t] = {"rows": int(len(merged)), "fetched": True, "currency": ccy}

    for row in meta_rows:
        lake.append_partition(_META, pd.DataFrame([row]), {"ticker": row["ticker"]},
                              overwrite=True, lake_dir=lake_dir)
    return summary


def read(tickers: list[str], start: str, end: str, allow_fetch: bool = True,
         fetch_fn=None, ccy_fn=None, lake_dir: Path | None = None) -> pd.DataFrame:
    """THE `price_fn`: DataFrame[date × ticker] of native adjusted closes over [start, end].

    Refreshes the cache first when the window reaches past what is cached (unless offline or
    `allow_fetch=False`). Drop-in for `nav_engine.yfinance_prices` / `dislocation.yfinance_prices`.
    """
    tickers = [t for t in dict.fromkeys(tickers) if t]
    if not tickers:
        return pd.DataFrame()
    if allow_fetch and not _offline():
        refresh(tickers, start, end, fetch_fn=fetch_fn, ccy_fn=ccy_fn, lake_dir=lake_dir)
    frames = [_read_ticker(t, lake_dir) for t in tickers]
    long_df = pd.concat([f for f in frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in frames) else pd.DataFrame()
    return wide_frame(long_df, tickers, start, end)


def price_fn(tickers: list[str], start: str, end: str):
    """Bare `price_fn(tickers, start, end)` for injection into the scorers/nav engine."""
    return read(tickers, start, end)


# ── native → EUR (v10.4) ─────────────────────────────────────────────────────
#
# WHY THIS LIVES HERE. The book is denominated in EUR and `nav_engine` has converted P&L
# since day one — but the SIGNAL path never did: `momentum_engine`, `calibration`'s forward
# returns, `covariance` and `comomentum` all read native closes. 15 of the 26 scored vehicles
# do not quote in euros (10 USD, 4 GBp, 1 GBP), so the composite ranked 26 sectors measured in
# four currencies, and the IC asked whether that ranking predicts a return the operator does
# not earn. Within ONE series the scale cancels in a return; between series it does not.
#
# The conversion itself is not new — this is `nav_engine._to_eur` moved to the layer that
# already owns both the cache and the per-ticker currency, so there is ONE implementation of
# the GBp×0.01 rule instead of two. `nav_engine` keeps its names as thin delegates, so the
# injectable ccy_fn/fx_fn contract every NAV test relies on is unchanged.

def to_eur(frame, ccy_map: dict, fx_by_ccy: dict):
    """Convert each column of a price frame from its listing currency to EUR.

    A currency with no FX series (couldn't be fetched) is left NATIVE rather than dropped —
    losing the column would silently shrink the cross-section a ranking is computed over,
    which is worse than one row on a stale basis. Callers that care read `basis_by_ticker`.
    """
    if frame is None or len(frame) == 0:
        return frame
    out = frame.copy()
    for t in list(getattr(out, "columns", [])):
        ccy = (ccy_map.get(t) or "EUR")
        pence = ccy in ("GBp", "GBX")
        base = "GBP" if pence else ccy
        col = out[t]
        if base != "EUR":
            fx = fx_by_ccy.get(base)
            if fx is None:
                continue
            # reindex ONTO the frame's own dates: a union index would add days on which FX
            # traded and the instrument did not, so "N rows back" would span a different
            # calendar window and manufacture a return difference that is pure calendar.
            col = col * fx.reindex(out.index).ffill().bfill()
        if pence:
            col = col * 0.01
        out[t] = col
    return out


def fx_series(currencies_needed, start: str, end: str, allow_fetch: bool = True) -> dict:
    """{base_ccy: Series of EUR per 1 unit}, served from this same cache.

    EURUSD=X quotes USD per EUR, so EUR-per-USD = 1/EURUSD; likewise 1/EURGBP for GBP.
    Pence (GBp/GBX) reuse the GBP series and are scaled x0.01 at conversion time.
    """
    pair_for = {"USD": "EURUSD=X", "GBP": "EURGBP=X"}
    out: dict = {}
    for ccy in currencies_needed:
        pair = pair_for.get(ccy)
        if not pair:
            continue
        px = read([pair], start, end, allow_fetch=allow_fetch)
        if px is None or len(px) == 0 or pair not in getattr(px, "columns", []):
            continue
        col = px[pair].dropna()
        if col.empty:
            continue
        out[ccy] = 1.0 / col
    return out


def basis_by_ticker(tickers: list[str], fx_by_ccy: dict, ccy_map: dict | None = None) -> dict:
    """{ticker: "EUR" | "<native>"} — what basis each column ACTUALLY ended up on.

    A row must declare its basis: an unconverted column (FX unavailable) is not EUR, and a
    ranking that mixes the two without saying so is the defect this whole change is about.
    """
    ccy_map = ccy_map if ccy_map is not None else currencies(list(tickers))
    out = {}
    for t in tickers:
        ccy = (ccy_map.get(t) or "EUR")
        base = "GBP" if ccy in ("GBp", "GBX") else ccy
        out[t] = "EUR" if (base == "EUR" or base in fx_by_ccy) else ccy
    return out


def read_eur(tickers: list[str], start: str, end: str, allow_fetch: bool = True,
             lake_dir: Path | None = None):
    """`read()` in EUR — the price_fn for anything that RANKS or MEASURES across vehicles.

    Returns the frame only. Use `read_eur_with_basis` when the caller must record which
    columns actually converted.
    """
    return read_eur_with_basis(tickers, start, end, allow_fetch, lake_dir)[0]


def read_eur_with_basis(tickers: list[str], start: str, end: str, allow_fetch: bool = True,
                        lake_dir: Path | None = None):
    """(frame_in_eur, {ticker: basis}) — see `read_eur`."""
    frame = read(tickers, start, end, allow_fetch=allow_fetch, lake_dir=lake_dir)
    if frame is None or len(frame) == 0:
        return frame, {}
    cols = list(frame.columns)
    ccy_map = currencies(cols, lake_dir=lake_dir)
    needed = {("GBP" if c in ("GBp", "GBX") else c) for c in ccy_map.values() if c} - {"EUR"}
    fx = fx_series(needed, start, end, allow_fetch=allow_fetch) if needed else {}
    return to_eur(frame, ccy_map, fx), basis_by_ticker(cols, fx, ccy_map)


def eur_price_fn(tickers: list[str], start: str, end: str):
    """Bare EUR `price_fn(tickers, start, end)` for injection into the scorers."""
    return read_eur(tickers, start, end)


def currencies(tickers: list[str], ccy_fn=None, lake_dir: Path | None = None,
               default: str | None = "EUR") -> dict[str, str | None]:
    """{ticker: listing currency} from the cache — the `ccy_fn` contract in `nav_engine`.

    Before this, every NAV/exit run called `yf.Ticker(t).fast_info` per ticker (one network
    round-trip EACH) to learn a value that never changes.

    `default` is what an UNREADABLE currency becomes. "EUR" is right for `to_eur`, whose
    question is "convert or not" and for which not-knowing and no-conversion-needed are the
    same action. It is wrong for a DIAGNOSTIC column: `market_data` publishes the quote
    currency so a GBp line cannot pass as EUR, and there a default is the defect itself —
    v8.1's `hist.attrs.get("currency", "USD")` said "USD" by default rather than by reading
    for all 26 rows. Those callers pass `default=None`, and "not measured" stays None.
    """
    meta = _read_meta(lake_dir)
    known = {r["ticker"]: r.get("currency") for _, r in meta.iterrows()} if not meta.empty else {}
    out = {t: known.get(t) for t in tickers}
    missing = [t for t, v in out.items() if not v]
    if missing and not _offline():
        fresh = (ccy_fn or yfinance_currencies)(missing)
        for t, ccy in fresh.items():
            out[t] = ccy
            row = _meta_row(meta, t) or {"ticker": t, "fetched_from": None,
                                         "fetched_through": None, "n_rows": 0}
            row.update({"ticker": t, "currency": ccy,
                        "fetched_at": datetime.now(timezone.utc).isoformat()})
            lake.append_partition(_META, pd.DataFrame([row]), {"ticker": t},
                                  overwrite=True, lake_dir=lake_dir)
    return {t: (v or default) for t, v in out.items()}


def coverage(lake_dir: Path | None = None) -> list[dict]:
    """What the cache holds per ticker — the audit view behind the CLI."""
    meta = _read_meta(lake_dir)
    if meta.empty:
        return []
    return meta.sort_values("ticker").to_dict(orient="records")


def universe_tickers() -> list[str]:
    """Every ticker the pipeline prices: ETF universe + benchmarks + FX pairs.

    Reading the universe from `etf_universe.yaml` keeps ONE list — a sector added there is
    priced by the next refresh with no code change.
    """
    import yaml

    path = Path(__file__).parents[2] / "catalyx" / "config" / "etf_universe.yaml"
    tickers: list[str] = []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    sectors = data.get("etf_universe", data)
    if isinstance(sectors, dict):
        for entry in sectors.values():
            # v2.0 shape: {sector_id: [ {ticker: …}, … ]}. A dict wrapper ({etfs: [...]}) is
            # tolerated so a future regrouping does not silently empty the cache — the failure
            # mode of guessing wrong here is invisible (you get benchmarks only, no error).
            etfs = entry if isinstance(entry, list) else (entry.get("etfs") or []) \
                if isinstance(entry, dict) else []
            for e in etfs:
                tk = (e or {}).get("ticker") if isinstance(e, dict) else None
                if tk:
                    tickers.append(str(tk))
    return list(dict.fromkeys(tickers + list(BENCHMARK_TICKERS) + list(MACRO_TICKERS)
                                    + list(FX_TICKERS)))


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX shared price cache (one fetch per run)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refresh", help="Fetch missing price history into the lake")
    r.add_argument("--tickers", default=None, help="Comma-separated. Default: the whole universe.")
    r.add_argument("--days", type=int, default=DEFAULT_HISTORY_DAYS)
    r.add_argument("--as-of", default=None, help="End date (YYYY-MM-DD). Default: today.")
    r.add_argument("--force", action="store_true", help="Refetch even if coverage looks complete")

    s = sub.add_parser("show", help="Show cached closes for one ticker")
    s.add_argument("ticker")
    s.add_argument("--limit", type=int, default=10)

    sub.add_parser("coverage", help="What the cache holds per ticker")

    args = p.parse_args()

    if args.cmd == "refresh":
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers \
            else universe_tickers()
        end = args.as_of or date.today().isoformat()
        start = (date.fromisoformat(end) - timedelta(days=args.days)).isoformat()
        out = refresh(tickers, start, end, force=args.force)
        fetched = [t for t, v in out.items() if v.get("fetched")]
        cached = [t for t, v in out.items() if not v.get("fetched")]
        print(f"  price cache → {end}  ({len(tickers)} tickers)")
        print(f"    fetched : {len(fetched)}" + (f"  {', '.join(fetched[:12])}" +
              ("…" if len(fetched) > 12 else "") if fetched else ""))
        print(f"    cached  : {len(cached)} (already covered)")
        empty = [t for t, v in out.items() if v.get("fetched") and not v.get("rows")]
        if empty:
            print(f"    ⚠ no data returned for: {', '.join(empty)}")
    elif args.cmd == "show":
        df = _read_ticker(args.ticker)
        if df.empty:
            print(f"  (not cached: {args.ticker}) — run `refresh --tickers {args.ticker}`")
            return
        print(f"  {args.ticker}: {len(df)} rows  {df['date'].min()} → {df['date'].max()}")
        print(df.tail(args.limit).to_string(index=False))
    elif args.cmd == "coverage":
        rows = coverage()
        if not rows:
            print("  (cache empty) — run `uv run python -m catalyx.data.prices refresh`")
            return
        print(f"  {'ticker':<12}{'ccy':<6}{'from':<12}{'through':<12}{'rows':>6}")
        for r_ in rows:
            print(f"  {str(r_['ticker']):<12}{str(r_.get('currency') or '?'):<6}"
                  f"{str(r_.get('fetched_from') or '?'):<12}{str(r_.get('fetched_through') or '?'):<12}"
                  f"{int(r_.get('n_rows') or 0):>6}")


if __name__ == "__main__":
    main()
