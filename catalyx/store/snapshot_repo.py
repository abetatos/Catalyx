"""Score history store — the append-only spine for validating past analyses.

Four tables (all append-only; nothing is ever overwritten):

  score_run        one row per scoring run. Carries `scoring_version` (hash of
                   scoring_weights.yaml) + git commit so scores are comparable and
                   reproducible across formula changes.
  sector_snapshot  one row per sector per run: the 5 dimensions + composite + rank +
                   primary ETF + `rationale_md` (the narrative block for top-N sectors).
  rank_event       derived diff vs the previous run: which sectors entered/exited the
                   top-N and how far each moved ("algunos salen, entran").
  report           one row per generated markdown report, linked to its run.

Why this exists: reports (.md) are human-readable but not queryable; the DB repos hold
only current state (import overwrites). Nothing persisted the COMPUTED scores over time,
so past analyses could not be validated. This module is that missing primitive — and the
foundation of the Phase 3 feedback loop.

CLI:
    uv run python -m catalyx.store.snapshot_repo record [--top-n 10] [--notes "..."]
    uv run python -m catalyx.store.snapshot_repo history <sector_id>
    uv run python -m catalyx.store.snapshot_repo events [--run-id ...]
    uv run python -m catalyx.store.snapshot_repo runs
    uv run python -m catalyx.store.snapshot_repo register-report <path> --type heatmap
    uv run python -m catalyx.store.snapshot_repo export [--out data/history]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, select

from catalyx.config import weights
from catalyx.store.db import Base, get_engine, get_session

_REPO_ROOT = Path(__file__).parents[2]
_WEIGHTS_PATH = _REPO_ROOT / "catalyx" / "config" / "scoring_weights.yaml"
_ETF_UNIVERSE = _REPO_ROOT / "catalyx" / "config" / "sector_taxonomy.yaml"
_ETF_PATH = _REPO_ROOT / "catalyx" / "config" / "etf_universe.yaml"
_STUDY_DIR = _REPO_ROOT / "data" / "sector_studies"
_BLOCKS_DIR = _REPO_ROOT / "data" / "reports" / "heatmap_blocks"
_HISTORY_DIR = _REPO_ROOT / "data" / "history"


# ── Models ───────────────────────────────────────────────────────────────────

class ScoreRun(Base):
    __tablename__ = "score_run"
    run_id = Column(String(40), primary_key=True)
    run_at = Column(DateTime(timezone=True), nullable=False)
    scoring_version = Column(String(32), nullable=False)  # md5(scoring_weights.yaml)[:12]
    git_commit = Column(String(40))
    momentum_snapshot = Column(String(120))
    sector_count = Column(Integer)
    notes = Column(Text)


class SectorSnapshot(Base):
    __tablename__ = "sector_snapshot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(40), index=True, nullable=False)
    snapshot_at = Column(DateTime(timezone=True), nullable=False)
    sector_id = Column(String(80), index=True, nullable=False)
    rank = Column(Integer)
    composite = Column(Float)
    catalyst_alignment = Column(Float)
    momentum = Column(Float)
    flow_confirmation = Column(Float)
    valuation_relative = Column(Float)
    crowding_risk = Column(Float)
    narrative_maturity = Column(String(20))
    has_study = Column(Integer)  # 1/0
    primary_etf = Column(String(20))
    etf_price = Column(Float)
    price_date = Column(String(10))
    scoring_version = Column(String(32))
    rationale_md = Column(Text)  # narrative block for top-N sectors; null otherwise


class RankEvent(Base):
    __tablename__ = "rank_event"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(40), index=True, nullable=False)
    prev_run_id = Column(String(40))
    sector_id = Column(String(80), index=True, nullable=False)
    event_type = Column(String(20))  # entered_topN | exited_topN | rank_up | rank_down | new
    from_rank = Column(Integer)
    to_rank = Column(Integer)
    delta = Column(Integer)  # from_rank - to_rank (positive = moved up)
    top_n = Column(Integer)
    created_at = Column(DateTime(timezone=True))


class ReportRecord(Base):
    __tablename__ = "report"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(40), index=True)
    report_type = Column(String(40))
    report_date = Column(String(10))
    path = Column(String(300))
    content_md = Column(Text)
    created_at = Column(DateTime(timezone=True))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _scoring_version() -> str:
    try:
        return hashlib.md5(_WEIGHTS_PATH.read_bytes()).hexdigest()[:12]
    except FileNotFoundError:
        return "unknown"


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def _narrative_maturity(sector_id: str) -> str | None:
    p = _STUDY_DIR / f"study_{sector_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("narrative_maturity")
    except Exception:
        return None


def _crowding_for(maturity: str | None) -> float:
    if maturity is None:
        return 35.0  # Phase 0.5 default when no study
    return float(weights.crowding_from_maturity().get(maturity, 35))


def _primary_etf(sector_id: str) -> str | None:
    """Best primary ETF ticker for a sector: prefer UCITS + recommendation_tier 1."""
    try:
        data = yaml.safe_load(_ETF_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return None
    sectors = data.get("etf_universe", data)  # sectors are nested under the `etf_universe:` key
    etfs = sectors.get(sector_id) or []
    if not isinstance(etfs, list) or not etfs:
        return None
    ucits = [e for e in etfs if e.get("ucits")]
    pool = ucits or etfs
    pool = sorted(pool, key=lambda e: e.get("recommendation_tier", 9))
    return pool[0].get("ticker")


def _rationale_md(sector_id: str) -> str | None:
    p = _BLOCKS_DIR / f"{sector_id}.md"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return None


# ── Record a run ─────────────────────────────────────────────────────────────

def record_run(top_n: int = 10, notes: str | None = None,
               momentum_snapshot_path: Path | None = None) -> dict:
    """Score every investable sector, persist the run + per-sector snapshots +
    rank-change events vs the previous run. Append-only. Returns a summary dict."""
    # Imported here to avoid a heavy import at module load
    from catalyx.scorer.sector_scorer import _investable_sector_ids, score_sector

    now = datetime.now(timezone.utc)
    run_id = "run_" + now.strftime("%Y%m%d_%H%M%S")
    version = _scoring_version()

    sector_ids = _investable_sector_ids()
    rows = []
    for sid in sector_ids:
        maturity = _narrative_maturity(sid)
        crowd = _crowding_for(maturity)
        r = score_sector(sid, crowding_risk=crowd,
                         momentum_snapshot_path=momentum_snapshot_path)
        sb = r["score_breakdown"]
        rows.append({
            "sector_id": sid,
            "composite": r["composite"],
            "catalyst_alignment": sb["catalyst_alignment"],
            "momentum": sb["momentum"],
            "flow_confirmation": sb["flow_confirmation"],
            "valuation_relative": sb["valuation_relative"],
            "crowding_risk": sb["crowding_risk"],
            "narrative_maturity": maturity,
            "has_study": 1 if maturity is not None else 0,
            "primary_etf": _primary_etf(sid),
            "rationale_md": _rationale_md(sid),
        })

    # Rank by composite descending
    rows.sort(key=lambda x: -x["composite"])
    for i, row in enumerate(rows, 1):
        row["rank"] = i

    session = get_session()
    try:
        # Find the previous run (for rank-event diffing) BEFORE inserting this one
        prev = session.execute(
            select(ScoreRun).order_by(ScoreRun.run_at.desc())
        ).scalars().first()
        prev_run_id = prev.run_id if prev else None
        prev_ranks = {}
        if prev_run_id:
            for s in session.execute(
                select(SectorSnapshot).where(SectorSnapshot.run_id == prev_run_id)
            ).scalars():
                prev_ranks[s.sector_id] = s.rank

        # Insert run
        session.add(ScoreRun(
            run_id=run_id, run_at=now, scoring_version=version,
            git_commit=_git_commit(),
            momentum_snapshot=str(momentum_snapshot_path) if momentum_snapshot_path else None,
            sector_count=len(rows), notes=notes,
        ))

        # Insert snapshots
        for row in rows:
            session.add(SectorSnapshot(
                run_id=run_id, snapshot_at=now, scoring_version=version, **row,
            ))

        # Derive rank events vs previous run
        events = []
        if prev_ranks:
            for row in rows:
                sid = row["sector_id"]
                to_rank = row["rank"]
                from_rank = prev_ranks.get(sid)
                if from_rank is None:
                    events.append((sid, "new", None, to_rank, None))
                    if to_rank <= top_n:
                        events.append((sid, "entered_topN", None, to_rank, None))
                    continue
                delta = from_rank - to_rank  # positive = moved up
                was_top = from_rank <= top_n
                is_top = to_rank <= top_n
                if is_top and not was_top:
                    events.append((sid, "entered_topN", from_rank, to_rank, delta))
                elif was_top and not is_top:
                    events.append((sid, "exited_topN", from_rank, to_rank, delta))
                elif abs(delta) >= 3:
                    events.append((sid, "rank_up" if delta > 0 else "rank_down",
                                   from_rank, to_rank, delta))
            for sid, etype, fr, tr, d in events:
                session.add(RankEvent(
                    run_id=run_id, prev_run_id=prev_run_id, sector_id=sid,
                    event_type=etype, from_rank=fr, to_rank=tr, delta=d,
                    top_n=top_n, created_at=now,
                ))

        session.commit()
        # Parquet-first: the lake is the durable source of truth; SQLite is a cache.
        _write_run_to_lake(run_id, now, version, _git_commit(),
                           str(momentum_snapshot_path) if momentum_snapshot_path else None,
                           rows, events, prev_run_id, top_n, notes)
    finally:
        session.close()

    return {
        "run_id": run_id,
        "scoring_version": version,
        "sector_count": len(rows),
        "prev_run_id": prev_run_id,
        "top_n": top_n,
        "top": [(r["rank"], r["sector_id"], r["composite"]) for r in rows[:top_n]],
    }


def register_report(path: str, report_type: str, run_id: str | None = None,
                    store_text: bool = True) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = _REPO_ROOT / p
    content = p.read_text(encoding="utf-8") if (store_text and p.exists()) else None
    # report_date from filename suffix YYYYMMDD if present
    stem = p.stem
    rdate = None
    for tok in stem.replace("-", "_").split("_"):
        if len(tok) == 8 and tok.isdigit():
            rdate = f"{tok[:4]}-{tok[4:6]}-{tok[6:]}"
            break
    session = get_session()
    try:
        if run_id is None:
            last = session.execute(
                select(ScoreRun).order_by(ScoreRun.run_at.desc())
            ).scalars().first()
            run_id = last.run_id if last else None
        rec = ReportRecord(
            run_id=run_id, report_type=report_type, report_date=rdate,
            path=str(p.relative_to(_REPO_ROOT)) if p.is_relative_to(_REPO_ROOT) else str(p),
            content_md=content, created_at=datetime.now(timezone.utc),
        )
        session.add(rec)
        session.commit()
        result = {"report_type": report_type, "run_id": run_id, "date": rdate, "path": rec.path}
    finally:
        session.close()

    # Parquet-first: append the report row to the lake (durable, git-committed truth).
    try:
        import pandas as pd

        from catalyx.store import lake
        lake.append_partition("report", pd.DataFrame([{
            "run_id": run_id, "report_type": report_type, "report_date": rdate,
            "path": result["path"], "content_md": content,
            "created_at": datetime.now(timezone.utc),
        }]))
    except Exception:  # noqa: BLE001 — lake write is best-effort during migration
        pass
    return result


# ── Queries ──────────────────────────────────────────────────────────────────

def history(sector_id: str) -> list[dict]:
    """Parquet-first read (Tier 2). SQLite is only a cache now."""
    from catalyx.store import lake

    df = lake.read_table("sector_snapshot")
    if df.empty:
        return []
    df = df[df["sector_id"] == sector_id].sort_values("run_id")
    out = []
    for _, s in df.iterrows():
        sa = s.get("snapshot_at")
        out.append({
            "run_id": s["run_id"],
            "date": sa.date().isoformat() if hasattr(sa, "date") else str(sa)[:10],
            "rank": s.get("rank"), "composite": s.get("composite"),
            "catalyst_alignment": s.get("catalyst_alignment"), "momentum": s.get("momentum"),
            "scoring_version": s.get("scoring_version"),
        })
    return out


def list_runs() -> list[dict]:
    from catalyx.store import lake

    df = lake.read_table("score_run")
    if df.empty:
        return []
    df = df.sort_values("run_id")
    out = []
    for _, r in df.iterrows():
        ra = r.get("run_at")
        out.append({
            "run_id": r["run_id"], "at": ra.isoformat() if hasattr(ra, "isoformat") else str(ra),
            "version": r.get("scoring_version"), "git": r.get("git_commit"),
            "sectors": r.get("sector_count"),
        })
    return out


def rank_events(run_id: str | None = None) -> list[dict]:
    from catalyx.store import lake

    df = lake.read_table("rank_event")
    if df.empty:
        return []
    if run_id:
        df = df[df["run_id"] == run_id]
    else:
        df = df.sort_values("created_at", ascending=False)
    return [
        {"run_id": e["run_id"], "sector_id": e["sector_id"], "event": e.get("event_type"),
         "from": e.get("from_rank"), "to": e.get("to_rank"), "delta": e.get("delta")}
        for _, e in df.iterrows()
    ]


# ── Validation: did past scores predict forward ETF returns? ─────────────────

def validate_run(run_id: str | None = None, as_of: str | None = None,
                 top_n: int = 10) -> dict:
    """Measure whether a past run's composite ranking predicted forward ETF returns.

    For each sector's `primary_etf`, fetch the return from the run date to `as_of`
    (default today) via yfinance, then compute:
      - rank_ic: Spearman correlation between composite and forward return (the headline
        "were we right" number; >0 means higher-scored sectors did better).
      - topN_vs_rest: mean forward return of the top-N minus the rest (the tradable spread).

    Needs a run dated meaningfully in the past — within the same day it is ~noise.
    """
    import pandas as pd
    import yfinance as yf

    session = get_session()
    try:
        if run_id is None:
            run = session.execute(select(ScoreRun).order_by(ScoreRun.run_at)).scalars().first()
        else:
            run = session.get(ScoreRun, run_id)
        if run is None:
            return {"error": "no run found"}
        snaps = list(session.execute(
            select(SectorSnapshot).where(SectorSnapshot.run_id == run.run_id)
        ).scalars())
    finally:
        session.close()

    start = run.run_at.date().isoformat()
    end = as_of or datetime.now(timezone.utc).date().isoformat()
    rows = [(s.sector_id, s.primary_etf, s.composite, s.rank) for s in snaps if s.primary_etf]
    tickers = sorted({t for _, t, _, _ in rows})
    if not tickers:
        return {"error": "no primary_etf stored in this run"}

    # Fetch forward returns (best-effort; skip tickers yfinance can't resolve)
    fwd = {}
    data = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=True)
    closes = data["Close"] if "Close" in data else data
    for t in tickers:
        try:
            ser = closes[t].dropna() if hasattr(closes, "columns") else closes.dropna()
            if len(ser) >= 2:
                fwd[t] = float(ser.iloc[-1] / ser.iloc[0] - 1.0)
        except Exception:
            pass

    recs = [{"sector_id": sid, "etf": t, "composite": c, "rank": rk, "fwd_return": fwd[t]}
            for sid, t, c, rk in rows if t in fwd]
    if len(recs) < 3:
        return {"run_id": run.run_id, "start": start, "end": end,
                "error": f"only {len(recs)} sectors had usable forward returns"}

    df = pd.DataFrame(recs)
    rank_ic = float(df["composite"].corr(df["fwd_return"], method="spearman"))
    top = df.nsmallest(top_n, "rank")["fwd_return"].mean()
    rest = df[~df.index.isin(df.nsmallest(top_n, "rank").index)]["fwd_return"].mean()
    return {
        "run_id": run.run_id, "scoring_version": run.scoring_version,
        "start": start, "end": end, "sectors_evaluated": len(recs),
        "rank_ic": round(rank_ic, 3),
        "topN_mean_return": round(float(top), 4),
        "rest_mean_return": round(float(rest), 4),
        "topN_minus_rest": round(float(top - rest), 4),
    }


# ── Parquet lake: write-through (record_run) + rebuild (SQLite ← lake) ────────

def _write_run_to_lake(run_id, run_at, version, git_commit, momentum_snapshot,
                       rows, events, prev_run_id, top_n, notes) -> None:
    """Write a run's score_run / sector_snapshot / rank_event partitions to the lake.

    Append-only, one partition per run (keyed by run_id). This is the durable, git-
    committed source of truth; the SQLite rows written by `record_run` are a cache.
    """
    import pandas as pd

    from catalyx.store import lake

    lake.append_partition("score_run", pd.DataFrame([{
        "run_id": run_id, "run_at": run_at, "scoring_version": version,
        "git_commit": git_commit, "momentum_snapshot": momentum_snapshot,
        "sector_count": len(rows), "notes": notes,
    }]), {"run_id": run_id}, overwrite=True)

    snap_rows = [{
        "run_id": run_id, "snapshot_at": run_at, "scoring_version": version,
        "sector_id": r["sector_id"], "rank": r["rank"], "composite": r["composite"],
        "catalyst_alignment": r["catalyst_alignment"], "momentum": r["momentum"],
        "flow_confirmation": r["flow_confirmation"], "valuation_relative": r["valuation_relative"],
        "crowding_risk": r["crowding_risk"], "narrative_maturity": r["narrative_maturity"],
        "has_study": r["has_study"], "primary_etf": r["primary_etf"], "rationale_md": r["rationale_md"],
    } for r in rows]
    lake.append_partition("sector_snapshot", pd.DataFrame(snap_rows),
                          {"run_id": run_id}, overwrite=True)

    if events:
        ev_rows = [{
            "run_id": run_id, "prev_run_id": prev_run_id, "sector_id": sid,
            "event_type": etype, "from_rank": fr, "to_rank": tr, "delta": d,
            "top_n": top_n, "created_at": run_at,
        } for sid, etype, fr, tr, d in events]
        lake.append_partition("rank_event", pd.DataFrame(ev_rows),
                              {"run_id": run_id}, overwrite=True)


# tables the SQLite cache mirrors from the lake (run-keyed history)
_CACHE_MODELS = {
    "score_run": ScoreRun, "sector_snapshot": SectorSnapshot,
    "rank_event": RankEvent, "report": ReportRecord,
}


def rebuild_from_lake() -> dict:
    """Reconstruct the SQLite query cache from the parquet lake (Tier 2 truth).

    Wipes and repopulates the run-history tables from data/lake/. Use after a fresh
    clone, after `git pull`, or whenever the .db is missing — the lake is the truth,
    SQLite is disposable.
    """
    import pandas as pd

    from catalyx.store import lake

    engine = get_engine()
    Base.metadata.create_all(engine)
    session = get_session()
    summary: dict[str, int] = {}
    try:
        for table, model in _CACHE_MODELS.items():
            df = lake.read_table(table)
            session.query(model).delete()
            cols = {c.name for c in model.__table__.columns}
            n = 0
            for rec in df.to_dict(orient="records"):
                kwargs = {}
                for k, v in rec.items():
                    if k not in cols:
                        continue
                    if isinstance(v, float) and pd.isna(v):
                        v = None
                    elif isinstance(v, pd.Timestamp):
                        v = v.to_pydatetime()
                    kwargs[k] = v
                session.add(model(**kwargs))
                n += 1
            summary[table] = n
        session.commit()
    finally:
        session.close()
    return summary


def export(out_dir: Path | None = None) -> dict:
    """DEPRECATED (kept one version): flat data/history/*.parquet export from SQLite.

    Superseded by the partitioned lake under data/lake/ (written through by `record_run`).
    Prefer `rebuild` (lake → SQLite). This remains only for back-compat with skills that
    still call `export`; it will be removed once those are updated.
    """
    import pandas as pd
    out = out_dir or _HISTORY_DIR
    out.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    written = {}
    for table in ("score_run", "sector_snapshot", "rank_event", "report"):
        df = pd.read_sql_table(table, engine)
        try:
            fp = out / f"{table}.parquet"
            df.to_parquet(fp, index=False)
        except Exception:
            fp = out / f"{table}.csv"
            df.to_csv(fp, index=False)
        written[table] = (str(fp.relative_to(_REPO_ROOT)), len(df))
    return written


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX score-history store")
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="Score all sectors and persist a run")
    rec.add_argument("--top-n", type=int, default=10)
    rec.add_argument("--notes", default=None)

    h = sub.add_parser("history", help="Score history for a sector")
    h.add_argument("sector_id")

    sub.add_parser("runs", help="List all score runs")

    ev = sub.add_parser("events", help="Rank-change events")
    ev.add_argument("--run-id", default=None)

    rr = sub.add_parser("register-report", help="Register a markdown report")
    rr.add_argument("path")
    rr.add_argument("--type", required=True, dest="report_type")
    rr.add_argument("--run-id", default=None)

    ex = sub.add_parser("export", help="[deprecated] Export SQLite tables to flat data/history/*.parquet")
    ex.add_argument("--out", type=Path, default=None)

    sub.add_parser("rebuild", help="Rebuild the SQLite cache from the parquet lake (lake → SQLite)")

    va = sub.add_parser("validate", help="Did a past run's ranking predict forward ETF returns?")
    va.add_argument("--run-id", default=None)
    va.add_argument("--as-of", default=None)
    va.add_argument("--top-n", type=int, default=10)

    args = p.parse_args()

    if args.cmd == "record":
        r = record_run(top_n=args.top_n, notes=args.notes)
        print(f"Recorded {r['run_id']} (version {r['scoring_version']}, {r['sector_count']} sectors)")
        if r["prev_run_id"]:
            print(f"  diffed vs {r['prev_run_id']}")
        print("  top:")
        for rank, sid, comp in r["top"]:
            print(f"    #{rank:<2} {sid:<36} {comp}")
    elif args.cmd == "history":
        for row in history(args.sector_id):
            print(f"  {row['date']}  rank #{row['rank']:<3} composite={row['composite']:<6} "
                  f"cat={row['catalyst_alignment']:<6} mom={row['momentum']:<6} v={row['scoring_version']}")
    elif args.cmd == "runs":
        for row in list_runs():
            print(f"  {row['run_id']}  {row['at']}  v={row['version']}  git={row['git']}  sectors={row['sectors']}")
    elif args.cmd == "events":
        for e in rank_events(args.run_id):
            arrow = f"#{e['from']}→#{e['to']}" if e['from'] else f"→#{e['to']}"
            print(f"  {e['sector_id']:<36} {e['event']:<13} {arrow}  (Δ{e['delta']})")
    elif args.cmd == "register-report":
        r = register_report(args.path, args.report_type, run_id=args.run_id)
        print(f"Registered {r['report_type']} report ({r['date']}) → run {r['run_id']}")
    elif args.cmd == "export":
        for table, (fp, n) in export(args.out).items():
            print(f"  {table:<16} → {fp}  ({n} rows)")
    elif args.cmd == "rebuild":
        for table, n in rebuild_from_lake().items():
            print(f"  {table:<16} ← lake  ({n} rows)")
    elif args.cmd == "validate":
        r = validate_run(run_id=args.run_id, as_of=args.as_of, top_n=args.top_n)
        if "error" in r:
            print(f"  {r.get('run_id','?')}: {r['error']}")
        else:
            print(f"  run {r['run_id']} (v{r['scoring_version']})  {r['start']} → {r['end']}")
            print(f"  sectors evaluated: {r['sectors_evaluated']}")
            print(f"  rank IC (composite vs forward return): {r['rank_ic']}")
            print(f"  top-{args.top_n} mean return: {r['topN_mean_return']:+.2%}  "
                  f"rest: {r['rest_mean_return']:+.2%}  spread: {r['topN_minus_rest']:+.2%}")


if __name__ == "__main__":
    main()
