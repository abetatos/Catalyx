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

import httpx
import yfinance as yf

_REPO_ROOT = Path(__file__).parents[2]
_SNAPSHOTS_DIR = _REPO_ROOT / "data" / "snapshots"

# ════════════════════════════════════════════════════════════════════════════════
# SECTOR_FLOW_TICKERS — the SINGLE SOURCE OF TRUTH for flow-signal coverage.
# ════════════════════════════════════════════════════════════════════════════════
#
# Each investable sector → an ORDERED FALLBACK CHAIN of ETF tickers. The fetcher walks
# the chain and uses the FIRST ticker that yields a usable flow signal; if none does it
# carries forward the last good reading, and only as a last resort falls back to a
# neutral 50. The goal (per the user): an APPROXIMATION beats a blind 50 — so every
# sector that has *any* representative ETF is covered here.
#
# ── ORDER CONVENTION (important — read before editing) ──────────────────────────
#   chain[0]      = the realistic TRADEABLE / primary vehicle for the sector (often the
#                   UCITS tier-1 in etf_universe.yaml). A flow computed from chain[0] is
#                   labelled `computed` (proxy_used = False).
#   chain[1:]     = SIGNAL fallbacks, ordered best-first. PREFER US-listed ETFs: yfinance
#                   reliably exposes `sharesOutstanding` for US funds (UCITS rarely do, so
#                   their creation/redemption — the actual flow — is invisible). A flow
#                   computed from a fallback is labelled `proxy_computed` and records
#                   `flow_proxy_ticker` so the dashboard shows exactly where it came from.
#
# ── WHY proxies are valid (and when they are NOT) ───────────────────────────────
#   For GLOBAL / FUNGIBLE themes (gold, silver, semis, copper, AI, …) the structural flow
#   into the THEME is vehicle-agnostic — a US sibling captures the same demand because the
#   underlying is the same (gold is gold; global semi demand shows up in SOXX). For
#   REGION-SPECIFIC themes (EU banks/defense/insurance) a US ETF would measure a different
#   investor base, so we either keep an EU-listed-but-US-traded fund (e.g. EUFN, which IS
#   US-listed → exposes shares, yet holds EU names) or leave the chain UCITS-only and
#   accept it may stay `estimated`. Such cases are flagged inline below.
#
# ── HOW TO ADD / CHANGE A SECTOR ────────────────────────────────────────────────
#   Add ONE line: "<sector_id>": ["<tradeable_primary>", "<us_fallback1>", "<us_fallback2>"].
#   Nothing else is needed — fetch, lake write, sector_snapshot, and the dashboard all read
#   this map. Keep this the ONLY place flow tickers live. Dead/renamed tickers are harmless
#   (a failed fetch just advances to the next in the chain), but prefer to prune them.
#
# Sectors with NO representative ETF at all (e.g. cobalt_nickel, semiconductors_foundry)
# use the closest thematic approximation available, noted inline; if truly nothing fits
# they are omitted and score a neutral 50 (still marked `estimated` in the dashboard).
SECTOR_FLOW_TICKERS: dict[str, list[str]] = {
    # ── DEFENSE / AEROSPACE ─────────────────────────────────────────────────────
    "eu_defense_prime_contractors":   ["EUDF.L", "DFEN.DE", "NATO.PA"],  # region-specific (EU) — no clean US proxy; all UCITS
    "us_defense_prime_contractors":   ["ITA", "XAR", "PPA"],
    "cybersecurity_defense":          ["CIBR", "BUG", "IHAK"],
    "cybersecurity_commercial":       ["CIBR", "BUG", "ISPY.L"],
    "space_defense_satellite":        ["ROKT", "ARKX", "UFO"],
    "space_commercial":               ["ROKT", "ARKX", "UFO"],
    "drone_autonomous_systems":       ["SHLD", "ROBO", "BOTZ"],          # no pure drone ETF — defense-tech/robotics approximation
    # ── ENERGY ──────────────────────────────────────────────────────────────────
    "oil_majors_integrated":          ["XLE", "VDE", "IUES.L"],
    "oil_services_equipment":         ["OIH", "XES", "PXJ"],
    "lng_natural_gas":                ["FCG", "UNG", "LNGA.L"],
    "nuclear_energy":                 ["NLR", "NUKZ", "NUKE.L"],
    "uranium_miners":                 ["URNM", "URA", "URNJ"],
    "solar_energy":                   ["TAN", "RAYS.L"],
    "wind_energy_offshore":           ["FAN", "WNDY", "ICLN"],           # FAN/WNDY expose no shares — ICLN (broad clean energy) approximation
    "grid_infrastructure_utilities":  ["IQQH.DE", "GRID", "XLU"],
    "hydrogen_clean_fuels":           ["HYDR", "HJEN", "HDRO"],
    # ── PRECIOUS METALS ─────────────────────────────────────────────────────────
    "gold_physical":                  ["IGLN.L", "GLD", "IAU"],         # UCITS primary → GLD/IAU (same underlying) for the signal
    "gold_miners":                    ["GDX", "GDXJ", "RING"],
    "silver_physical":                ["PHAG.L", "SLV", "SIVR"],
    "silver_miners":                  ["SIL", "SILJ", "SLVP"],
    "royalty_streaming_metals":       ["GDX", "SGDM"],                   # no pure royalty ETF — gold-miner basket approximation (holds WPM/RGLD)
    # ── INDUSTRIAL & BATTERY METALS ─────────────────────────────────────────────
    "copper_miners":                  ["COPX", "CPER", "COPA.L"],
    "lithium_miners":                 ["LIT", "BATT", "LITP"],
    "cobalt_nickel":                  ["BATT", "LIT", "REMX"],          # no pure ETF — battery-metals approximation
    "rare_earth_miners":              ["REMX", "MP"],                    # MP = MP Materials (single name, exposes shares) as fallback
    "steel_producers":                ["SLX", "XME"],
    "water_infrastructure":           ["PHO", "FIW", "WTRD.L"],
    "agriculture_soft_commodities":   ["DBA", "MOO", "VEGI"],
    # ── FINANCIALS ──────────────────────────────────────────────────────────────
    "eu_retail_banking":              ["EXV1.DE", "EUFN"],               # EUFN is US-listed (→ shares) but holds EU financials
    "us_retail_banking":              ["KBE", "KRE", "IAT"],
    "insurance_eu":                   ["EUFN", "KIE"],                   # EU financials proxy (EUFN); KIE is US insurance (weaker)
    "asset_management":               ["IAI"],                          # broker-dealers & asset managers
    "fintech_payments":               ["FINX", "IPAY", "ARKF"],
    "private_equity_listed":          ["PSP"],
    "crypto_infrastructure":          ["IBIT", "BITO", "BTCE.DE"],      # IBIT/BITO = large US BTC ETFs (expose shares)
    # ── TECHNOLOGY ──────────────────────────────────────────────────────────────
    "semiconductors_design":          ["SEMI.L", "SOXX", "SMH"],
    "semiconductors_equipment":       ["SMH", "SOXX"],                   # no pure equipment ETF — SMH/SOXX hold AMAT/LRCX/KLAC
    "semiconductors_foundry":         ["SMH", "SOXX"],                   # no pure foundry ETF — SMH is TSMC-heavy
    "semiconductors_memory":          ["SMH", "DRAM", "EWY"],
    "ai_infrastructure_data_centers": ["SRVR", "BOTZ", "WTAI"],
    "cloud_software_saas":            ["CLOU", "WCLD", "IGV"],
    "robotics_automation":            ["ROBO", "BOTZ", "IQQR.DE"],
    # ── HEALTHCARE ──────────────────────────────────────────────────────────────
    "biotech_drug_development":       ["IBB", "XBI"],
    "medical_devices":                ["IHI", "IHF"],
    "genomics_precision_medicine":    ["ARKG", "GNOM"],
    "pharma_large_cap":               ["IHE", "PPH", "XPH"],
    "longevity_biotech":              ["XBI", "ARKG", "IBB"],            # no pure longevity ETF — biotech/genomics approximation
    "synthetic_biology":              ["ARKG", "GNOM"],                  # no pure synbio ETF — genomics approximation
    # ── REAL ASSETS & CONSUMER ──────────────────────────────────────────────────
    "real_estate_logistics":          ["INDS", "REZ"],
    "real_estate_data_centers":       ["SRVR", "DTCR"],
    "infrastructure_core":            ["IFRA", "PAVE", "INFR.L"],
    "luxury_goods":                   ["LUXE.PA", "GLUX.SW"],           # no US pure-play luxury ETF — UCITS-only, may stay estimated
    "consumer_india_em":              ["INDA", "SMIN", "NDIA.L"],
}

# Back-compat alias: the primary (tradeable) ticker per sector = first of each chain.
SECTOR_TICKERS: dict[str, str] = {sid: chain[0] for sid, chain in SECTOR_FLOW_TICKERS.items()}

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


# ════════════════════════════════════════════════════════════════════════════════
# SHARES-OUTSTANDING SOURCES — the resilience cascade for the flow signal.
# ════════════════════════════════════════════════════════════════════════════════
#
# flow_pct only needs the SHARE COUNT over time (NAV cancels:
#   flow_pct = (shares_now / shares_prior − 1) × 100 ).
# So we just need a reliable shares-outstanding number per ticker. ORDER MATTERS — a delta
# subtracts two readings, so they must come from the SAME source or the cross-source gap
# (COPX: yfinance 89.1M vs stockanalysis 92.9M ≈ 4%) becomes phantom flow. Sources, in order:
#
#   1) stockanalysis.com  PRIMARY — broadest (all US tickers, even weekends) + most consistent,
#                 validated to ~0.25-1.75% vs iShares. UNOFFICIAL internal endpoint → it is THE
#                 fragile link: if it breaks we lose most coverage → it drives the warning.
#   2) iShares    official screener (one call, AUM/NAV same EOD timestamp → shares = AUM/NAV,
#                 clean). Covers iShares funds only.
#   3) yfinance   `sharesOutstanding` (direct only — never the totalAssets-derived path). LAST:
#                 least reliable (sparse on weekends; has returned plain-wrong counts, e.g. SOXX).
#   4) CMF        (in _volume_flow_estimate) — NOT shares-based; a price+volume proxy. Last
#                 resort, flagged DISTINCTLY because it diverges from true flow.
#
# `health` accumulates ok/err per source so the run can shout if a source (esp.
# stockanalysis) is DOWN and mark the flow as degraded/needs-review.

_SA_URL = "https://stockanalysis.com/api/symbol/e/{tk}/overview"
_ISHARES_SCREENER_URL = (
    "https://www.ishares.com/us/product-screener/product-screener-v3.1.jsn"
    "?dcrPath=/templatedata/config/product-screener-v3/data/en/us-ishares/"
    "ishares-product-screener-backend-config&siteEntryPassthrough=true"
)
_HTTP_HEADERS = {"User-Agent": "catalyx-research/0.1 abetatos@gmail.com"}
_ISHARES_SCREENER_CACHE: dict[str, float] | None = None  # ticker → shares (AUM/NAV)
# Politeness delay + one retry for stockanalysis — without it, ~70 rapid calls in a full
# snapshot get throttled and the later/fallback tickers fail (looked like coverage gaps).
_SA_REQUEST_DELAY_S = 0.4
_SA_MAX_RETRIES = 1

# Flow uses a FIXED TRAILING 7-DAY WINDOW (not "since last run") so it is smooth and comparable
# no matter how often you run — daily during an event or monthly otherwise. We pick the stored
# snapshot CLOSEST to 7 days ago (so a daily cadence compares today vs ~7d ago, summing a week
# of lumpy creations = smoothed; it does NOT compare against yesterday, which would be noisy),
# then normalise to exactly 7 days. Look back far enough that sparse cadences still find a prior.
_FLOW_LOOKBACK_DAYS = 90
_FLOW_TARGET_DAYS = 7


def new_health() -> dict:
    """Per-run source health accumulator: ok/err counts per shares source."""
    return {s: {"ok": 0, "err": 0} for s in ("yfinance", "stockanalysis", "ishares")}


def _parse_human_num(s) -> float | None:
    """'60.50M' / '$14.01B' / '362,500,000' → float. None if unparseable."""
    if s is None:
        return None
    t = str(s).replace("$", "").replace(",", "").strip()
    if not t:
        return None
    mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(t[-1].upper())
    if mult:
        t = t[:-1]
    try:
        return float(t) * (mult or 1.0)
    except ValueError:
        return None


def _shares_stockanalysis(ticker: str, health: dict) -> float | None:
    """Shares outstanding from stockanalysis.com (US tickers). Unofficial endpoint — rate-limited,
    so we pace + retry once; any persistent HTTP/parse failure increments
    health['stockanalysis']['err'] so the run can warn."""
    for attempt in range(_SA_MAX_RETRIES + 1):
        try:
            r = httpx.get(_SA_URL.format(tk=ticker), headers=_HTTP_HEADERS, timeout=12)
            if r.status_code == 200:
                v = _parse_human_num(r.json().get("data", {}).get("sharesOut"))
                time.sleep(_SA_REQUEST_DELAY_S)
                if v:
                    health["stockanalysis"]["ok"] += 1
                    return v
                return None  # 200 but no field = not covered, not an error
            # non-200 (often throttling) → back off and retry
            time.sleep(_SA_REQUEST_DELAY_S * (2 ** attempt))
        except Exception:  # noqa: BLE001
            time.sleep(_SA_REQUEST_DELAY_S * (2 ** attempt))
    health["stockanalysis"]["err"] += 1
    return None


def _ishares_screener(health: dict) -> dict[str, float]:
    """iShares US screener → {ticker: shares=AUM/NAV}. Fetched once per process (cached).
    NAV and AUM carry the same EOD timestamp, so the derived share count is clean."""
    global _ISHARES_SCREENER_CACHE
    if _ISHARES_SCREENER_CACHE is not None:
        return _ISHARES_SCREENER_CACHE
    out: dict[str, float] = {}
    try:
        d = httpx.get(_ISHARES_SCREENER_URL, headers=_HTTP_HEADERS, timeout=25,
                      follow_redirects=True).json()
        for f in d.values():
            tk = f.get("localExchangeTicker")
            nav = f.get("navAmount", {})
            aum = f.get("totalNetAssets", {})
            navr = nav.get("r") if isinstance(nav, dict) else None
            aumr = aum.get("r") if isinstance(aum, dict) else None
            if tk and navr and aumr:
                out[tk] = aumr / navr
        health["ishares"]["ok"] += 1 if out else 0
        if not out:
            health["ishares"]["err"] += 1
    except Exception:  # noqa: BLE001
        health["ishares"]["err"] += 1
    _ISHARES_SCREENER_CACHE = out
    return out


def _shares_ishares(ticker: str, health: dict) -> float | None:
    return _ishares_screener(health).get(ticker)


def _shares_multi(ticker: str, health: dict) -> tuple[float | None, str | None, float | None]:
    """Resolve shares outstanding via the cascade stockanalysis → iShares → yfinance.

    ORDER MATTERS: a flow delta subtracts two readings, so they must come from the SAME source
    or the cross-source gap (e.g. COPX: yfinance 89.1M vs stockanalysis 92.9M ≈ 4%) shows up as
    phantom flow. stockanalysis is primary because it is the broadest + most consistent (and
    validated ~1% vs iShares); yfinance is LAST because it is the least reliable (it has even
    returned plain-wrong counts, e.g. SOXX 7.65M vs the real ~68M). Returns (shares, source, nav).
    """
    # 1) stockanalysis (US-listed only — skip exchange-suffixed UCITS); the consistent primary
    if "." not in ticker:
        sa = _shares_stockanalysis(ticker, health)
        if sa is not None:
            return sa, "stockanalysis", None
    # 2) iShares screener (iShares funds only — official, AUM/NAV same-timestamp)
    ish = _shares_ishares(ticker, health)
    if ish is not None:
        return ish, "ishares", None
    # 3) yfinance — direct sharesOutstanding only; last resort (least reliable)
    try:
        info = yf.Ticker(ticker).info
        s = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        nav = info.get("navPrice") or info.get("previousClose") or info.get("regularMarketPrice")
        if s:
            health["yfinance"]["ok"] += 1
            return float(s), "yfinance", nav
    except Exception:  # noqa: BLE001
        health["yfinance"]["err"] += 1
    return None, None, None


# ── Per-ticker flow (trailing 7-day moving average of the daily flow rate) ────────

def _fetch_flow_for_ticker(ticker: str, health: dict,
                           lookback_days: int = _FLOW_LOOKBACK_DAYS) -> dict | None:
    """Flow for one ticker as a MOVING AVERAGE over the last `_FLOW_TARGET_DAYS` days.

    Not "change since last run". Each stored snapshot pair gives an interval's average daily
    flow rate (Δshares / days); each of the last N days takes the rate of the interval it falls
    in, and we average them → a smooth N-day rate whether you run daily (event mode) or monthly.
    Example (a 2-month-old reading + one fresh day with a huge move): the 6 older days carry the
    long-run daily average, today carries the spike → (6×avg + spike)/7.

    shares_now from the cascade (stockanalysis → iShares → yfinance); no prior data at all → a
    baseline for next run (flow_pct None).
    """
    shares_now, source, nav_price = _shares_multi(ticker, health)
    if shares_now is None:
        return {"ticker": ticker, "error": "no shares from any source", "shares_source": None}

    implied_aum = (shares_now * nav_price) if (shares_now and nav_price) else None
    out = {
        "ticker": ticker,
        "shares_outstanding": shares_now,
        "shares_source": source,
        "nav_price": round(nav_price, 4) if nav_price else None,
        "implied_aum_m_usd": round(implied_aum / 1e6, 1) if implied_aum else None,
        "flow_pct": None,
        "flow_window_days": None,
        "flow_days_covered": None,
        "fetched_at": date.today().isoformat(),
    }
    tw = _trailing_window_flow(ticker, shares_now, lookback_days, _FLOW_TARGET_DAYS)
    if tw:
        out.update(tw)
    return out


def _load_shares_series(ticker: str, lookback_days: int) -> list[tuple[date, float]]:
    """All stored (date, shares) for `ticker` within the lookback, strictly before today,
    real-source only (drops legacy derived_from_total_assets), ascending, one per date.
    Matches either `ticker` or `flow_proxy_ticker` so a proxy's history is found."""
    try:
        import pandas as pd
        from catalyx.store import lake
    except Exception:  # noqa: BLE001
        return []
    df = lake.read_table("flow")
    if df.empty or "shares_outstanding" not in df.columns:
        return []
    today = date.today()
    cutoff = today - timedelta(days=lookback_days)
    if "flow_proxy_ticker" in df.columns:
        mask = (df["ticker"] == ticker) | (df["flow_proxy_ticker"] == ticker)
    else:
        mask = df["ticker"] == ticker
    sub = df[mask]
    if "shares_source" in sub.columns:
        sub = sub[sub["shares_source"] != "derived_from_total_assets"]
    by_date: dict[date, float] = {}
    for _, r in sub.iterrows():
        try:
            d = date.fromisoformat(str(r["date"])[:10])
        except (ValueError, TypeError):
            continue
        s = r.get("shares_outstanding")
        if s is None or (isinstance(s, float) and pd.isna(s)):
            continue
        if cutoff < d < today:
            by_date[d] = float(s)  # last write per date wins
    return sorted(by_date.items())


def _trailing_window_flow(ticker: str, shares_now: float, lookback_days: int,
                          window: int) -> dict | None:
    """Moving average of the daily flow rate over the last `window` days → a window-% flow.

    Builds a piecewise daily-rate timeline from the stored snapshots (each interval = Δshares/
    days), takes the rate for each of the last `window` days, and averages. Returns
    {flow_pct, flow_window_days, flow_days_covered} or None if no prior data (→ baseline).
    flow_pct = (avg_daily_rate × window) / shares_now × 100.
    """
    series = _load_shares_series(ticker, lookback_days)
    if not series:
        return None
    pts = series + [(date.today(), float(shares_now))]
    intervals: list[tuple[date, date, float]] = []
    for (d0, s0), (d1, s1) in zip(pts, pts[1:]):
        nd = (d1 - d0).days
        if nd > 0:
            intervals.append((d0, d1, (s1 - s0) / nd))  # shares per day
    if not intervals:
        return None
    today = date.today()
    rates: list[float] = []
    for i in range(window):
        d = today - timedelta(days=i)            # today, today-1, … today-(window-1)
        for d0, d1, rate in intervals:
            if d0 < d <= d1:
                rates.append(rate)
                break
    if not rates:
        return None
    avg_daily = sum(rates) / len(rates)
    flow_pct = (avg_daily * window) / shares_now * 100.0 if shares_now else None
    return {
        "flow_pct": round(flow_pct, 3) if flow_pct is not None else None,
        "flow_window_days": window,
        "flow_days_covered": len(rates),
    }


# Carry-forward window: how stale a last-good flow reading may be before we stop reusing it.
# On a CLOSED market no creation/redemption happens, so the last trading day's flow IS today's
# flow — reusing it is correct, not a hack. Capped so a long gap (extended outage/holiday) does
# eventually fall back to neutral rather than parroting an arbitrarily old number.
_CARRY_FORWARD_MAX_DAYS = 7


def _carry_forward_flow(signal_ticker: str, max_age_days: int = _CARRY_FORWARD_MAX_DAYS) -> dict | None:
    """Last genuine flow reading for `signal_ticker` to reuse when THIS run can't compute one
    (market closed → no fresh direct shares, or a transient yfinance failure).

    Keyed by TICKER, not sector: flow is a property of the ETF, so two sectors sharing an ETF
    (e.g. cybersecurity_defense + cybersecurity_commercial both on CIBR) MUST carry the same
    value. Matches the ticker against either the execution `ticker` or `flow_proxy_ticker`.

    Returns the most recent lake row (strictly before today, within max_age_days) whose
    data_quality was a real reading (computed/proxy_computed) — so the pipeline degrades to
    'last good value, marked stale' instead of a blank neutral 50. None if nothing eligible.
    """
    try:
        import pandas as pd
        from catalyx.store import lake
    except Exception:  # noqa: BLE001
        return None
    df = lake.read_table("flow")
    if df.empty or "data_quality" not in df.columns or not signal_ticker:
        return None
    today = date.today()
    cutoff = today - timedelta(days=max_age_days)

    def _d(s):
        try:
            return date.fromisoformat(str(s))
        except ValueError:
            return None

    tick_match = df["ticker"] == signal_ticker
    if "flow_proxy_ticker" in df.columns:
        tick_match = tick_match | (df["flow_proxy_ticker"] == signal_ticker)
    sub = df[tick_match & df["data_quality"].isin(["computed", "proxy_computed"])].copy()
    if sub.empty:
        return None
    sub["_d"] = sub["date"].map(_d)
    sub = sub[sub["_d"].notna() & (sub["_d"] > cutoff) & (sub["_d"] < today)]
    if sub.empty:
        return None
    row = sub.sort_values("_d").iloc[-1]
    fc = row.get("flow_confirmation")
    if fc is None or (isinstance(fc, float) and pd.isna(fc)):
        return None
    pct = row.get("flow_pct")
    return {
        "flow_confirmation": float(fc),
        "flow_pct": None if (pct is None or (isinstance(pct, float) and pd.isna(pct))) else float(pct),
        "data_quality": "carried",
        "carried_from": row.get("date"),
        "carried_quality": row.get("data_quality"),
    }


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


def _flow_data_quality(flow_pct: float | None, proxy_used: bool) -> str:
    """Provenance label for the flow signal — surfaced verbatim in the dashboard.

    A flow_pct only ever exists on a consistent DIRECT sharesOutstanding basis (see the
    basis_ok gate in _fetch_yfinance — a totalAssets-derived basis is rejected because it
    re-injects price into the count). So only three states occur:

      computed       own vehicle, delta from reported sharesOutstanding (gold standard)
      proxy_computed same-theme PROXY's reported sharesOutstanding (UCITS vehicle exposes none)
      estimated      no usable direct-basis delta yet → flow_confirmation defaulted to 50
    """
    if flow_pct is None:
        return "estimated"
    return "proxy_computed" if proxy_used else "computed"


def _resolve_flow_signal(chain: list[str], health: dict,
                         lookback_days: int = _FLOW_LOOKBACK_DAYS) -> tuple[str | None, dict | None, str]:
    """Walk a sector's fallback chain and return the best available flow source.

    For each ticker, shares come from the cascade yfinance → stockanalysis → iShares (see
    _shares_multi). Prefers (best → worst):
      'flow'     a computable week-over-week delta — stop immediately
      'baseline' shares NOW but no prior yet — writes a baseline so the NEXT run computes
      'valid'    something resolved but no shares
      'none'     nothing

    Returns (signal_ticker, fetched_result, kind). Short-circuits on the first 'flow' so a
    covered primary costs one pass; a UCITS primary falls through to its US sibling.
    """
    baseline: tuple[str, dict] | None = None
    first_valid: tuple[str, dict] | None = None
    for tk in chain:
        r = _fetch_flow_for_ticker(tk, health, lookback_days)
        if not r or r.get("error"):
            continue
        if first_valid is None:
            first_valid = (tk, r)
        if r.get("flow_pct") is not None:
            return tk, r, "flow"
        if baseline is None and r.get("shares_outstanding"):
            baseline = (tk, r)
    if baseline is not None:
        return baseline[0], baseline[1], "baseline"
    if first_valid is not None:
        return first_valid[0], first_valid[1], "valid"
    return (chain[0] if chain else None), None, "none"


def _volume_flow_estimate(chain: list[str], window: int = 20) -> tuple[str | None, float | None, float | None]:
    """Money-flow APPROXIMATION from OHLCV — a single yfinance call, NO sharesOutstanding and
    NO prior snapshot needed, so every ticker gets a real signal instead of a blank 50.

    Uses Chaikin Money Flow over `window` sessions: where each day closes within its range,
    weighted by volume → net buying (accumulation) vs selling (distribution) pressure. This is
    not true ETF creation/redemption (that needs shares), but it is a legitimate flow PROXY and
    is available immediately for any tradeable ticker. Returns (ticker, [0-100] score, raw CMF).

    Mapping: CMF ~[-1,1] → 50 + CMF*80, clamped [10,90] (CMF ±0.5 → ±40 ≈ strong in/outflow).
    """
    import yfinance as yf

    for tk in chain:
        try:
            h = yf.Ticker(tk).history(period="2mo", auto_adjust=False)
        except Exception:  # noqa: BLE001
            continue
        if h is None or len(h) < 10:
            continue
        h = h.tail(window)
        hi, lo, cl, vol = h["High"], h["Low"], h["Close"], h["Volume"]
        rng = (hi - lo)
        # money-flow multiplier per day; flat-range days contribute 0 (avoid /0)
        mfm = (((cl - lo) - (hi - cl)) / rng.where(rng != 0)).fillna(0.0)
        total_vol = float(vol.sum())
        if total_vol <= 0:
            continue
        cmf = float((mfm * vol).sum() / total_vol)  # ~[-1, 1]
        score = round(max(10.0, min(90.0, 50.0 + cmf * 80.0)), 1)
        return tk, score, round(cmf, 3)
    return None, None, None


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
    sector_tickers: dict[str, list[str] | str] | None = None,
) -> dict:
    """Fetch flow data + institutional ownership breadth for all sectors.

    Args:
        sector_tickers: {sector_id: chain}. `chain` is an ordered fallback list of tickers
            (a bare string is accepted and wrapped). Defaults to SECTOR_FLOW_TICKERS.

    Returns:
        Snapshot dict with per-sector flow data, inst sponsorship scores, and raw data.
    """
    chains = sector_tickers or SECTOR_FLOW_TICKERS
    today = date.today().isoformat()
    etf_results: dict[str, dict] = {}
    sector_scores: dict[str, dict] = {}
    health = new_health()  # per-source ok/err tallies → drives the degraded-source warning

    for sector_id, chain in chains.items():
        chain = [chain] if isinstance(chain, str) else list(chain)
        primary = chain[0] if chain else None  # the realistic tradeable vehicle (chain head)

        # Walk the fallback chain → first ticker that yields a usable flow signal.
        signal_ticker, result, _kind = _resolve_flow_signal(chain, health)
        proxy_used = signal_ticker is not None and signal_ticker != primary

        # 13F institutional breadth is about the vehicle you actually HOLD → the primary.
        inst = _fetch_institutional_ownership(primary) if primary else {}

        if result:
            etf_results[signal_ticker] = {**result, **inst}

        flow_pct = result.get("flow_pct") if result else None
        error = result.get("error") if result else "fetch failed"

        score = {
            "ticker": primary,
            "flow_proxy_ticker": signal_ticker,
            "flow_proxy_used": proxy_used,
            "flow_source": result.get("shares_source") if result else None,
            "flow_confirmation": _flow_score(flow_pct),
            "flow_pct": flow_pct,
            "flow_window_days": result.get("flow_window_days") if result else None,
            "flow_days_covered": result.get("flow_days_covered") if result else None,
            "implied_aum_m_usd": result.get("implied_aum_m_usd") if result else None,
            "data_quality": _flow_data_quality(flow_pct, proxy_used),
            "error": error if flow_pct is None else None,
            "inst_sponsorship_score": inst.get("inst_sponsorship_score"),
            "inst_13f_filer_count": inst.get("inst_13f_filer_count"),
            "inst_source": inst.get("inst_source"),
        }
        # Resilience ladder — never silently park a sector at a neutral 50:
        #   1) carry forward the last genuine share-flow reading (market closed → no new flow), else
        #   2) a money-flow APPROXIMATION from price+volume (CMF) — one fetch, no prior needed, so a
        #      brand-new sector still gets a signal on its first run. CMF is flagged DISTINCTLY
        #      (it diverges from true flow). Only if even OHLCV is unavailable → estimated (50).
        if score["data_quality"] == "estimated":
            carried = _carry_forward_flow(signal_ticker or primary)
            if carried:
                score.update(carried)
            else:
                vt, vscore, cmf = _volume_flow_estimate(chain)
                if vscore is not None:
                    score["flow_confirmation"] = vscore
                    score["flow_pct"] = None
                    score["data_quality"] = "volume_proxy"
                    score["flow_source"] = "cmf"
                    score["flow_proxy_ticker"] = vt
                    score["flow_proxy_used"] = vt != primary
                    score["volume_cmf"] = cmf
                    score["error"] = None
        sector_scores[sector_id] = score

    # ── Source-health warning ────────────────────────────────────────────────────
    # stockanalysis is the workhorse (covers ~all US tickers); if it is DOWN we lose most of
    # the real-flow coverage and silently fall to CMF — so shout and mark the run degraded so
    # the pipeline/dashboard flags "flow source broken, review", not a quiet quality drop.
    degraded: list[str] = []
    for src in ("stockanalysis", "ishares"):
        h = health[src]
        if h["err"] > 0 and h["ok"] == 0:
            degraded.append(src)
    if "stockanalysis" in degraded:
        print("\n  ⚠⚠  FLOW WARNING: stockanalysis source is DOWN "
              f"({health['stockanalysis']['err']} errors, 0 ok). Real-flow coverage is "
              "degraded → most sectors fell back to CMF. REVIEW the source.\n", file=sys.stderr)

    return {
        "generated_at": today,
        "date": today,
        "source": "yfinance + stockanalysis + ishares + edgar_13f",
        "source_health": health,
        "degraded_sources": degraded,
        "note": (
            "flow_pct = (shares_now/shares_prior − 1)×100 (NAV cancels). shares via cascade "
            "yfinance→stockanalysis→ishares. data_quality: computed/proxy_computed=real shares "
            "(see flow_source); carried=last good reused (flow_carried_from); "
            "volume_proxy=CMF price+volume APPROXIMATION (⚠ not true flow); estimated=neutral 50. "
            "degraded_sources flags a broken source (esp. stockanalysis) needing review."
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
        exec_ticker = s.get("ticker")
        # raw flow data is keyed by the SIGNAL ticker (the proxy when one is used)
        signal_ticker = s.get("flow_proxy_ticker", exec_ticker)
        raw = etfs.get(signal_ticker, {})
        rows.append({
            **meta,
            "sector_id": sid,
            "ticker": exec_ticker,
            "flow_proxy_ticker": signal_ticker,
            "flow_proxy_used": bool(s.get("flow_proxy_used", False)),
            "flow_source": s.get("flow_source"),
            "flow_confirmation": s.get("flow_confirmation"),
            "flow_pct": s.get("flow_pct"),
            "flow_window_days": s.get("flow_window_days"),
            "flow_days_covered": s.get("flow_days_covered"),
            "implied_aum_m_usd": s.get("implied_aum_m_usd"),
            "data_quality": s.get("data_quality"),
            "carried_from": s.get("carried_from"),
            "carried_quality": s.get("carried_quality"),
            "volume_cmf": s.get("volume_cmf"),
            "shares_outstanding": raw.get("shares_outstanding"),
            # shares_source mirrors flow_source on real rows — kept because the series loader
            # reads this column to exclude legacy derived_from_total_assets rows.
            "shares_source": raw.get("shares_source"),
            "nav_price": raw.get("nav_price"),
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
        f"  {'sector_id':<42} {'ticker':<10} {'flow_conf':>9}  {'quality':<14} "
        f"{'inst_score':>10}  {'13f_filers':>10}  {'aum_m':>8}"
    )
    print(f"  {'-'*42} {'-'*10} {'-'*9}  {'-'*14} {'-'*10}  {'-'*10}  {'-'*8}")

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
        dq = s.get("data_quality", "")
        if s.get("flow_proxy_used"):
            dq = f"{dq}→{s.get('flow_proxy_ticker', '')}"
        print(
            f"  {sid:<42} {s['ticker']:<10} {s['flow_confirmation']:>9.1f}  {dq:<14} "
            f"{inst_str}  {filer_str}  {aum_str}"
        )

    print(f"\n  {snapshot['note']}")


if __name__ == "__main__":
    main()
