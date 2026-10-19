"""
Market data fetcher for CATALYX Phase 0.5.
Reads ETF price history from the shared `prices` cache, computes momentum metrics,
and writes a snapshot JSON for the heatmap skill to consume.

This module talks to `catalyx.data.prices`, never to yfinance. It was the last consumer
still fetching independently (v14.3) — see MOMENTUM_HISTORY_DAYS for what that cost.

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

from rich.console import Console
from rich.table import Table

# Force UTF-8 output on Windows to avoid cp1252 encoding errors with Unicode symbols.
import io, os, sys
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console(highlight=False)

# Canonical sector → tradeable ETF tickers, mirroring etf_universe.yaml (v2.0).
#
# ── CAMBIO DE DOCTRINA (2026-08-27, universo v2.0) ─────────────────────────────
# Antes esta tabla priorizaba tickers US "por fiabilidad de yfinance": el momentum de
# copper_miners salia de COPX (NYSE, USD), el de gold_miners de GDX, el de biotech de
# XBI. Pero NINGUNO de esos se puede comprar desde Espana (US no-UCITS, PRIIPs). Se
# rankeaba el heatmap sobre un instrumento y se operaba otro, en otra divisa — el
# retorno medido no era el retorno obtenible.
#
# REGLA AHORA: chain[0] es el vehiculo REALMENTE COMPRABLE (tier-1 de etf_universe.yaml,
# verificado contra yfinance). El momentum que puntua el heatmap es el que capturarias
# de verdad, FX incluido. Los alternativos son otras lineas del mismo fondo o el tier-2.
#
# NOTA: esto es lo contrario de SECTOR_FLOW_TICKERS en flow_data.py, y a proposito.
# Alli los proxies US son correctos porque yfinance solo expone `sharesOutstanding`
# (= creacion/reembolso = flujo) en fondos US. Momentum = precio que operas (UCITS);
# flujo = senal de demanda del tema (proxy US valido). No unificar las dos tablas.
#
# Cobertura: los 25 sectores investables de sector_taxonomy.yaml v2.0. Los 28 retirados
# a watch-only quedan fuera por definicion — no tienen vehiculo comprable.
# Tickers en GBp (RAYS.L, INRG.L, SPGP.L, SSLN.L, SPAG.L, INFR.L) no dan
# problema: el momentum es un RETORNO, y la escala peniques/libras se cancela. Lo que
# NO se cancela es la DIVISA de cotizacion: una linea en GBp mide el retorno de un
# comprador en GBP. Por eso, cuando el mismo fondo cotiza en EUR y esa es la linea que
# se opera, chain[0] es la EUR (2026-09-01: IQQQ.DE sobre IH2O.L, 2B76.DE sobre RBOT.L).
SECTOR_TICKERS: dict[str, list[str]] = {
    # ── Defensa y espacio ────────────────────────────────────────────────────
    "eu_defense_prime_contractors":  ["EUDF.L", "NATO.PA", "DFEN.DE"],
    # 2026-10-17: DFCU.DE lanzado 2026-06-29 (~80 barras): el spec 12-1 no es medible hasta 2027.
    # us_defense_prime_contractors RETIRADO a watch_only 2026-10-19 (DFCU.DE no esta en el
    # buscador de Revolut). Su cadena era ["DFCU.DE"] — anotada para que vuelva intacta.
    "space_defense_satellite":       ["JEDI.DE", "JEDI.L"],
    # ── Energia ──────────────────────────────────────────────────────────────
    "oil_majors_integrated":         ["IUES.L"],
    "nuclear_energy":                ["NUKL.DE"],
    "uranium_miners":                ["URNM.L", "URNU.DE"],   # URNU.DE solo tiene 4 dias de historico en yfinance
    "solar_energy":                  ["RAYS.L"],
    "grid_infrastructure_utilities": ["IQQH.DE", "INRG.L"],   # clean energy, no grid puro — ver aviso en etf_universe.yaml
    # ── Metales preciosos ────────────────────────────────────────────────────
    "gold_physical":                 ["PPFB.DE", "IGLN.L", "4GLD.DE"],  # PPFB.DE = mismo ETC de iShares, linea Xetra EUR; es la que devuelve el buscador del broker
    "gold_miners":                   ["SPGP.L", "GDX.L", "AUCO.L"],
    "silver_physical":               ["PHAG.L", "SSLN.L"],
    # ── Metales industriales y agro ──────────────────────────────────────────
    "copper_miners":                 ["4COP.DE", "COPX.L"],
    "lithium_miners":                ["VOLT.L", "LITU.L"],
    "agriculture_soft_commodities":  ["SPAG.L"],
    "water_infrastructure":          ["IQQQ.DE", "IH2O.L"],   # linea Xetra EUR; IH2O.L (GBp) no aparece en el buscador
    # ── Financiero ───────────────────────────────────────────────────────────
    "eu_retail_banking":             ["EXV1.DE"],
    "us_retail_banking":             ["IUS2.DE", "BNKS.L"],   # mismo fondo iShares IE00BD3V0B10: Xetra EUR / LSE USD
    "crypto_infrastructure":         ["DAPP.L"],
    # ── Tecnologia ───────────────────────────────────────────────────────────
    # 2026-09-01: SEC0.DE pasa a cabeza. Es el MISMO fondo que SEMI.L (longName identico en
    # yfinance) pero la linea de Xetra en EUR, y es donde se ejecuto el add de ese dia. La
    # regla es puntuar el vehiculo que se OPERA: `test_the_scored_vehicle_is_the_recommended_vehicle`
    # lo caza en cuanto la cabeza de esta cadena y `primary_etf` divergen.
    "semiconductors_design":         ["SEC0.DE", "SEMI.L", "SMGB.L"],
    # V9N = Global X Data Center REITs UCITS (53% REITs: EQIX/DLR/AMT/CCI). Antes XAIX.DE,
    # que es IA generalista (0% real estate) y media la beta de otro sector.
    "ai_infrastructure_data_centers":["V9N.DE", "XAIX.DE", "AIAI.L"],
    "robotics_automation":           ["2B76.DE", "RBOT.L"],   # 2B76 = misma clase en Xetra; antes IQQR.DE = MSCI Eastern Europe (sector equivocado)
    "cybersecurity_commercial":      ["USPY.L", "LOCK.L"],
    # cloud_software_saas RETIRADO a watch_only 2026-09-01 (ningun vehiculo listado en el
    # broker: WCLD/SKYE/SKYU/FSKY/SKYY/DGTL buscados uno a uno, ninguno aparece). Su cadena
    # era ["WCLD.L", "DGTL.L"] — anotada aqui para que vuelva intacta si el sector vuelve.
    # ── Salud ────────────────────────────────────────────────────────────────
    "pharma_large_cap":              ["IUHE.AS", "IUHC.L"],
    # BTEC.L fuera de la cadena 2026-09-01: no esta en el buscador del broker (usuario).
    # NBTK.DE = mismo indice NASDAQ Biotechnology, linea Xetra EUR, rho semanal EUR 0,984
    # contra BTEC.L. OM3E.MU es el MISMO fondo que BTEC y tambien comprable, pero su
    # cotizacion de Munich es de creador de mercado (245/254 dias a volumen cero, spread
    # 358bps) y mide rho 0,727 contra su propia linea de LSE: se queda fuera de la cadena
    # a proposito -- un fallback silencioso a una serie de ese ruido puntua otra cosa.
    "biotech_drug_development":      ["NBTK.DE", "HEAL.L"],
    # ── Activos reales y consumo ─────────────────────────────────────────────
    "infrastructure_core":           ["INFR.L"],
    "luxury_goods":                  ["GLUX.SW", "LUXU.L"],
    "consumer_india_em":             ["NDIA.L"],
}

# Weights from scoring_weights.yaml momentum_period_weights
MOMENTUM_WEIGHTS = {"1m": 0.20, "3m": 0.45, "6m": 0.35}

# Trading day approximations
TRADING_DAYS = {"1m": 22, "3m": 63, "6m": 126, "1y": 252}

# The 52w-high window, in BARS — the same unit `_safe_return` counts in, so the high and the
# 12-1 anchor are read off the same slice of the same series.
MOMENTUM_WINDOW_BARS = 252

# ── WHY THIS IS 450 AND NOT `period="1y"` (v14.3) ────────────────────────────────────────
# The official momentum spec (v8 Q1, §momentum_spec) is 0.635 x pct(12-1) + 0.365 x
# pct(52w-high). 12-1 needs `return_1y_pct`, and `_safe_return(closes, 252)` needs 253 BARS.
# `period="1y"` asks for 365 CALENDAR days, and how many bars that returns is a property of
# the exchange's calendar, not of the fund: measured 2026-09-01 across the live universe,
# Xetra returned 251-252, SIX 247, LSE 252-254, Euronext 255. So the cut at 253 fell straight
# through the middle of the venue distribution and the official spec reached 12 of 25 sectors
# — every `.DE` line in the fallback, none of them for want of history.
#
# The bias was SYSTEMATIC and self-worsening: broker-reality migrations move vehicles TOWARD
# Xetra (v14.1 BTEC.L->NBTK.DE, v14.2 SEMI.L->SEC0.DE), so each correction to a more buyable
# vehicle shrank the coverage of the scored one. Next run would have been 10 of 25.
#
# Measured effect of closing the gap (same vehicles, same everything else, only the window):
# 12-1 coverage 13/26 -> 26/26; composite rank moves on 19 of 25 sectors, max 8 places,
# Spearman 0.906 — and the top-6, which IS the book at `n_target: 6`, changes membership
# (`water_infrastructure` 2->8 out, `ai_infrastructure_data_centers` 12->6 in, copper 5->1).
# A 42%-coverage dimension was not noise; it was picking positions.
#
# 450 days ~= 305-315 bars, so ~55 bars of margin over the 253 the anchor needs. 400 (the
# cache's own cold-fetch depth) would have worked at 272-282 bars, but GLUX.SW sat 19 bars
# from the cliff and a thin margin here fails the way the old one did: silently, per venue,
# in the direction of less coverage.
MOMENTUM_HISTORY_DAYS = 450


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


def compute_metrics(ticker: str, closes, quote_ccy: str | None,
                    return_basis: str | None) -> dict | None:
    """Momentum metrics for ONE already-priced series. Pure: no network, no cache, no FX.

    `closes` arrives in the basis `return_basis` names (EUR wherever the FX leg resolved),
    because conversion belongs to the layer that owns the cache and the per-ticker currency
    — `prices.to_eur` — not to a second copy here. This function used to hold that copy.
    """
    closes = closes.dropna()
    if len(closes) < 5:
        console.print(f"[yellow]WARN {ticker}: insufficient data after dropna[/yellow]")
        return None

    current = float(closes.iloc[-1])

    # ── THE 52-WEEK WINDOW IS 52 WEEKS, not "whatever we happened to fetch" (v14.3) ──
    # This was `closes.max()` over the WHOLE fetched series, which tied the meaning of the
    # 52w-high dimension — 36.5% of the official momentum blend — to the fetch window. While
    # that window was `period="1y"` the two coincided and nothing was visibly wrong; the
    # moment the window widens to give 12-1 the coverage it needs, `near_52w_high_pct` would
    # silently start measuring proximity to a 15-month high for all 26 rows. Measured at the
    # 400-day cache the delta is exactly 0.00 on every vehicle (no ticker set a new high in
    # the extra ~26 bars), so this change moves nothing today — which is the point: the
    # coupling is latent, undefended, and one bar away from re-scoring the universe by
    # accident. Windowed explicitly, the number means what its name says at any fetch depth.
    window = closes.iloc[-MOMENTUM_WINDOW_BARS:]
    high_52w = float(window.max())
    low_52w = float(window.min())

    r1m = _safe_return(closes, TRADING_DAYS["1m"])
    r3m = _safe_return(closes, TRADING_DAYS["3m"])
    r6m = _safe_return(closes, TRADING_DAYS["6m"])
    r1y = _safe_return(closes, TRADING_DAYS["1y"])

    return {
        "ticker": ticker,
        "current_price": round(current, 4),
        "currency": quote_ccy,
        "return_basis": return_basis or "unknown",
        "return_1m_pct": round(r1m * 100, 2) if r1m is not None else None,
        "return_3m_pct": round(r3m * 100, 2) if r3m is not None else None,
        "return_6m_pct": round(r6m * 100, 2) if r6m is not None else None,
        "return_1y_pct": round(r1y * 100, 2) if r1y is not None else None,
        "high_52w": round(high_52w, 4),
        "low_52w": round(low_52w, 4),
        "near_52w_high_pct": round((current / high_52w - 1) * 100, 2),
        "momentum_score": momentum_score(r1m, r3m, r6m),
        "data_points": len(closes),
        "last_date": closes.index[-1].strftime("%Y-%m-%d"),
    }


def price_panel(tickers: list[str], history_days: int = MOMENTUM_HISTORY_DAYS,
                end: str | None = None, frame_fn=None, ccy_fn=None):
    """(EUR frame, {ticker: basis}, {ticker: quote ccy}) for `tickers`, from the SHARED cache.

    ONE call for the whole universe. `prices` fetches only what it is missing, so a second
    module in the same run pays nothing.
    """
    from catalyx.data import prices

    end = str(end or date.today().isoformat())[:10]
    start = (date.fromisoformat(end) - timedelta(days=history_days)).isoformat()
    frame, basis = (frame_fn or prices.read_eur_with_basis)(list(tickers), start, end)
    if frame is None or len(frame) == 0:
        return frame, {}, {}
    # default=None: the quote currency is a DIAGNOSTIC column here, and an unreadable one
    # must stay None rather than pass as a reading (see `prices.currencies`).
    ccy = (ccy_fn or prices.currencies)(list(getattr(frame, "columns", [])), default=None)
    return frame, basis, ccy


def fetch_metrics(ticker: str, history_days: int = MOMENTUM_HISTORY_DAYS,
                  end: str | None = None, frame_fn=None, ccy_fn=None) -> dict | None:
    """Metrics for ONE ticker, served from the shared price cache.

    Single-ticker convenience over `price_panel` + `compute_metrics`. `run_snapshot` does NOT
    call this in a loop — it prices the whole universe once.
    """
    frame, basis, ccy = price_panel([ticker], history_days, end, frame_fn, ccy_fn)
    if frame is None or len(frame) == 0 or ticker not in getattr(frame, "columns", []):
        console.print(f"[yellow]WARN {ticker}: no prices[/yellow]")
        return None
    return compute_metrics(ticker, frame[ticker], ccy.get(ticker), basis.get(ticker))


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
        "source": "prices_cache",
        "sectors": {},
        "standalone_tickers": {},
    }

    all_results: list[dict] = []

    # ONE priced panel for the whole universe — `prices` is the shared cache this module was
    # the last consumer to bypass (its docstring names `market_data` first among the ~15
    # independent yfinance hits it was built to collapse). Beyond the round-trips, a per-
    # ticker `yf.Ticker().history(period="1y")` made the LOOKBACK a per-venue accident; the
    # cache answers a date range, so every row is read off the same window.
    every_ticker = sorted({t for lst in jobs.values() for t in lst})
    console.print(f"  Pricing {len(every_ticker)} tickers from the shared cache...")
    frame, basis, ccy = price_panel(every_ticker, end=today_str)
    columns = set(getattr(frame, "columns", []))

    for sector_or_ticker, ticker_list in jobs.items():
        sector_results = []
        for tkr in ticker_list:
            console.print(f"  {tkr}...", end=" ")
            metrics = (compute_metrics(tkr, frame[tkr], ccy.get(tkr), basis.get(tkr))
                       if tkr in columns else None)
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
        # An ad-hoc `--tickers` run is NOT the day's snapshot and must not claim its filename.
        # It already skips the lake partition for exactly that reason ("would collide with the
        # real daily partition") — but it still overwrote the dated JSON, so a two-ticker
        # smoke test replaced the 25-sector snapshot of a recorded run with two standalone
        # rows, silently and under the name every reader resolves by date. Same rule, both
        # sinks: an ad-hoc run writes an ad-hoc file.
        name = f"momentum_snapshot_{today_str}.json" if not tickers else f"momentum_adhoc_{today_str}.json"
        output_path = output_dir / name

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
