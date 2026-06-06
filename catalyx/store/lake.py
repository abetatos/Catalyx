"""Parquet data lake — the durable, append-only source of truth (Tier 2).

This is the primitive every time-series / computed-numeric table is built on. It
replaced the old model where SQLite was the truth and parquet a throwaway export
(see docs/PLAN_lake_dvc_serving.md). Now: parquet IS the truth, committed to git.
SQLite has been removed entirely — the lake is the only persistent store.

Physical model — one logical table = a folder of partition files:

    data/lake/scores/sector_snapshot/
    ├── run_id=run_20260605_120000.parquet   ← one immutable file per run
    ├── run_id=run_20260706_090000.parquet
    └── ...

  • Partitioned: each run writes ONE new small file and never rewrites old ones →
    git stores each partition once (no binary bloat), and a half-written append
    can never corrupt prior data.
  • Append-only: a partition is immutable once written. `append_partition` refuses
    to overwrite unless `overwrite=True`. Corrections are new runs, not edits.
  • Read = union: `read_table` globs every partition file and concatenates them, so
    you query all months at once from HEAD (no commit-travel needed).

The partition key value lives BOTH in the filename (human-navigable) and as a real
column inside the file (so a plain `**/*.parquet` glob — including DuckDB-WASM in
the browser — can filter without hive-partition inference magic).

CLI:
    uv run python -m catalyx.store.lake tables
    uv run python -m catalyx.store.lake ls <table>
    uv run python -m catalyx.store.lake read <table> [--limit N]
    uv run python -m catalyx.store.lake seed-from-history
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).parents[2]
_LAKE_DIR = _REPO_ROOT / "data" / "lake"
_HISTORY_DIR = _REPO_ROOT / "data" / "history"

# ── Table registry ───────────────────────────────────────────────────────────
# table_name -> (relative path under data/lake, partition key columns)
# Empty key list = unpartitioned dataset that grows via timestamped part files.
TABLES: dict[str, tuple[str, list[str]]] = {
    # market snapshots (one partition per fetch date)
    "momentum":           ("market/momentum",            ["date"]),
    "flow":               ("market/flow",                ["date"]),
    # scoring history (one partition per run)
    "score_run":          ("scores/score_run",           ["run_id"]),
    "sector_snapshot":    ("scores/sector_snapshot",     ["run_id"]),
    "rank_event":         ("scores/rank_event",          ["run_id"]),
    # report: multiple rows per run (heatmap + dashboard …) → unpartitioned, grows via part files
    "report":             ("scores/report",              []),
    # indicator value history — externalized from the catalyst YAMLs; one file per catalyst
    "indicator_history":  ("indicators/indicator_history", ["catalyst_id"]),
    # portfolios (model + real)
    "portfolio_nav":      ("portfolio/nav",              ["portfolio_id"]),  # one NAV series file per portfolio
    "portfolio_holding":  ("portfolio/holding",          ["portfolio_id", "run_id"]),
    # movements: queryable mirror of the Tier-1 data/movements/*.json (truth stays in the files)
    "movement":           ("portfolio/movement",         ["sector_id"]),
    # catalyst track record — derived ledger, one time-versioned snapshot per ingest
    "catalyst_performance": ("validation/catalyst_performance", ["as_of"]),
    # validation / forward returns (grows; unpartitioned)
    "forward_returns":    ("validation/forward_returns", []),
    # dislocation lens (opportunities + diversifiers) — one materialization per run
    "dislocation":        ("analysis/dislocation",       ["run_id"]),
    # entry-timing overlay (micro-tension + event overhang) — one materialization per run
    "entry_timing":       ("analysis/entry_timing",      ["run_id"]),
    # portfolio rotation: diversifiers anchored to the REAL book's holdings (not the stressed cluster)
    "portfolio_rotation": ("analysis/portfolio_rotation", ["run_id"]),
}


# ── Path helpers ─────────────────────────────────────────────────────────────

def _resolve(table: str) -> tuple[str, list[str]]:
    if table not in TABLES:
        raise KeyError(f"unknown lake table {table!r}. Known: {', '.join(sorted(TABLES))}")
    return TABLES[table]


def table_dir(table: str, lake_dir: Path | None = None) -> Path:
    rel, _ = _resolve(table)
    return (lake_dir or _LAKE_DIR) / rel


def _sanitize(value: object) -> str:
    """Filesystem-safe rendering of a partition value (no separators)."""
    s = "null" if value is None else str(value)
    for bad in ("/", "\\", "=", ":", " "):
        s = s.replace(bad, "_")
    return s


def _partition_filename(keys: list[str], partition: dict | None) -> str:
    partition = partition or {}
    if not keys:
        # Unpartitioned: timestamped part file so repeated appends accumulate.
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        return f"part-{ts}.parquet"
    missing = [k for k in keys if k not in partition]
    if missing:
        raise ValueError(f"partition missing keys {missing} for this table (needs {keys})")
    return "__".join(f"{k}={_sanitize(partition[k])}" for k in keys) + ".parquet"


# ── Write ────────────────────────────────────────────────────────────────────

def append_partition(table: str, df: pd.DataFrame, partition: dict | None = None,
                     overwrite: bool = False, lake_dir: Path | None = None) -> Path:
    """Write `df` as one immutable partition of `table`. Append-only.

    The partition key columns are ensured present in `df` (filled from `partition`)
    so a plain glob read still carries them. Refuses to clobber an existing
    partition unless `overwrite=True`.
    """
    _, keys = _resolve(table)
    partition = partition or {}
    df = df.copy()
    for k in keys:
        if k not in df.columns:
            df[k] = partition.get(k)

    d = table_dir(table, lake_dir)
    fp = d / _partition_filename(keys, partition)
    if fp.exists() and not overwrite:
        raise FileExistsError(
            f"partition already exists (append-only): {fp.relative_to(lake_dir or _LAKE_DIR)}. "
            f"Pass overwrite=True only to correct a same-day write."
        )
    d.mkdir(parents=True, exist_ok=True)
    df.to_parquet(fp, index=False)
    return fp


# ── Read ─────────────────────────────────────────────────────────────────────

def read_table(table: str, lake_dir: Path | None = None) -> pd.DataFrame:
    """Union of every partition of `table` (empty DataFrame if none yet)."""
    d = table_dir(table, lake_dir)
    parts = sorted(d.glob("*.parquet"))
    if not parts:
        return pd.DataFrame()
    return pd.concat((pd.read_parquet(p) for p in parts), ignore_index=True)


def list_partitions(table: str, lake_dir: Path | None = None) -> list[str]:
    d = table_dir(table, lake_dir)
    return [p.name for p in sorted(d.glob("*.parquet"))]


def connect(lake_dir: Path | None = None):
    """A DuckDB connection with a view per non-empty table (globs partitions).

    Ad-hoc SQL over the lake:
        con = lake.connect()
        con.sql("SELECT sector_id, composite FROM sector_snapshot ORDER BY composite DESC")
    """
    import duckdb

    con = duckdb.connect()
    base = lake_dir or _LAKE_DIR
    for table in TABLES:
        d = table_dir(table, lake_dir)
        if any(d.glob("*.parquet")):
            glob = str(d / "*.parquet").replace("\\", "/")
            con.execute(
                f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{glob}', union_by_name=true)"
            )
    return con


# ── Seed from the legacy data/history exports ────────────────────────────────

def seed_from_history(lake_dir: Path | None = None) -> dict[str, int]:
    """One-off: migrate the legacy flat data/history/*.parquet exports into the
    partitioned lake. Split the run-keyed tables by run_id. Idempotent (overwrite)."""
    written: dict[str, int] = {}
    for name in ("score_run", "sector_snapshot", "rank_event", "report"):
        src = _HISTORY_DIR / f"{name}.parquet"
        if not src.exists():
            continue
        df = pd.read_parquet(src)
        if df.empty:
            continue
        _, keys = _resolve(name)
        if keys == ["run_id"] and "run_id" in df.columns:
            n = 0
            for run_id, group in df.groupby(df["run_id"].fillna("unassigned")):
                append_partition(name, group, {"run_id": run_id},
                                 overwrite=True, lake_dir=lake_dir)
                n += 1
            written[name] = n
        else:
            append_partition(name, df, {}, overwrite=True, lake_dir=lake_dir)
            written[name] = 1
    return written


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX parquet lake (Tier 2 source of truth)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tables", help="List known lake tables and their partition keys")

    lsp = sub.add_parser("ls", help="List partition files of a table")
    lsp.add_argument("table")

    rd = sub.add_parser("read", help="Read a table (union of partitions)")
    rd.add_argument("table")
    rd.add_argument("--limit", type=int, default=20)

    sub.add_parser("seed-from-history", help="Migrate data/history/*.parquet into the lake")

    args = p.parse_args()

    if args.cmd == "tables":
        for t, (rel, keys) in sorted(TABLES.items()):
            n = len(list_partitions(t))
            print(f"  {t:<20} {rel:<32} keys={keys or '[]'}  partitions={n}")
    elif args.cmd == "ls":
        parts = list_partitions(args.table)
        if not parts:
            print(f"  (no partitions yet for {args.table})")
        for name in parts:
            print(f"  {name}")
    elif args.cmd == "read":
        df = read_table(args.table)
        if df.empty:
            print(f"  (empty: {args.table})")
        else:
            print(f"  {len(df)} rows, {len(df.columns)} cols")
            with pd.option_context("display.max_columns", None, "display.width", 200):
                print(df.head(args.limit).to_string(index=False))
    elif args.cmd == "seed-from-history":
        out = seed_from_history()
        if not out:
            print("  nothing to seed (data/history/*.parquet not found)")
        for table, n in out.items():
            print(f"  {table:<20} → {n} partition(s)")


if __name__ == "__main__":
    main()
