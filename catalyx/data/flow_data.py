"""ETF flow data module — shares_outstanding × NAV + institutional ownership.

Per CLAUDE.md: "Use shares_outstanding × NAV, NOT total AUM. AUM conflates price
appreciation with net flows."

Two data sources in priority order:

1. iShares API (for iShares-branded ETFs only) — returns exact shares_outstanding and NAV.
   Endpoint: https://www.ishares.com/us/products/{ticker}/fund.jsonp (US)
             https://www.ishares.com/uk/... (EU UCITS)
   Status: **stubbed** — requires per-fund URL mapping not yet built.

2. yfinance — primary source for Phase 0.5.
   `Ticker.info['sharesOutstanding']` × close_price ≈ implied AUM proxy.
   Change in shares_outstanding week-over-week = net ETF creation/redemption (true flow).

Flow confirmation score [0–100]:
   50 = neutral (no significant change in shares outstanding)
   > 50 = net inflow (shares created)
   < 50 = net outflow (shares redeemed)

Institutional ownership (US-listed ETFs only):
   Source: SEC EDGAR 13F full-text search via CUSIP (quarterly filings, ~45-day lag).
   CUSIP derived from yfinance ISIN (US ISINs: CUSIP = ISIN[2:11]).
   UCITS ETFs (LSE, XETRA, Euronext, SIX) have no 13F equivalent → null.

   inst_13f_filer_count: number of distinct 13F-HR filings mentioning the CUSIP in
   the last 9 months. Proxy for breadth of institutional ownership.

   inst_sponsorship_score [0–100]: inverted-U over filer breadth count.
   Anchors calibrated to observed range in this universe (0–4000+ filers):
     Pre-institutional (<100) → low score (pre-discovery)
     Building conviction (500–1500) → peak score (thesis validated, not yet crowded)
     Overcrowded (3000+) → lower score (exit risk)

Output: `data/snapshots/flow_snapshot_YYYYMMDD.json`

Usage:
    uv run python -m catalyx.data.flow_data
    uv run python -m catalyx.data.flow_data --tickers COPX IQQH.DE GDX
    uv run python -m catalyx.data.flow_data --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf

_REPO_ROOT = Path(__file__).parents[2]
_SNAPSHOTS_DIR = _REPO_ROOT / "data" / "snapshots"

# Mirrors market_data.py SECTOR_TICKERS — primary ETF per sector.
# Only the tier-1 ETF is used for flow data (most liquid → cleanest flow signal).
SECTOR_TICKERS: dict[str, str] = {
    "grid_infrastructure_utilities":  "IQQH.DE",
    "copper_miners":                  "COPX",
    "eu_defense_prime_contractors":   "EUDF.L",
    "ai_infrastructure_data_centers": "SRVR",
    "semiconductors_design":          "SEMI.L",
    "gold_physical":                  "IGLN.L",
    "gold_miners":                    "GDX",
    "nuclear_energy":                 "NLR",
    "uranium_miners":                 "URNM",
    "silver_physical":                "PHAG.L",
    "us_defense_prime_contractors":   "ITA",
    "cybersecurity_defense":          "CIBR",
    "rare_earth_miners":              "REMX",
    "lithium_miners":                 "LIT",
    "solar_energy":                   "TAN",
    "wind_energy_offshore":           "WNDY",
    "silver_miners":                  "SIL",
}

# iShares fund IDs for the iShares API (stubbed — add as needed).
_ISHARES_FUND_IDS: dict[str, str] = {}

# European exchange suffixes → UCITS ETFs, no SEC 13F filing.
_UCITS_EXCHANGE_SUFFIXES = {".L", ".DE", ".PA", ".SW", ".AS", ".MI", ".MC", ".CO", ".ST"}

# EDGAR EFTS 13F search window: 9 months rolling.
_EDGAR_LOOKBACK_MONTHS = 9
# Per-request delay to respect EDGAR fair-access policy.
# 0.35s observed reliable; shorter causes intermittent 429/timeouts under load.
_EDGAR_REQUEST_DELAY_S = 0.35
_EDGAR_MAX_RETRIES = 2
# Contact info required by SEC EDGAR User-Agent policy.
_EDGAR_USER_AGENT = "catalyx-research/0.1 abetatos@gmail.com"


def _is_ucits(ticker: str) -> bool:
    upper = ticker.upper()
    return any(upper.endswith(s.upper()) for s in _UCITS_EXCHANGE_SUFFIXES)


# ── iShares API (stubbed) ──────────────────────────────────────────────────────

def _fetch_ishares(ticker: str) -> dict | None:
    if ticker not in _ISHARES_FUND_IDS:
        return None
    # TODO: implement iShares API call
    return None


# ── yfinance flow data ─────────────────────────────────────────────────────────

def _fetch_yfinance(ticker: str, lookback_days: int = 7) -> dict | None:
    """Fetch shares_outstanding delta over the last N days from yfinance."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
    except Exception as exc:
        return {"ticker": ticker, "error": str(exc)}

    shares_now = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
    nav_price = info.get("navPrice") or info.get("previousClose") or info.get("regularMarketPrice")
    total_assets = info.get("totalAssets")

    if shares_now is None and total_assets and nav_price:
        shares_now = int(total_assets / nav_price)
        shares_source = "derived_from_total_assets"
    else:
        shares_source = "yfinance_direct"

    if nav_price is None:
        return {
            "ticker": ticker,
            "error": "No price data available from yfinance",
            "total_assets_m_usd": round(total_assets / 1e6, 1) if total_assets else None,
            "fetched_at": date.today().isoformat(),
        }

    implied_aum = (shares_now * nav_price) if shares_now else total_assets or 0.0

    prior = _load_prior_snapshot(ticker, lookback_days)
    shares_delta = None
    flow_usd = None
    if prior and shares_now:
        shares_prior = prior.get("shares_outstanding")
        price_prior = prior.get("nav_price")
        if shares_prior and price_prior:
            shares_delta = shares_now - shares_prior
            flow_usd = shares_delta * nav_price

    return {
        "ticker": ticker,
        "shares_outstanding": shares_now,
        "shares_source": shares_source,
        "nav_price": round(nav_price, 4),
        "implied_aum_m_usd": round(implied_aum / 1e6, 1) if implied_aum else None,
        "shares_delta_1w": shares_delta,
        "flow_usd_1w": round(flow_usd, 0) if flow_usd is not None else None,
        "flow_pct_1w": round(
            flow_usd / (implied_aum - flow_usd) * 100, 3
        ) if flow_usd and (implied_aum - flow_usd) > 0 else None,
        "source": "yfinance",
        "fetched_at": date.today().isoformat(),
    }


def _load_prior_from_lake(ticker: str, lookback_days: int) -> dict | None:
    """Most recent prior flow row for `ticker` from the parquet lake (Tier 2 truth)."""
    try:
        import pandas as pd
        from catalyx.store import lake
    except Exception:
        return None
    df = lake.read_table("flow")
    if df.empty or "ticker" not in df.columns:
        return None
    cutoff = date.today() - timedelta(days=lookback_days)
    sub = df[df["ticker"] == ticker].copy()
    if sub.empty:
        return None

    def _d(s):
        try:
            return date.fromisoformat(str(s))
        except ValueError:
            return None

    sub["_d"] = sub["date"].map(_d)
    sub = sub[sub["_d"].notna() & (sub["_d"] > cutoff)]
    if sub.empty:
        return None
    row = sub.sort_values("_d").iloc[-1]

    def _clean(v):
        return None if v is None or (isinstance(v, float) and pd.isna(v)) else v

    return {
        "shares_outstanding": _clean(row.get("shares_outstanding")),
        "nav_price": _clean(row.get("nav_price")),
    }


def _load_prior_snapshot(ticker: str, lookback_days: int) -> dict | None:
    """Load the most recent prior flow data within the lookback window.

    Parquet-first: try the lake, then fall back to the legacy snapshot JSON files
    (kept during migration). Returns a dict with shares_outstanding + nav_price.
    """
    from_lake = _load_prior_from_lake(ticker, lookback_days)
    if from_lake and from_lake.get("shares_outstanding") and from_lake.get("nav_price"):
        return from_lake

    cutoff = date.today() - timedelta(days=lookback_days)
    candidates = sorted(_SNAPSHOTS_DIR.glob("flow_snapshot_*.json"), reverse=True)
    for f in candidates:
        try:
            snap_date = date.fromisoformat(f.stem.replace("flow_snapshot_", ""))
        except ValueError:
            continue
        if snap_date <= cutoff:
            continue
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
            etf_data = snap.get("etfs", {})
            if ticker in etf_data:
                return etf_data[ticker]
        except Exception:
            continue
    return None


# ── Institutional ownership via SEC EDGAR 13F ─────────────────────────────────

def _cusip_from_yfinance(ticker: str) -> str | None:
    """Derive CUSIP from yfinance ISIN. US ISIN = 'US' + 9-char CUSIP + check digit."""
    try:
        isin = yf.Ticker(ticker).isin
        if isin and isin.startswith("US") and len(isin) >= 12:
            return isin[2:11]
    except Exception:
        pass
    return None


def _edgar_13f_count(cusip: str) -> int | None:
    """Query SEC EDGAR EFTS for 13F-HR filings mentioning this CUSIP.

    Returns filing count over the last _EDGAR_LOOKBACK_MONTHS months.
    Each unique filing ≈ one institutional holder reporting this position.
    Requires no API key. Rate-limited to respect SEC fair-access policy.
    """
    today = date.today()
    start = (today.replace(day=1) - timedelta(days=_EDGAR_LOOKBACK_MONTHS * 30)).isoformat()
    url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q=%22{cusip}%22&forms=13F-HR"
        f"&dateRange=custom&startdt={start}&enddt={today.isoformat()}"
    )
    for attempt in range(_EDGAR_MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _EDGAR_USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            return data.get("hits", {}).get("total", {}).get("value", 0)
        except OSError:
            if attempt < _EDGAR_MAX_RETRIES:
                time.sleep(_EDGAR_REQUEST_DELAY_S * (2 ** attempt))
    return None


def _fetch_institutional_ownership(ticker: str) -> dict:
    """Fetch institutional ownership breadth from SEC EDGAR 13F (US ETFs only).

    Pipeline: yfinance ISIN → CUSIP → EDGAR EFTS 13F search → filer count.
    UCITS ETFs return null (no 13F equivalent in EU).
    """
    today = date.today().isoformat()

    if _is_ucits(ticker):
        return {
            "inst_cusip": None,
            "inst_13f_filer_count": None,
            "inst_sponsorship_score": None,
            "inst_source": "not_available_ucits",
            "inst_fetched_at": today,
        }

    cusip = _cusip_from_yfinance(ticker)
    if cusip is None:
        return {
            "inst_cusip": None,
            "inst_13f_filer_count": None,
            "inst_sponsorship_score": None,
            "inst_source": "error_no_cusip",
            "inst_fetched_at": today,
        }

    time.sleep(_EDGAR_REQUEST_DELAY_S)
    count = _edgar_13f_count(cusip)

    if count is None:
        return {
            "inst_cusip": cusip,
            "inst_13f_filer_count": None,
            "inst_sponsorship_score": None,
            "inst_source": "error_edgar",
            "inst_fetched_at": today,
        }

    return {
        "inst_cusip": cusip,
        "inst_13f_filer_count": count,
        "inst_sponsorship_score": _inst_sponsorship_score(count),
        "inst_source": "edgar_13f",
        "inst_fetched_at": today,
    }


# ── Scoring functions ──────────────────────────────────────────────────────────

def _flow_score(flow_pct: float | None) -> float:
    """Convert a week-over-week flow % into a [0, 100] flow_confirmation score.

    Anchors:
      +5% AUM inflow  → 90 (strong inflow)
       0% (neutral)   → 50
      -5% AUM outflow → 10 (strong outflow)
    """
    if flow_pct is None:
        return 50.0
    raw = 50.0 + flow_pct * 8.0
    return round(max(10.0, min(90.0, raw)), 1)


def _inst_sponsorship_score(filer_count: int | None) -> float | None:
    """Convert 13F filer breadth count to [0–100] inst_sponsorship_score.

    Inverted-U: peaks at ~1000–1500 filers (institutional conviction building,
    trade not yet overcrowded). Calibrated to this universe's observed range.

    Anchored breakpoints:
       0     → 22  (no institutional presence — pre-discovery)
      100    → 40  (early institutional interest)
      500    → 68  (growing conviction, strong confirmation)
     1200    → 82  (peak: well-validated, crowding risk moderate)
     2000    → 70  (well-established, crowding building)
     3000    → 50  (crowded — large-cap ETF saturation)
     4500+   → 35  (very crowded, exit risk elevated)

    Returns None when count is unavailable (UCITS or fetch error).
    """
    if filer_count is None:
        return None

    breakpoints = [
        (0,    22.0),
        (100,  40.0),
        (500,  68.0),
        (1200, 82.0),
        (2000, 70.0),
        (3000, 50.0),
        (4500, 35.0),
    ]

    c = max(0, filer_count)
    for i in range(len(breakpoints) - 1):
        x0, y0 = breakpoints[i]
        x1, y1 = breakpoints[i + 1]
        if x0 <= c <= x1:
            t = (c - x0) / (x1 - x0)
            return round(y0 + t * (y1 - y0), 1)
    return breakpoints[-1][1]


# ── Core ──────────────────────────────────────────────────────────────────────

def fetch_flow_data(
    sector_tickers: dict[str, str] | None = None,
) -> dict:
    """Fetch flow data + institutional ownership breadth for all sectors.

    Args:
        sector_tickers: {sector_id: ticker}. Defaults to SECTOR_TICKERS.

    Returns:
        Snapshot dict with per-sector flow data, inst sponsorship scores, and raw data.
    """
    tickers = sector_tickers or SECTOR_TICKERS
    today = date.today().isoformat()
    etf_results: dict[str, dict] = {}
    sector_scores: dict[str, dict] = {}

    for sector_id, ticker in tickers.items():
        result = _fetch_ishares(ticker) or _fetch_yfinance(ticker)
        inst = _fetch_institutional_ownership(ticker)

        if result:
            etf_results[ticker] = {**result, **inst}

        flow_pct = result.get("flow_pct_1w") if result else None
        error = result.get("error") if result else "fetch failed"

        sector_scores[sector_id] = {
            "ticker": ticker,
            "flow_confirmation": _flow_score(flow_pct),
            "flow_pct_1w": flow_pct,
            "implied_aum_m_usd": result.get("implied_aum_m_usd") if result else None,
            "data_quality": "estimated" if flow_pct is None else "computed",
            "error": error if flow_pct is None else None,
            "inst_sponsorship_score": inst.get("inst_sponsorship_score"),
            "inst_13f_filer_count": inst.get("inst_13f_filer_count"),
            "inst_source": inst.get("inst_source"),
        }

    return {
        "generated_at": today,
        "date": today,
        "source": "yfinance + edgar_13f",
        "note": (
            "flow_pct_1w requires a prior snapshot. "
            "inst_sponsorship_score from EDGAR 13F CUSIP search (~9-month window, ~45-day lag). "
            "UCITS ETFs: inst_source=not_available_ucits."
        ),
        "sector_scores": sector_scores,
        "etfs": etf_results,
    }


def flow_to_rows(snapshot: dict) -> list[dict]:
    """Flatten a flow snapshot into one row per sector (= its primary ETF), merging the
    derived `sector_scores` with the raw `etfs` data (shares_outstanding, nav_price).
    This is the tabular shape the parquet lake stores."""
    etfs = snapshot.get("etfs", {})
    meta = {
        "date": snapshot.get("date"),
        "generated_at": snapshot.get("generated_at"),
        "source": snapshot.get("source", "yfinance + edgar_13f"),
    }
    rows: list[dict] = []
    for sid, s in snapshot.get("sector_scores", {}).items():
        ticker = s.get("ticker")
        raw = etfs.get(ticker, {})
        rows.append({
            **meta,
            "sector_id": sid,
            "ticker": ticker,
            "flow_confirmation": s.get("flow_confirmation"),
            "flow_pct_1w": s.get("flow_pct_1w"),
            "implied_aum_m_usd": s.get("implied_aum_m_usd"),
            "data_quality": s.get("data_quality"),
            "shares_outstanding": raw.get("shares_outstanding"),
            "nav_price": raw.get("nav_price"),
            "shares_delta_1w": raw.get("shares_delta_1w"),
            "flow_usd_1w": raw.get("flow_usd_1w"),
            "inst_sponsorship_score": s.get("inst_sponsorship_score"),
            "inst_13f_filer_count": s.get("inst_13f_filer_count"),
            "inst_source": s.get("inst_source"),
        })
    return rows


def _write_lake_partition(snapshot: dict) -> None:
    """Dual-write the flow snapshot to the parquet lake (Tier 2 source of truth).
    Best-effort during migration: never let a parquet failure break the JSON pipeline."""
    rows = flow_to_rows(snapshot)
    if not rows or not snapshot.get("date"):
        return
    try:
        import pandas as pd
        from catalyx.store import lake

        df = pd.DataFrame(rows)
        lake.append_partition("flow", df, {"date": snapshot["date"]}, overwrite=True)
        print(f"lake: flow partition date={snapshot['date']} ({len(df)} rows)", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"WARN lake write skipped: {e}", file=sys.stderr)


def backfill_lake() -> int:
    """Convert existing data/snapshots/flow_snapshot_*.json into lake partitions."""
    n = 0
    for p in sorted(_SNAPSHOTS_DIR.glob("flow_snapshot_*.json")):
        snap = json.loads(p.read_text(encoding="utf-8"))
        snap.setdefault("date", p.stem.replace("flow_snapshot_", ""))
        _write_lake_partition(snap)
        n += 1
    return n


def write_snapshot(snapshot: dict) -> Path:
    """Write flow snapshot to data/snapshots/flow_snapshot_YYYYMMDD.json + dual-write lake."""
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _SNAPSHOTS_DIR / f"flow_snapshot_{snapshot['date']}.json"
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_lake_partition(snapshot)
    return path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    """CLI entry point for flow + institutional ownership snapshot."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CATALYX flow data — ETF shares_outstanding × NAV + EDGAR 13F institutional breadth"
    )
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="Specific tickers to fetch.")
    parser.add_argument("--write", action="store_true",
                        help="Write snapshot to data/snapshots/flow_snapshot_YYYYMMDD.json.")
    parser.add_argument("--json", action="store_true", help="Output raw JSON only.")
    parser.add_argument("--backfill-lake", action="store_true",
                        help="Convert existing flow_snapshot_*.json into lake partitions and exit.")
    args = parser.parse_args()

    if args.backfill_lake:
        n = backfill_lake()
        print(f"Backfilled {n} flow snapshot(s) into the lake", file=sys.stderr)
        return

    sector_tickers = None
    if args.tickers:
        sector_tickers = {t: t for t in args.tickers}

    print("Fetching ETF flow + institutional ownership (EDGAR 13F)...", file=sys.stderr)
    snapshot = fetch_flow_data(sector_tickers=sector_tickers)

    if args.write:
        path = write_snapshot(snapshot)
        print(f"Written: {path}", file=sys.stderr)

    if args.json:
        print(json.dumps(snapshot, indent=2, ensure_ascii=False))
        return

    print(f"\nCATALYX — ETF Flow + Institutional Sponsorship  [{snapshot['date']}]\n")
    print(
        f"  {'sector_id':<42} {'ticker':<10} {'flow_conf':>9}  "
        f"{'inst_score':>10}  {'13f_filers':>10}  {'aum_m':>8}"
    )
    print(f"  {'-'*42} {'-'*10} {'-'*9}  {'-'*10}  {'-'*10}  {'-'*8}")

    for sid, s in sorted(
        snapshot["sector_scores"].items(),
        key=lambda x: x[1].get("inst_sponsorship_score") or -1,
        reverse=True,
    ):
        inst_score = s.get("inst_sponsorship_score")
        filer_count = s.get("inst_13f_filer_count")
        inst_src = s.get("inst_source", "")
        if inst_score is not None:
            inst_str = f"{inst_score:>10.1f}"
        elif inst_src == "not_available_ucits":
            inst_str = "  n/a(UCIT)"
        else:
            inst_str = f"  err({inst_src[:4]})" if inst_src else "       n/a"
        filer_str = f"{filer_count:>10d}" if filer_count is not None else "       n/a"
        aum_str = f"{s['implied_aum_m_usd']:>8.0f}" if s["implied_aum_m_usd"] else "     n/a"
        print(
            f"  {sid:<42} {s['ticker']:<10} {s['flow_confirmation']:>9.1f}  "
            f"{inst_str}  {filer_str}  {aum_str}"
        )

    print(f"\n  {snapshot['note']}")


if __name__ == "__main__":
    main()
