"""Unified DuckDB read-path over the parquet lake (Fase E).

The day-to-day query layer and the data foundation for the GitHub-Pages dashboard
(DuckDB-WASM will run the same SQL in the browser). Everything here is READ-ONLY over the
lake — it never mutates the source of truth. `lake.connect()` exposes one view per table
(globbing its partitions); this module adds the curated analytical queries the user asked
for: score evolution, risk-profile comparison, and decision lineage.

Each function is defensive: a table with no partitions yet returns an empty result rather
than erroring, so the dashboard degrades gracefully before data accrues.

CLI:
    uv run python -m catalyx.store.lake_query ranking [--top-n 10]
    uv run python -m catalyx.store.lake_query sector <sector_id>
    uv run python -m catalyx.store.lake_query portfolios
    uv run python -m catalyx.store.lake_query lineage <trade_id>
    uv run python -m catalyx.store.lake_query sql "SELECT ... FROM sector_snapshot ..."
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from catalyx.store import lake


def _has(table: str, lake_dir: Path | None) -> bool:
    return bool(lake.list_partitions(table, lake_dir=lake_dir))


def _df(sql: str, params: list | None = None, lake_dir: Path | None = None):
    con = lake.connect(lake_dir=lake_dir)
    try:
        return con.execute(sql, params or []).fetchdf()
    finally:
        con.close()


# ── Queries ──────────────────────────────────────────────────────────────────

def sector_history(sector_id: str, lake_dir: Path | None = None) -> list[dict]:
    """A sector's score evolution across every run (the 'is it climbing?' view)."""
    if not _has("sector_snapshot", lake_dir):
        return []
    df = _df(
        # substr(...::VARCHAR) is tz-safe: the lake mixes tz-aware and tz-naive snapshot_at,
        # so CAST(... AS DATE) fails on TIMESTAMP WITH TIME ZONE in DuckDB.
        "SELECT run_id, substr(CAST(snapshot_at AS VARCHAR),1,10) AS date, rank, composite, momentum, "
        "catalyst_alignment, crowding_risk, scoring_version "
        "FROM sector_snapshot WHERE sector_id = ? ORDER BY run_id",
        [sector_id], lake_dir,
    )
    return df.to_dict(orient="records")


def latest_ranking(top_n: int = 10, lake_dir: Path | None = None) -> list[dict]:
    """Top-N sectors of the most recent run."""
    if not _has("sector_snapshot", lake_dir):
        return []
    df = _df(
        "SELECT sector_id, rank, composite, momentum, catalyst_alignment, crowding_risk, "
        "narrative_maturity, primary_etf FROM sector_snapshot "
        "WHERE run_id = (SELECT max(run_id) FROM sector_snapshot) "
        "ORDER BY rank LIMIT ?",
        [top_n], lake_dir,
    )
    return df.to_dict(orient="records")


def rank_moves(top_n: int = 10, lake_dir: Path | None = None) -> list[dict]:
    """Latest run's rank-change events (entered/exited top-N, big moves)."""
    if not _has("rank_event", lake_dir):
        return []
    df = _df(
        "SELECT sector_id, event_type, from_rank, to_rank, delta FROM rank_event "
        "WHERE run_id = (SELECT max(run_id) FROM rank_event) "
        "ORDER BY abs(coalesce(delta, 99)) DESC",
        None, lake_dir,
    )
    return df.to_dict(orient="records")


def portfolio_compare(lake_dir: Path | None = None) -> list[dict]:
    """Latest NAV/return per portfolio (model and real) — the risk-profile comparison."""
    if not _has("portfolio_nav", lake_dir):
        return []
    df = _df(
        "SELECT portfolio_id, kind, date, nav, return_pct, benchmark_etf, vs_benchmark_pct "
        "FROM portfolio_nav "
        "QUALIFY row_number() OVER (PARTITION BY portfolio_id ORDER BY date DESC) = 1 "
        "ORDER BY return_pct DESC",
        None, lake_dir,
    )
    return df.to_dict(orient="records")


def portfolio_holdings(portfolio_id: str, lake_dir: Path | None = None) -> list[dict]:
    if not _has("portfolio_holding", lake_dir):
        return []
    df = _df(
        "SELECT sector_id, primary_etf, weight_pct, composite, momentum, narrative_maturity "
        "FROM portfolio_holding WHERE portfolio_id = ? "
        "AND run_id = (SELECT max(run_id) FROM portfolio_holding WHERE portfolio_id = ?) "
        "ORDER BY rank_in_portfolio",
        [portfolio_id, portfolio_id], lake_dir,
    )
    return df.to_dict(orient="records")


def lineage_for_trade(trade_id: str, lake_dir: Path | None = None) -> dict:
    """Walk a trade back to the analysis that justified it: trade → run_id → report +
    that run's sector_snapshot for the traded ETF. (`thesis_id` points to a Tier-1 JSON doc.)"""
    if not _has("portfolio_trade", lake_dir):
        return {"error": "no trades in lake"}
    trade = _df("SELECT * FROM portfolio_trade WHERE trade_id = ?", [trade_id], lake_dir)
    if len(trade) == 0:
        return {"error": f"trade {trade_id} not found"}
    t = trade.to_dict(orient="records")[0]
    run_id = t.get("run_id")
    out = {"trade": t, "thesis_id": t.get("thesis_id"), "run_id": run_id,
           "reports": [], "sector_snapshot": None}
    if run_id and _has("report", lake_dir):
        out["reports"] = _df(
            "SELECT report_type, report_date, path FROM report WHERE run_id = ?",
            [run_id], lake_dir).to_dict(orient="records")
    if run_id and _has("sector_snapshot", lake_dir) and t.get("etf"):
        snap = _df(
            "SELECT sector_id, rank, composite, momentum, catalyst_alignment "
            "FROM sector_snapshot WHERE run_id = ? AND primary_etf = ?",
            [run_id, t["etf"]], lake_dir)
        rows = snap.to_dict(orient="records")
        out["sector_snapshot"] = rows[0] if rows else None
    return out


def sql(query: str, lake_dir: Path | None = None) -> list[dict]:
    """Ad-hoc SQL over the lake views (read-only)."""
    return _df(query, None, lake_dir).to_dict(orient="records")


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_rows(rows: list[dict]) -> None:
    if not rows:
        print("  (no rows)")
        return
    cols = list(rows[0].keys())
    print("  " + "  ".join(f"{c}" for c in cols))
    for r in rows:
        print("  " + "  ".join(f"{r[c]}" for c in cols))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX lake query layer (DuckDB read-path, Fase E)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("ranking", help="Latest run's top-N sectors")
    r.add_argument("--top-n", type=int, default=10)
    sc = sub.add_parser("sector", help="A sector's score history across runs")
    sc.add_argument("sector_id")
    sub.add_parser("moves", help="Latest run's rank-change events")
    sub.add_parser("portfolios", help="Latest NAV/return per portfolio")
    hd = sub.add_parser("holdings", help="A portfolio's latest holdings")
    hd.add_argument("portfolio_id")
    ln = sub.add_parser("lineage", help="Trace a trade back to its run + reports")
    ln.add_argument("trade_id")
    sq = sub.add_parser("sql", help="Ad-hoc SQL over the lake")
    sq.add_argument("query")
    args = p.parse_args()

    if args.cmd == "ranking":
        _print_rows(latest_ranking(args.top_n))
    elif args.cmd == "sector":
        _print_rows(sector_history(args.sector_id))
    elif args.cmd == "moves":
        _print_rows(rank_moves())
    elif args.cmd == "portfolios":
        _print_rows(portfolio_compare())
    elif args.cmd == "holdings":
        _print_rows(portfolio_holdings(args.portfolio_id))
    elif args.cmd == "lineage":
        import json
        print(json.dumps(lineage_for_trade(args.trade_id), indent=2, ensure_ascii=False, default=str))
    elif args.cmd == "sql":
        _print_rows(sql(args.query))


if __name__ == "__main__":
    main()
