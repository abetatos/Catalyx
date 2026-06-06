"""Score history store — the append-only spine for validating past analyses.

The parquet lake (data/lake/, Tier 2) is the single source of truth. Four logical
tables, all append-only (nothing is ever overwritten):

  score_run        one row per scoring run. Carries `scoring_version` (hash of
                   scoring_weights.yaml) + git commit so scores are comparable and
                   reproducible across formula changes.
  sector_snapshot  one row per sector per run: the 5 dimensions + composite + rank +
                   primary ETF + `rationale_md` (the narrative block for top-N sectors).
  rank_event       derived diff vs the previous run: which sectors entered/exited the
                   top-N and how far each moved ("algunos salen, entran").
  report           one row per generated markdown report, linked to its run.

Why this exists: reports (.md) are human-readable but not queryable; nothing else
persisted the COMPUTED scores over time, so past analyses could not be validated. This
module is that missing primitive — and the foundation of the Phase 3 feedback loop.

All reads/writes go through `catalyx.store.lake`. There is no database.

CLI:
    uv run python -m catalyx.store.snapshot_repo record [--top-n 10] [--notes "..."]
    uv run python -m catalyx.store.snapshot_repo history <sector_id>
    uv run python -m catalyx.store.snapshot_repo events [--run-id ...]
    uv run python -m catalyx.store.snapshot_repo runs
    uv run python -m catalyx.store.snapshot_repo register-report <path> --type heatmap
    uv run python -m catalyx.store.snapshot_repo validate [--run-id ...] [--as-of ...]
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

from catalyx.config import weights

_REPO_ROOT = Path(__file__).parents[2]
_WEIGHTS_PATH = _REPO_ROOT / "catalyx" / "config" / "scoring_weights.yaml"
_ETF_PATH = _REPO_ROOT / "catalyx" / "config" / "etf_universe.yaml"
_STUDY_DIR = _REPO_ROOT / "data" / "sector_studies"
_BLOCKS_DIR = _REPO_ROOT / "data" / "reports" / "heatmap_blocks"
_EVENT_CAT_DIR = _REPO_ROOT / "data" / "catalysts"


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


def _date10(v) -> str | None:
    """First 10 chars (YYYY-MM-DD) of a datetime/ISO-string, or None."""
    if v is None:
        return None
    s = v.isoformat() if hasattr(v, "isoformat") else str(v)
    return s[:10] or None


def _new_catalysts_in_window(lo, hi) -> list[dict]:
    """Event catalysts detected in (lo, hi] — the 'what surfaced since the last run' signal.

    lo/hi are datetimes (lo may be None = first run, then nothing is "new"). Compared at
    day granularity on detected_at (falling back to created_at)."""
    if hi is None or lo is None:
        return []
    lo_s, hi_s = _date10(lo), _date10(hi)
    out: list[dict] = []
    if not _EVENT_CAT_DIR.exists():
        return out
    for f in sorted(_EVENT_CAT_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        dt = _date10(d.get("detected_at") or d.get("created_at") or "")
        if dt and lo_s < dt <= hi_s:
            out.append({"id": d.get("id", f.stem), "catalyst_type": d.get("catalyst_type"),
                        "relation_to_structural": d.get("relation_to_structural"),
                        "strength_score": d.get("strength_score")})
    return out


def _run_summary(rows: list[dict], events: list, prev_ranks: dict, prev_comp: dict,
                 prev_run_at, run_at, top_n: int) -> dict:
    """A deterministic digest of WHAT CHANGED at this run vs the previous one — authored by
    the pipeline at run time so it travels with the run (not recomputed by the dashboard).

    Captures: biggest rank movers (▲/▼), top-N entries/exits, new event catalysts in the
    window, regime stress (contested/breaking counts), and composite breadth (up/down)."""
    movers = [{"sector_id": r["sector_id"], "from": prev_ranks[r["sector_id"]],
               "to": r["rank"], "delta": prev_ranks[r["sector_id"]] - r["rank"]}
              for r in rows if r["sector_id"] in prev_ranks and prev_ranks[r["sector_id"]] != r["rank"]]
    movers.sort(key=lambda m: -abs(m["delta"]))
    entered = [e[0] for e in events if e[1] == "entered_topN"]
    exited = [e[0] for e in events if e[1] == "exited_topN"]
    regime = {"contested": 0, "breaking": 0}
    for r in rows:
        st = r.get("regime_state")
        if st in regime:
            regime[st] += 1
    breadth = None
    if prev_comp:
        deltas = [r["composite"] - prev_comp[r["sector_id"]] for r in rows if r["sector_id"] in prev_comp]
        if deltas:
            up = sum(1 for d in deltas if d > 0.5)
            down = sum(1 for d in deltas if d < -0.5)
            breadth = {"up": up, "down": down, "flat": len(deltas) - up - down,
                       "mean_delta": round(sum(deltas) / len(deltas), 2)}
    return {
        "prev_run_id": None,  # filled by caller
        "movers_up": [m for m in movers if m["delta"] > 0][:5],
        "movers_down": [m for m in movers if m["delta"] < 0][:5],
        "entered": entered[:8],
        "exited": exited[:8],
        "new_catalysts": _new_catalysts_in_window(prev_run_at, run_at),
        "regime": regime,
        "breadth": breadth,
        "top_sectors": [r["sector_id"] for r in sorted(rows, key=lambda x: x["rank"])[:5]],
    }


def _latest_run_id() -> str | None:
    """Most recent run_id from the lake (None if no runs yet)."""
    from catalyx.store import lake

    df = lake.read_table("score_run")
    if df.empty:
        return None
    # run_id format run_YYYYMMDD_HHMMSS sorts chronologically as a string
    return str(df.sort_values("run_id")["run_id"].iloc[-1])


# ── Record a run ─────────────────────────────────────────────────────────────

def record_run(top_n: int = 10, notes: str | None = None,
               momentum_snapshot_path: Path | None = None) -> dict:
    """Score every investable sector, persist the run + per-sector snapshots +
    rank-change events vs the previous run to the lake. Append-only. Returns a summary."""
    # Imported here to avoid a heavy import at module load
    from catalyx.store import lake
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
        cat_detail = r.get("catalyst_detail") or {}
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
            # noise-vs-regime annotation (intact/contested/breaking). Additive — does not
            # affect composite. See docs/DESIGN_catalyst_regime_discrimination.md.
            "regime_state": cat_detail.get("regime_state", "intact"),
        })

    # Rank by composite descending
    rows.sort(key=lambda x: -x["composite"])
    for i, row in enumerate(rows, 1):
        row["rank"] = i

    # Find the previous run (for rank-event diffing + the change summary) BEFORE writing this one
    prev_run_id = _latest_run_id()
    prev_ranks: dict[str, int] = {}
    prev_comp: dict[str, float] = {}
    prev_run_at = None
    if prev_run_id:
        snaps = lake.read_table("sector_snapshot")
        if not snaps.empty:
            prev = snaps[snaps["run_id"] == prev_run_id]
            prev_ranks = {row["sector_id"]: row["rank"] for _, row in prev.iterrows()}
            prev_comp = {row["sector_id"]: row["composite"] for _, row in prev.iterrows()}
        runs_df = lake.read_table("score_run")
        if not runs_df.empty:
            m = runs_df[runs_df["run_id"] == prev_run_id]
            if not m.empty:
                prev_run_at = m.iloc[0].get("run_at")

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

    summary = _run_summary(rows, events, prev_ranks, prev_comp, prev_run_at, now, top_n)
    summary["prev_run_id"] = prev_run_id

    _write_run_to_lake(run_id, now, version, _git_commit(),
                       str(momentum_snapshot_path) if momentum_snapshot_path else None,
                       rows, events, prev_run_id, top_n, notes, summary)

    return {
        "run_id": run_id,
        "scoring_version": version,
        "sector_count": len(rows),
        "prev_run_id": prev_run_id,
        "top_n": top_n,
        "top": [(r["rank"], r["sector_id"], r["composite"]) for r in rows[:top_n]],
    }


def backfill_summaries() -> int:
    """Recompute + store the change-`summary` for every existing run from the lake (one-off
    for runs recorded before summaries existed). Reconstructs each run's rows/events from
    sector_snapshot + rank_event and rewrites only the score_run partition. Idempotent."""
    import pandas as pd

    from catalyx.store import lake

    runs = lake.read_table("score_run")
    if runs.empty:
        return 0
    runs = runs.sort_values("run_id")
    snaps = lake.read_table("sector_snapshot")
    revs = lake.read_table("rank_event")
    ids = runs["run_id"].tolist()
    n = 0
    for i, rid in enumerate(ids):
        cur = snaps[snaps["run_id"] == rid] if not snaps.empty else snaps
        rows = [{"sector_id": r["sector_id"], "rank": int(r["rank"]), "composite": r["composite"],
                 "regime_state": r.get("regime_state", "intact")} for _, r in cur.iterrows()]
        prev_rid = ids[i - 1] if i > 0 else None
        prev_ranks, prev_comp, prev_at = {}, {}, None
        if prev_rid:
            pv = snaps[snaps["run_id"] == prev_rid]
            prev_ranks = {r["sector_id"]: int(r["rank"]) for _, r in pv.iterrows()}
            prev_comp = {r["sector_id"]: r["composite"] for _, r in pv.iterrows()}
            pm = runs[runs["run_id"] == prev_rid]
            if not pm.empty:
                prev_at = pm.iloc[0].get("run_at")
        events = []
        if not revs.empty:
            for _, e in revs[revs["run_id"] == rid].iterrows():
                events.append((e["sector_id"], e.get("event_type"), e.get("from_rank"),
                               e.get("to_rank"), e.get("delta")))
        run_row = runs[runs["run_id"] == rid].iloc[0]
        summary = _run_summary(rows, events, prev_ranks, prev_comp, prev_at, run_row.get("run_at"), 10)
        summary["prev_run_id"] = prev_rid
        new_row = run_row.to_dict()
        new_row["summary"] = json.dumps(summary, default=str)
        lake.append_partition("score_run", pd.DataFrame([new_row]), {"run_id": rid}, overwrite=True)
        n += 1
    return n


def register_report(path: str, report_type: str, run_id: str | None = None,
                    store_text: bool = True) -> dict:
    import pandas as pd

    from catalyx.store import lake

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

    if run_id is None:
        run_id = _latest_run_id()

    rel_path = str(p.relative_to(_REPO_ROOT)) if p.is_relative_to(_REPO_ROOT) else str(p)
    lake.append_partition("report", pd.DataFrame([{
        "run_id": run_id, "report_type": report_type, "report_date": rdate,
        "path": rel_path, "content_md": content,
        "created_at": datetime.now(timezone.utc),
    }]))
    return {"report_type": report_type, "run_id": run_id, "date": rdate, "path": rel_path}


# ── Queries (parquet lake, Tier 2) ───────────────────────────────────────────

def history(sector_id: str) -> list[dict]:
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

    from catalyx.store import lake

    runs = lake.read_table("score_run")
    if runs.empty:
        return {"error": "no run found"}
    runs = runs.sort_values("run_id")
    if run_id is None:
        run = runs.iloc[0]  # earliest run = most forward history
    else:
        match = runs[runs["run_id"] == run_id]
        if match.empty:
            return {"error": "no run found"}
        run = match.iloc[0]
    run_id = str(run["run_id"])

    snaps_all = lake.read_table("sector_snapshot")
    snaps = snaps_all[snaps_all["run_id"] == run_id] if not snaps_all.empty else snaps_all
    if snaps.empty:
        return {"run_id": run_id, "error": "no snapshots for run"}

    run_at = run.get("run_at")
    start = run_at.date().isoformat() if hasattr(run_at, "date") else str(run_at)[:10]
    end = as_of or datetime.now(timezone.utc).date().isoformat()
    rows = [(s["sector_id"], s.get("primary_etf"), s.get("composite"), s.get("rank"))
            for _, s in snaps.iterrows() if s.get("primary_etf")]
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
        return {"run_id": run_id, "start": start, "end": end,
                "error": f"only {len(recs)} sectors had usable forward returns"}

    df = pd.DataFrame(recs)
    rank_ic = float(df["composite"].corr(df["fwd_return"], method="spearman"))
    top = df.nsmallest(top_n, "rank")["fwd_return"].mean()
    rest = df[~df.index.isin(df.nsmallest(top_n, "rank").index)]["fwd_return"].mean()
    return {
        "run_id": run_id, "scoring_version": run.get("scoring_version"),
        "start": start, "end": end, "sectors_evaluated": len(recs),
        "rank_ic": round(rank_ic, 3),
        "topN_mean_return": round(float(top), 4),
        "rest_mean_return": round(float(rest), 4),
        "topN_minus_rest": round(float(top - rest), 4),
    }


# ── Parquet lake write-through ───────────────────────────────────────────────

def _write_run_to_lake(run_id, run_at, version, git_commit, momentum_snapshot,
                       rows, events, prev_run_id, top_n, notes, summary=None) -> None:
    """Write a run's score_run / sector_snapshot / rank_event partitions to the lake.

    Append-only, one partition per run (keyed by run_id). The durable, git-committed
    source of truth. `summary` is a deterministic change-digest (JSON) authored here so it
    travels with the run — see `_run_summary`.
    """
    import pandas as pd

    from catalyx.store import lake

    lake.append_partition("score_run", pd.DataFrame([{
        "run_id": run_id, "run_at": run_at, "scoring_version": version,
        "git_commit": git_commit, "momentum_snapshot": momentum_snapshot,
        "sector_count": len(rows), "notes": notes,
        "summary": json.dumps(summary, default=str) if summary is not None else None,
    }]), {"run_id": run_id}, overwrite=True)

    snap_rows = [{
        "run_id": run_id, "snapshot_at": run_at, "scoring_version": version,
        "sector_id": r["sector_id"], "rank": r["rank"], "composite": r["composite"],
        "catalyst_alignment": r["catalyst_alignment"], "momentum": r["momentum"],
        "flow_confirmation": r["flow_confirmation"], "valuation_relative": r["valuation_relative"],
        "crowding_risk": r["crowding_risk"], "narrative_maturity": r["narrative_maturity"],
        "has_study": r["has_study"], "primary_etf": r["primary_etf"], "rationale_md": r["rationale_md"],
        "regime_state": r["regime_state"],
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


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX score-history store (parquet lake)")
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="Score all sectors and persist a run")
    rec.add_argument("--top-n", type=int, default=10)
    rec.add_argument("--notes", default=None)

    h = sub.add_parser("history", help="Score history for a sector")
    h.add_argument("sector_id")

    sub.add_parser("runs", help="List all score runs")
    sub.add_parser("backfill-summaries", help="(One-off) recompute the change-summary for every run")

    ev = sub.add_parser("events", help="Rank-change events")
    ev.add_argument("--run-id", default=None)

    rr = sub.add_parser("register-report", help="Register a markdown report")
    rr.add_argument("path")
    rr.add_argument("--type", required=True, dest="report_type")
    rr.add_argument("--run-id", default=None)

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
    elif args.cmd == "backfill-summaries":
        n = backfill_summaries()
        print(f"Backfilled change-summary for {n} run(s)")
    elif args.cmd == "events":
        for e in rank_events(args.run_id):
            arrow = f"#{e['from']}→#{e['to']}" if e['from'] else f"→#{e['to']}"
            print(f"  {e['sector_id']:<36} {e['event']:<13} {arrow}  (Δ{e['delta']})")
    elif args.cmd == "register-report":
        r = register_report(args.path, args.report_type, run_id=args.run_id)
        print(f"Registered {r['report_type']} report ({r['date']}) → run {r['run_id']}")
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
