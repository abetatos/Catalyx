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
    uv run python -m catalyx.store.lake_query lineage <movement_id>
    uv run python -m catalyx.store.lake_query catalyst-lineage <catalyst_id>
    uv run python -m catalyx.store.lake_query ledger
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


def lineage_for_movement(mov_id: str, lake_dir: Path | None = None) -> dict:
    """Walk a movement back to the analysis that justified it: movement → attributed catalysts
    + the score_run that was current as-of executed_at → that run's reports + the sector_snapshot
    for the movement's sector. (Movement truth is the Tier-1 file; this reads the lake mirror.)"""
    import json as _json
    if not _has("movement", lake_dir):
        return {"error": "no movements in lake (run movement_repo ingest)"}
    mv = _df("SELECT * FROM movement WHERE id = ?", [mov_id], lake_dir)
    if len(mv) == 0:
        return {"error": f"movement {mov_id} not found"}
    m = mv.to_dict(orient="records")[0]
    run_id = m.get("score_run_id") or m.get("run_id")
    try:
        catalysts = _json.loads(m.get("attribution_json") or "[]")
    except Exception:
        catalysts = []
    out = {"movement": m, "catalysts": catalysts, "run_id": run_id,
           "reports": [], "sector_snapshot": None}
    if run_id and _has("report", lake_dir):
        out["reports"] = _df(
            "SELECT report_type, report_date, path FROM report WHERE run_id = ?",
            [run_id], lake_dir).to_dict(orient="records")
    if run_id and _has("sector_snapshot", lake_dir) and m.get("sector_id"):
        snap = _df(
            "SELECT sector_id, rank, composite, momentum, catalyst_alignment "
            "FROM sector_snapshot WHERE run_id = ? AND sector_id = ?",
            [run_id, m["sector_id"]], lake_dir)
        rows = snap.to_dict(orient="records")
        out["sector_snapshot"] = rows[0] if rows else None
    return out


def _sectors_for_catalyst(catalyst_id: str) -> list[str]:
    """Sectors a catalyst drives, from each study's `active_catalyst_ids` (Tier-1 JSON).
    This is the same catalyst→sector map the dashboard reads from docs.json."""
    from catalyx.store import sector_study_repo as ssr
    return [s["sector_id"] for s in ssr._load_all()
            if catalyst_id in (s.get("active_catalyst_ids") or [])]


def catalyst_lineage(catalyst_id: str, lake_dir: Path | None = None) -> dict:
    """Decision lineage anchored on the CATALYST (the unit of the track record). For a catalyst:
      • `movements`  — the real-book movements attributed to it (movement.attribution → weight),
      • `sectors`    — the sectors it drives (study.active_catalyst_ids),
      • `timeseries` — per run (each portfolio rebalance is a point): each strategy's TOTAL weight
                       in those sectors = its exposure to the catalyst, plus the `combined` exposure
                       (mean across strategies — the system's average conviction in the catalyst),
      • `latest`     — the most recent run's per-strategy exposure + the Δ vs the prior rebalance.
    This is how the system's bet on a catalyst accumulates and rebalances over time. Read-only;
    degrades to empty sections before the data accrues."""
    import json as _json
    sectors = _sectors_for_catalyst(catalyst_id)
    out: dict = {"catalyst_id": catalyst_id, "sectors": sectors,
                 "movements": [], "timeseries": [], "latest": []}

    # (1) real movements attributed to this catalyst
    if _has("movement", lake_dir):
        mv = _df("SELECT id, executed_at, action, sector_id, amount_eur, attribution_json "
                 "FROM movement ORDER BY executed_at DESC", None, lake_dir)
        for m in mv.to_dict(orient="records"):
            try:
                attr = _json.loads(m.get("attribution_json") or "[]")
            except Exception:  # noqa: BLE001
                attr = []
            w = next((a for a in attr if a.get("catalyst_id") == catalyst_id), None)
            if w:
                out["movements"].append({
                    "id": m["id"], "executed_at": m["executed_at"], "action": m["action"],
                    "sector_id": m["sector_id"], "amount_eur": m["amount_eur"],
                    "attribution_weight": w.get("weight"),
                })

    # (2) per-run, per-strategy exposure (SUM of weights in the catalyst's sectors) + combined
    if sectors and _has("portfolio_holding", lake_dir):
        placeholders = ",".join(["?"] * len(sectors))
        rows = _df(
            f"SELECT portfolio_id, run_id, SUM(weight_pct) AS exposure FROM portfolio_holding "
            f"WHERE sector_id IN ({placeholders}) GROUP BY portfolio_id, run_id ORDER BY run_id",
            list(sectors), lake_dir,
        ).to_dict(orient="records")
        run_ids = sorted({r["run_id"] for r in rows})
        pids = sorted({r["portfolio_id"] for r in rows})
        exp: dict[str, dict] = {}                       # exp[pid][run_id] = exposure
        for r in rows:
            exp.setdefault(r["portfolio_id"], {})[r["run_id"]] = r["exposure"]
        for rid in run_ids:
            by_strat = {pid: round(exp.get(pid, {}).get(rid, 0.0), 2) for pid in pids}
            combined = round(sum(by_strat.values()) / len(pids), 2) if pids else 0.0
            out["timeseries"].append({"run_id": rid, "combined_pct": combined, "by_strategy": by_strat})
        if run_ids:
            cur, prev = run_ids[-1], (run_ids[-2] if len(run_ids) > 1 else None)
            for pid in pids:
                c = round(exp.get(pid, {}).get(cur, 0.0), 2)
                p = exp.get(pid, {}).get(prev) if prev else None
                if p is None:
                    move = "held" if prev is None else ("entered" if c > 0 else "—")
                elif c > 0 and p == 0:
                    move = "entered"
                elif c == 0 and p > 0:
                    move = "exited"
                else:
                    move = f"{c - p:+.1f}pp"
                out["latest"].append({"portfolio_id": pid, "exposure_pct": c, "move": move})
    return out


def catalyst_ledger(lake_dir: Path | None = None) -> list[dict]:
    """The latest catalyst track-record snapshot (invested + realized P&L per catalyst), from
    the time-versioned `catalyst_performance` table written by movement_repo ingest."""
    if not _has("catalyst_performance", lake_dir):
        return []
    return _df(
        "SELECT catalyst_id, invested_eur, realized_eur, n_movements, sectors "
        "FROM catalyst_performance "
        "WHERE as_of = (SELECT max(as_of) FROM catalyst_performance) "
        "ORDER BY invested_eur DESC", None, lake_dir,
    ).to_dict(orient="records")


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
    ln = sub.add_parser("lineage", help="Trace a movement back to its catalysts + run + reports")
    ln.add_argument("movement_id")
    cl = sub.add_parser("catalyst-lineage", help="Catalyst → real movements + sectors + per-strategy run-over-run moves")
    cl.add_argument("catalyst_id")
    sub.add_parser("ledger", help="Latest catalyst track-record (invested/realized per catalyst)")
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
        print(json.dumps(lineage_for_movement(args.movement_id), indent=2, ensure_ascii=False, default=str))
    elif args.cmd == "catalyst-lineage":
        import json
        print(json.dumps(catalyst_lineage(args.catalyst_id), indent=2, ensure_ascii=False, default=str))
    elif args.cmd == "ledger":
        _print_rows(catalyst_ledger())
    elif args.cmd == "sql":
        _print_rows(sql(args.query))


if __name__ == "__main__":
    main()
