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
   Source: yfinance institutional_holders / major_holders (SEC 13F data, ~45-day lag).
   UCITS ETFs (LSE, XETRA, Euronext, SIX) have no 13F equivalent → null.

   inst_sponsorship_score [0–100]: inverted-U over % of float held by institutions.
   Peaks at ~55% ownership (strong validation, not yet crowded).
   Low at extremes: 0% = pre-institutional, >80% = crowded trade.

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
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf

_REPO_ROOT = Path(__file__).parents[2]
_SNAPSHOTS_DIR = _REPO_ROOT / "data" / "snapshots"
_MIN_SECTORS_FOR_PERCENTILE = 5

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
# Key = ticker, value = iShares product page ID.
# Example: IQQH.DE → "IQQH" on iShares EU platform.
_ISHARES_FUND_IDS: dict[str, str] = {
    # Populated as iShares API integration is built out.
    # "IQQH.DE": "IQQH",
    # "IGLN.L": "IGLN",
}

# European exchange suffixes → UCITS ETFs, no SEC 13F filing.
_UCITS_EXCHANGE_SUFFIXES = {".L", ".DE", ".PA", ".SW", ".AS", ".MI", ".MC", ".CO", ".ST"}


def _is_ucits(ticker: str) -> bool:
    upper = ticker.upper()
    return any(upper.endswith(suffix.upper()) for suffix in _UCITS_EXCHANGE_SUFFIXES)


# ── iShares API (stubbed) ──────────────────────────────────────────────────────

def _fetch_ishares(ticker: str) -> dict | None:
    """Fetch shares_outstanding and NAV from iShares API.

    Currently stubbed. Returns None until per-fund URL mapping is built.
    When implemented, should return:
        {"shares_outstanding": int, "nav": float, "source": "ishares_api"}
    """
    if ticker not in _ISHARES_FUND_IDS:
        return None
    # TODO: implement iShares API call
    # fund_id = _ISHARES_FUND_IDS[ticker]
    # url = f"https://www.ishares.com/uk/individual/en/products/{fund_id}/fund.jsonp?..."
    # ...
    return None


# ── yfinance flow data ─────────────────────────────────────────────────────────

def _fetch_yfinance(ticker: str, lookback_days: int = 7) -> dict | None:
    """Fetch shares_outstanding delta over the last N days from yfinance.

    yfinance ETF info contains:
      - info['sharesOutstanding']: current shares outstanding
      - info['previousClose']: last close price (≈ NAV for most ETFs)
      - info['totalAssets']: total AUM (price-appreciation-inclusive, not used for flow)

    Flow proxy: shares_outstanding_now - shares_outstanding_prior
    Prior period: compare to a snapshot saved lookback_days ago (if available),
                  otherwise use week-over-week change from history volume as proxy.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info
    except Exception as exc:
        return {"ticker": ticker, "error": str(exc)}

    shares_now = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
    nav_price = info.get("navPrice") or info.get("previousClose") or info.get("regularMarketPrice")
    total_assets = info.get("totalAssets")

    # Derive shares from totalAssets + price if not directly available
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

    # Week-over-week flow: requires a prior snapshot.
    # On first run, flow_pct_1w = None (no baseline to compare).
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
        "flow_pct_1w": round(flow_usd / (implied_aum - flow_usd) * 100, 3) if flow_usd and (implied_aum - flow_usd) > 0 else None,
        "source": "yfinance",
        "fetched_at": date.today().isoformat(),
    }


def _load_prior_snapshot(ticker: str, lookback_days: int) -> dict | None:
    """Load the most recent prior flow snapshot within the lookback window."""
    cutoff = date.today() - timedelta(days=lookback_days)
    candidates = sorted(_SNAPSHOTS_DIR.glob("flow_snapshot_*.json"), reverse=True)
    for f in candidates:
        try:
            snap_date = date.fromisoformat(f.stem.replace("flow_snapshot_", ""))
        except ValueError:
            continue
        if snap_date <= cutoff:
            continue  # too old
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
            etf_data = snap.get("etfs", {})
            if ticker in etf_data:
                return etf_data[ticker]
        except Exception:
            continue
    return None


# ── Institutional ownership ────────────────────────────────────────────────────

def _fetch_institutional_ownership(ticker: str) -> dict:
    """Fetch institutional ownership from yfinance (SEC 13F data, US-listed ETFs only).

    Returns null fields for UCITS ETFs — no 13F equivalent in EU.
    Data has ~45-day reporting lag (quarterly 13F filings).
    """
    if _is_ucits(ticker):
        return {
            "inst_pct_float": None,
            "inst_holders_top3": None,
            "inst_source": "not_available_ucits",
            "inst_fetched_at": date.today().isoformat(),
        }

    try:
        t = yf.Ticker(ticker)

        # % of float held by institutions
        pct_float: float | None = None
        try:
            major = t.major_holders
            if major is not None and not major.empty:
                for _, row in major.iterrows():
                    desc = str(row.iloc[1]).lower() if len(row) > 1 else ""
                    if "float" in desc and "institution" in desc:
                        raw_val = str(row.iloc[0]).replace("%", "").strip()
                        pct_float = float(raw_val)
                        # yfinance sometimes returns fraction (0.32) sometimes percent (32.0)
                        if pct_float > 1.0:
                            pct_float /= 100.0
                        break
        except Exception:
            pass

        # Top 3 institutional holders
        top3: list[dict] | None = None
        try:
            holders = t.institutional_holders
            if holders is not None and not holders.empty:
                top3 = []
                for _, row in holders.head(3).iterrows():
                    name = row.get("Holder", row.iloc[0] if len(row) > 0 else "Unknown")
                    pct_out = row.get("% Out", row.get("pctOut", None))
                    try:
                        pct_val = float(pct_out)
                        if pct_val > 1.0:
                            pct_val /= 100.0
                    except (TypeError, ValueError):
                        pct_val = None
                    top3.append({
                        "name": str(name),
                        "pct_out": round(pct_val * 100, 2) if pct_val is not None else None,
                    })
        except Exception:
            pass

        return {
            "inst_pct_float": round(pct_float, 4) if pct_float is not None else None,
            "inst_holders_top3": top3,
            "inst_source": "yfinance_13f",
            "inst_fetched_at": date.today().isoformat(),
        }

    except Exception as exc:
        return {
            "inst_pct_float": None,
            "inst_holders_top3": None,
            "inst_source": "error",
            "inst_error": str(exc),
            "inst_fetched_at": date.today().isoformat(),
        }


# ── Scoring functions ──────────────────────────────────────────────────────────

def _flow_score(flow_pct: float | None) -> float:
    """Convert a week-over-week flow % into a [0, 100] flow_confirmation score.

    Anchors:
      +5% AUM inflow  → 90 (strong inflow)
      +2% AUM inflow  → 70
      0% (neutral)    → 50
      -2% AUM outflow → 30
      -5% AUM outflow → 10 (strong outflow)

    Uses a sigmoid-like linear piecewise mapping capped at [10, 90].
    """
    if flow_pct is None:
        return 50.0  # neutral default
    # linear: score = 50 + flow_pct × 8 (i.e. +5% → 90, -5% → 10)
    raw = 50.0 + flow_pct * 8.0
    return round(max(10.0, min(90.0, raw)), 1)


def _inst_sponsorship_score(pct_float: float | None) -> float | None:
    """Convert % of float held by institutions to [0-100] sponsorship score.

    Inverted-U peaking at ~55% ownership: institutions have validated the thesis
    but the trade is not yet overcrowded.

    Calibrated breakpoints:
      0%   → 30 (no institutional interest — pre-discovery)
      15%  → 42 (early institutional interest)
      40%  → 72 (growing conviction, strong confirmation)
      55%  → 82 (peak: well-validated, crowding risk still low)
      70%  → 58 (well-owned, crowding risk building)
      80%  → 40 (crowded — risk of simultaneous exits)
      100% → 28 (extremely crowded)

    Returns None for UCITS ETFs (no data available).
    """
    if pct_float is None:
        return None

    breakpoints = [
        (0.00, 30.0),
        (0.15, 42.0),
        (0.40, 72.0),
        (0.55, 82.0),
        (0.70, 58.0),
        (0.80, 40.0),
        (1.00, 28.0),
    ]

    p = max(0.0, min(1.0, pct_float))
    for i in range(len(breakpoints) - 1):
        x0, y0 = breakpoints[i]
        x1, y1 = breakpoints[i + 1]
        if x0 <= p <= x1:
            t_interp = (p - x0) / (x1 - x0)
            return round(y0 + t_interp * (y1 - y0), 1)
    return breakpoints[-1][1]


# ── Core ──────────────────────────────────────────────────────────────────────

def fetch_flow_data(
    sector_tickers: dict[str, str] | None = None,
) -> dict:
    """Fetch flow data + institutional ownership for all sectors.

    Args:
        sector_tickers: {sector_id: ticker}. Defaults to SECTOR_TICKERS.

    Returns:
        Snapshot dict with per-sector flow data, inst ownership, and scores.
    """
    tickers = sector_tickers or SECTOR_TICKERS
    today = date.today().isoformat()
    etf_results: dict[str, dict] = {}
    sector_scores: dict[str, dict] = {}

    for sector_id, ticker in tickers.items():
        # Flow data
        result = _fetch_ishares(ticker) or _fetch_yfinance(ticker)
        # Institutional ownership (separate yfinance call — 13F data)
        inst = _fetch_institutional_ownership(ticker)

        if result:
            etf_results[ticker] = {**result, **inst}

        flow_pct = result.get("flow_pct_1w") if result else None
        error = result.get("error") if result else "fetch failed"
        inst_pct = inst.get("inst_pct_float")

        sector_scores[sector_id] = {
            "ticker": ticker,
            "flow_confirmation": _flow_score(flow_pct),
            "flow_pct_1w": flow_pct,
            "implied_aum_m_usd": result.get("implied_aum_m_usd") if result else None,
            "data_quality": "estimated" if flow_pct is None else "computed",
            "error": error if flow_pct is None else None,
            "inst_ownership_pct": round(inst_pct * 100, 1) if inst_pct is not None else None,
            "inst_sponsorship_score": _inst_sponsorship_score(inst_pct),
            "inst_source": inst.get("inst_source"),
            "inst_holders_top3": inst.get("inst_holders_top3"),
        }

    return {
        "generated_at": today,
        "date": today,
        "source": "yfinance",
        "note": "flow_pct_1w requires a prior snapshot to be meaningful. First run = estimated (50). inst_ownership from 13F (~45-day lag). UCITS ETFs: inst_source=not_available_ucits.",
        "sector_scores": sector_scores,
        "etfs": etf_results,
    }


def write_snapshot(snapshot: dict) -> Path:
    """Write flow snapshot to data/snapshots/flow_snapshot_YYYYMMDD.json."""
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _SNAPSHOTS_DIR / f"flow_snapshot_{snapshot['date']}.json"
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CATALYX flow data — ETF shares_outstanding × NAV + institutional ownership"
    )
    parser.add_argument(
        "--tickers", nargs="+", default=None,
        help="Specific tickers to fetch (overrides SECTOR_TICKERS list)."
    )
    parser.add_argument(
        "--write", action="store_true",
        help="Write snapshot to data/snapshots/flow_snapshot_YYYYMMDD.json."
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON only.")
    args = parser.parse_args()

    sector_tickers = None
    if args.tickers:
        sector_tickers = {t: t for t in args.tickers}

    print("Fetching ETF flow + institutional ownership data...", file=sys.stderr)
    snapshot = fetch_flow_data(sector_tickers=sector_tickers)

    if args.write:
        path = write_snapshot(snapshot)
        print(f"Written: {path}", file=sys.stderr)

    if args.json:
        print(json.dumps(snapshot, indent=2, ensure_ascii=False))
        return

    print(f"\nCATALYX — ETF Flow + Institutional Data  [{snapshot['date']}]\n")
    print(f"  {'sector_id':<45} {'flow_conf':>9}  {'flow_%':>8}  {'inst_spons':>10}  {'inst_%':>7}  {'aum_m':>9}")
    print(f"  {'-'*45} {'-'*9}  {'-'*8}  {'-'*10}  {'-'*7}  {'-'*9}")

    for sid, s in sorted(snapshot["sector_scores"].items(), key=lambda x: x[1].get("inst_sponsorship_score") or 0, reverse=True):
        flow_str = f"{s['flow_pct_1w']:+.2f}%" if s["flow_pct_1w"] is not None else "    n/a"
        inst_score = s.get("inst_sponsorship_score")
        inst_pct = s.get("inst_ownership_pct")
        inst_str = f"{inst_score:.1f}" if inst_score is not None else "n/a (UCITS)"
        inst_pct_str = f"{inst_pct:.1f}%" if inst_pct is not None else "n/a"
        aum_str = f"{s['implied_aum_m_usd']:.0f}" if s["implied_aum_m_usd"] else "n/a"
        print(
            f"  {sid:<45} {s['flow_confirmation']:>9.1f}  {flow_str:>8}  {inst_str:>10}  {inst_pct_str:>7}  {aum_str:>9}"
        )

    print(f"\n  Note: {snapshot['note']}")
    print("\n  Top institutional holders (US ETFs only):")
    for sid, s in snapshot["sector_scores"].items():
        top3 = s.get("inst_holders_top3")
        if top3:
            holders_str = ", ".join(
                f"{h['name']} ({h['pct_out']}%)" if h.get("pct_out") else h["name"]
                for h in top3
            )
            print(f"    {sid}: {holders_str}")


if __name__ == "__main__":
    main()
