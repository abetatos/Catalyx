"""
Market data fetcher for CATALYX Phase 0.5.
Fetches ETF price history via yfinance, computes momentum metrics,
and writes a snapshot JSON for the heatmap skill to consume.

Usage:
    uv run python -m catalyx.data.market_data
    uv run python -m catalyx.data.market_data --tickers COPX IQQH.DE GDX
    uv run python -m catalyx.data.market_data --output data/snapshots/momentum_snapshot.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf
from rich.console import Console
from rich.table import Table

# Force UTF-8 output on Windows to avoid cp1252 encoding errors with Unicode symbols.
import io, os, sys
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console(highlight=False)

# Canonical sector → primary ETF tickers mapping.
# Mirrors etf_universe.yaml recommendation_tier 1/2 picks. First ticker = preferred
# (used by momentum_engine as the PRIMARY); the rest are alternatives.
#
# Coverage goal (v1.6): every INVESTABLE sector in sector_taxonomy.yaml that has a
# liquid, yfinance-reliable proxy. Tickers are curated for yfinance reliability —
# US-listed where possible (richer history), .L/.DE only when that is the cleanest
# proxy. Each ticker is unique across sectors so two sectors never share a momentum
# series. Sectors deliberately OMITTED (no pure-play / illiquid yfinance proxy):
#   royalty_streaming_metals, cobalt_nickel, insurance_eu, asset_management,
#   private_equity_listed, real_estate_logistics, synthetic_biology, longevity_biotech,
#   hydrogen_clean_fuels (HDRO/HDRO.L not resolvable in yfinance, poor proxy anyway).
# Watch-only sectors (quantum_computing, nuclear_fusion, brain_computer_interface,
#   advanced_materials_metamaterials) are excluded by definition — no investable ETF.
SECTOR_TICKERS: dict[str, list[str]] = {
    # ── Defense ──────────────────────────────────────────────────────────────
    "eu_defense_prime_contractors":  ["EUDF.L", "DFEN.DE"],
    "us_defense_prime_contractors":  ["ITA", "XAR"],
    "cybersecurity_defense":         ["CIBR", "BUG"],
    "space_defense_satellite":       ["ROKT"],
    "drone_autonomous_systems":      ["SHLD"],
    # ── Energy ───────────────────────────────────────────────────────────────
    "oil_majors_integrated":         ["XLE", "IUES.L"],
    "oil_services_equipment":        ["OIH"],
    "lng_natural_gas":               ["FCG"],
    "nuclear_energy":                ["NLR"],
    "uranium_miners":                ["URNM", "URA"],
    "solar_energy":                  ["TAN"],
    "wind_energy_offshore":          ["FAN"],
    "grid_infrastructure_utilities": ["IQQH.DE", "GRID"],
    # ── Precious metals ──────────────────────────────────────────────────────
    "gold_physical":                 ["IGLN.L", "GLD"],
    "gold_miners":                   ["GDX", "AUCO.L"],
    "silver_physical":               ["PHAG.L", "SLV"],
    "silver_miners":                 ["SIL"],
    # ── Industrial metals & battery materials ────────────────────────────────
    "copper_miners":                 ["COPX", "COPX.L"],
    "lithium_miners":                ["LIT"],
    "rare_earth_miners":             ["REMX"],
    "steel_producers":               ["SLX"],
    "water_infrastructure":          ["PHO"],
    "agriculture_soft_commodities":  ["MOO", "DBA"],
    # ── Financials ───────────────────────────────────────────────────────────
    "eu_retail_banking":             ["EXV1.DE", "EUFN"],
    "us_retail_banking":             ["KRE", "KBE"],
    "fintech_payments":              ["FINX"],
    "crypto_infrastructure":         ["BTCE.DE"],
    # ── Technology ───────────────────────────────────────────────────────────
    "semiconductors_design":         ["SEMI.L", "SMH"],
    "semiconductors_equipment":      ["ASML"],
    "semiconductors_foundry":        ["TSM"],
    "semiconductors_memory":         ["DRAM"],
    "ai_infrastructure_data_centers":["WTAI", "BOTZ"],
    "cloud_software_saas":           ["CLOU", "WCLD"],
    "robotics_automation":           ["ROBO"],
    "cybersecurity_commercial":      ["ISPY.L"],
    # ── Healthcare ───────────────────────────────────────────────────────────
    "biotech_drug_development":      ["XBI", "IBB"],
    "medical_devices":               ["IHI"],
    "genomics_precision_medicine":   ["ARKG"],
    "pharma_large_cap":              ["IHE", "PPH"],
    # ── Real assets & consumer ───────────────────────────────────────────────
    "real_estate_data_centers":      ["SRVR"],
    "infrastructure_core":           ["IFRA", "INFR.L"],
    "luxury_goods":                  ["GLUX.SW"],
    "consumer_india_em":             ["INDA", "NDIA.L"],
    # ── Deep tech (investable) ───────────────────────────────────────────────
    "space_commercial":              ["UFO"],
}

# Weights from scoring_weights.yaml momentum_period_weights
MOMENTUM_WEIGHTS = {"1m": 0.20, "3m": 0.45, "6m": 0.35}

# Trading day approximations
TRADING_DAYS = {"1m": 22, "3m": 63, "6m": 126, "1y": 252}


def _safe_return(series, lookback_days: int) -> float | None:
    """Return percentage change over lookback_days, or None if insufficient data."""
    if len(series) < lookback_days + 1:
        return None
    current = series.iloc[-1]
    past = series.iloc[-lookback_days - 1]
    if past == 0:
        return None
    return (current / past) - 1.0


def momentum_score(ret_1m: float | None, ret_3m: float | None, ret_6m: float | None) -> float | None:
    """
    Weighted momentum score [0, 100].
    Formula: raw = w1m * r1m + w3m * r3m + w6m * r6m
    score = 50 + 50 * tanh(raw / 0.30)
    Returns None if all inputs are None.
    """
    available = [(w, r) for w, r in [
        (MOMENTUM_WEIGHTS["1m"], ret_1m),
        (MOMENTUM_WEIGHTS["3m"], ret_3m),
        (MOMENTUM_WEIGHTS["6m"], ret_6m),
    ] if r is not None]
    if not available:
        return None
    total_weight = sum(w for w, _ in available)
    raw = sum(w * r for w, r in available) / total_weight
    score = 50.0 + 50.0 * math.tanh(raw / 0.30)
    return round(score, 1)


def fetch_metrics(ticker: str, period: str = "1y") -> dict | None:
    """Fetch yfinance history and return computed metrics dict."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period, auto_adjust=True)
    except Exception as e:
        console.print(f"[yellow]WARN {ticker}: {e}[/yellow]")
        return None

    if hist.empty or len(hist) < 5:
        console.print(f"[yellow]WARN {ticker}: insufficient data[/yellow]")
        return None

    # Drop NaN closes BEFORE any return math. yfinance appends an empty bar for
    # today's session when the exchange has not yet opened/settled (e.g. US ETFs
    # fetched during European morning hours), making closes.iloc[-1] NaN and
    # poisoning every return → momentum_score. Use only settled closes.
    closes = hist["Close"].dropna()
    if len(closes) < 5:
        console.print(f"[yellow]WARN {ticker}: insufficient data after dropna[/yellow]")
        return None
    current = float(closes.iloc[-1])
    high_52w = float(closes.max())
    low_52w = float(closes.min())

    r1m = _safe_return(closes, TRADING_DAYS["1m"])
    r3m = _safe_return(closes, TRADING_DAYS["3m"])
    r6m = _safe_return(closes, TRADING_DAYS["6m"])
    r1y = _safe_return(closes, TRADING_DAYS["1y"])

    mscore = momentum_score(r1m, r3m, r6m)

    return {
        "ticker": ticker,
        "current_price": round(current, 4),
        "currency": hist.attrs.get("currency", "USD"),
        "return_1m_pct": round(r1m * 100, 2) if r1m is not None else None,
        "return_3m_pct": round(r3m * 100, 2) if r3m is not None else None,
        "return_6m_pct": round(r6m * 100, 2) if r6m is not None else None,
        "return_1y_pct": round(r1y * 100, 2) if r1y is not None else None,
        "high_52w": round(high_52w, 4),
        "low_52w": round(low_52w, 4),
        "near_52w_high_pct": round((current / high_52w - 1) * 100, 2),
        "momentum_score": mscore,
        "data_points": len(closes),
        "last_date": closes.index[-1].strftime("%Y-%m-%d"),
    }


def run_snapshot(
    tickers: list[str] | None = None,
    output_path: Path | None = None,
    show_table: bool = True,
) -> dict:
    """
    Fetch metrics for all configured tickers (or a custom list) and write snapshot.
    Returns the snapshot dict.
    """
    today_str = date.today().isoformat()

    if tickers:
        # Custom ticker list — no sector mapping
        jobs = {t: [t] for t in tickers}
    else:
        jobs = SECTOR_TICKERS

    snapshot: dict = {
        "generated_at": datetime.now().isoformat(),
        "date": today_str,
        "source": "yfinance",
        "sectors": {},
        "standalone_tickers": {},
    }

    all_results: list[dict] = []

    for sector_or_ticker, ticker_list in jobs.items():
        sector_results = []
        for tkr in ticker_list:
            console.print(f"  Fetching {tkr}...", end=" ")
            metrics = fetch_metrics(tkr)
            if metrics:
                # Newly-listed ETFs (e.g. DRAM, launched 2026) lack 3m/6m history → None.
                # Format defensively so a short-history ticker doesn't crash the snapshot.
                def _pct(v: float | None) -> str:
                    return f"{v:+.1f}%" if v is not None else "n/a"
                console.print(
                    f"[green]OK[/green] {metrics['current_price']:.2f} | "
                    f"1m={_pct(metrics['return_1m_pct'])} | "
                    f"3m={_pct(metrics['return_3m_pct'])} | "
                    f"score={metrics['momentum_score']}"
                )
                sector_results.append(metrics)
                all_results.append({**metrics, "sector_id": sector_or_ticker})
            else:
                console.print("[red]FAIL no data[/red]")

        if sector_results:
            if tickers:
                snapshot["standalone_tickers"][sector_or_ticker] = sector_results[0]
            else:
                snapshot["sectors"][sector_or_ticker] = {
                    "primary": sector_results[0],
                    "alternatives": sector_results[1:] if len(sector_results) > 1 else [],
                }

    if show_table and all_results:
        _print_table(all_results)

    if output_path is None:
        output_dir = Path("data/snapshots")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"momentum_snapshot_{today_str}.json"

    output_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    console.print(f"\n[bold green]Snapshot written → {output_path}[/bold green]")

    # Dual-write to the parquet lake (Tier 2 truth). Skip ad-hoc custom-ticker runs:
    # those have no sector mapping and would collide with the real daily partition.
    if not tickers:
        _write_lake_partition(snapshot)

    return snapshot


def snapshot_to_rows(snapshot: dict) -> list[dict]:
    """Flatten a nested momentum snapshot into one flat row per (sector_id, ticker, role).

    This is the tabular shape the parquet lake stores: `momentum_engine` reconstructs
    the primary-per-sector view from `role == "primary"` rows.
    """
    meta = {
        "date": snapshot.get("date"),
        "generated_at": snapshot.get("generated_at"),
        "source": snapshot.get("source", "yfinance"),
    }
    rows: list[dict] = []
    for sid, data in snapshot.get("sectors", {}).items():
        primary = data.get("primary")
        if primary:
            rows.append({**meta, "sector_id": sid, "role": "primary", **primary})
        for alt in data.get("alternatives", []) or []:
            rows.append({**meta, "sector_id": sid, "role": "alternative", **alt})
    return rows


def _write_lake_partition(snapshot: dict) -> None:
    """Dual-write the snapshot to the parquet lake (Tier 2 source of truth).

    Best-effort during migration: wrapped so a parquet failure never breaks the
    existing JSON pipeline. Once `momentum_engine` reads the lake by default, the
    JSON write becomes the deprecated compatibility path.
    """
    rows = snapshot_to_rows(snapshot)
    if not rows or not snapshot.get("date"):
        return
    try:
        import pandas as pd
        from catalyx.store import lake

        df = pd.DataFrame(rows)
        lake.append_partition("momentum", df, {"date": snapshot["date"]}, overwrite=True)
        console.print(f"[dim]lake: momentum partition date={snapshot['date']} ({len(df)} rows)[/dim]")
    except Exception as e:  # noqa: BLE001 — never let lake break the JSON pipeline
        console.print(f"[yellow]WARN lake write skipped: {e}[/yellow]")


def backfill_lake() -> int:
    """Convert existing data/snapshots/momentum_snapshot_*.json into lake partitions."""
    n = 0
    for p in sorted(Path("data/snapshots").glob("momentum_snapshot_*.json")):
        snap = json.loads(p.read_text(encoding="utf-8"))
        snap.setdefault("date", p.stem.replace("momentum_snapshot_", ""))
        _write_lake_partition(snap)
        n += 1
    return n


def _print_table(results: list[dict]) -> None:
    table = Table(title="CATALYX Momentum Snapshot", show_lines=False)
    table.add_column("Sector / Ticker", style="cyan", no_wrap=True)
    table.add_column("Ticker", style="dim")
    table.add_column("Price", justify="right")
    table.add_column("1M%", justify="right")
    table.add_column("3M%", justify="right")
    table.add_column("6M%", justify="right")
    table.add_column("vs 52wH", justify="right")
    table.add_column("Momentum", justify="right")

    for r in sorted(results, key=lambda x: x.get("momentum_score") or 0, reverse=True):
        mscore = r.get("momentum_score")
        score_str = f"[bold green]{mscore}[/bold green]" if mscore and mscore > 65 else (
            f"[yellow]{mscore}[/yellow]" if mscore and mscore > 40 else
            f"[red]{mscore}[/red]"
        )
        table.add_row(
            r.get("sector_id", r["ticker"]),
            r["ticker"],
            f"{r['current_price']:.2f}",
            f"{r['return_1m_pct']:+.1f}%" if r.get("return_1m_pct") is not None else "—",
            f"{r['return_3m_pct']:+.1f}%" if r.get("return_3m_pct") is not None else "—",
            f"{r['return_6m_pct']:+.1f}%" if r.get("return_6m_pct") is not None else "—",
            f"{r['near_52w_high_pct']:+.1f}%" if r.get("near_52w_high_pct") is not None else "—",
            score_str,
        )
    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CATALYX market data fetcher — generates momentum snapshot for heatmap."
    )
    parser.add_argument(
        "--tickers", nargs="+", metavar="TICKER",
        help="Custom ticker list. If omitted, fetches all configured sector ETFs."
    )
    parser.add_argument(
        "--output", type=Path, metavar="FILE",
        help="Output JSON path. Default: data/snapshots/momentum_snapshot_YYYYMMDD.json"
    )
    parser.add_argument(
        "--no-table", action="store_true",
        help="Suppress the Rich table output."
    )
    parser.add_argument(
        "--backfill-lake", action="store_true",
        help="Convert existing momentum_snapshot_*.json into lake parquet partitions and exit."
    )
    args = parser.parse_args()

    if args.backfill_lake:
        n = backfill_lake()
        console.print(f"[bold green]Backfilled {n} snapshot(s) into the lake[/bold green]")
        return

    console.print("[bold cyan]CATALYX — Market Data Snapshot[/bold cyan]")
    console.print(f"Date: {date.today().isoformat()}\n")

    run_snapshot(
        tickers=args.tickers,
        output_path=args.output,
        show_table=not args.no_table,
    )


if __name__ == "__main__":
    main()
