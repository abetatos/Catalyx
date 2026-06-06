"""Build the static GitHub-Pages dashboard (Fase F).

Bakes the parquet lake into a self-contained `dist/`: copies the static frontend
(site/*) + every lake parquet, and writes `manifest.json` mapping each table to its
partition files. The page (DuckDB-WASM) reads `manifest.json`, registers the parquet,
and queries them in the browser — no backend, no DVC pull (the lake is committed to git).

Run locally to preview:
    uv run python scripts/build_site.py
    python -m http.server -d dist 8000   # → http://localhost:8000

The GitHub Actions workflow (.github/workflows/pages.yml) runs this and deploys `dist/`.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from catalyx.store import lake

_ROOT = Path(__file__).parents[1]
_SITE = _ROOT / "site"
_DIST = _ROOT / "dist"
_LAKE = _ROOT / "data" / "lake"
_STATIC = ("index.html", "app.js")

# Tier-1 documents (config/JSON) surfaced read-only in the dashboard alongside the lake.
_STRUCTURAL_CAT = _ROOT / "catalyx" / "config" / "structural_catalysts"
_EVENT_CAT = _ROOT / "data" / "catalysts"
_STUDIES = _ROOT / "data" / "sector_studies"
_THESES = _ROOT / "data" / "theses"


def _bake_docs(dist: Path) -> dict:
    """Bundle the Tier-1 documents into docs.json so the page can show the full picture:
    structural + event catalysts, sector studies, theses. Small (KB each) — one fetch."""
    docs: dict[str, list] = {"catalysts_structural": [], "catalysts_event": [],
                             "studies": [], "theses": []}
    for f in sorted(_STRUCTURAL_CAT.glob("*.yaml")):
        try:
            docs["catalysts_structural"].append(yaml.safe_load(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    for src, key in ((_EVENT_CAT, "catalysts_event"), (_STUDIES, "studies"), (_THESES, "theses")):
        if src.exists():
            for f in sorted(src.glob("*.json")):
                try:
                    docs[key].append(json.loads(f.read_text(encoding="utf-8")))
                except Exception:  # noqa: BLE001
                    pass
    (dist / "docs.json").write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
    return {k: len(v) for k, v in docs.items()}


def _records(df) -> list[dict]:
    """DataFrame → JSON-safe list of dicts (numpy → native, NaN → null, datetime → ISO)."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _downsample(values: list, n: int = 40) -> list:
    """Reduce a series to ~n evenly-spaced points (keeps first + last) for sparklines."""
    if len(values) <= n:
        return values
    step = (len(values) - 1) / (n - 1)
    idx = sorted({round(i * step) for i in range(n)} | {len(values) - 1})
    return [values[i] for i in idx]


def _series_metrics(navs: list) -> dict | None:
    """Annualized volatility, Sharpe (rf=0) and max drawdown from a NAV series."""
    import math
    xs = [v for v in navs if v is not None]
    if len(xs) < 3:
        return None
    rets = [xs[i] / xs[i - 1] - 1 for i in range(1, len(xs)) if xs[i - 1]]
    if not rets:
        return None
    n = len(rets)
    mean = sum(rets) / n
    sd = (sum((r - mean) ** 2 for r in rets) / n) ** 0.5
    peak, mdd = xs[0], 0.0
    for v in xs:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {
        "vol_pct": round(sd * math.sqrt(252) * 100, 1),
        "sharpe": round(mean / sd * math.sqrt(252), 2) if sd else None,
        "max_drawdown_pct": round(mdd * 100, 1),
    }


def _portfolio_configs() -> dict:
    """portfolio_id → config dict (name, description, construction) for the methodology panel."""
    out: dict = {}
    pdir = _ROOT / "catalyx" / "config" / "portfolios"
    for f in sorted(pdir.glob("*.yaml")):
        try:
            c = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            out[c.get("portfolio_id", f.stem)] = c
        except Exception:  # noqa: BLE001
            pass
    return out


def _bake_overview(dist: Path) -> dict:
    """Precompute the 'latest-state' AND every-run views into overview.json so the page's
    first paint — and switching the whole page to a historical run — both need zero
    DuckDB-WASM. Read-only over the lake; any absent table degrades to []/{} (mirrors the
    defensive `_has` guard in lake_query.py)."""
    con = lake.connect()

    def has(table: str) -> bool:
        return bool(lake.list_partitions(table))

    def q(sql: str) -> list[dict]:
        try:
            return _records(con.execute(sql).fetchdf())
        except Exception:  # noqa: BLE001 — defensive: missing table/column → empty
            return []

    ov: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    try:
        # ── run catalogue (newest first) — each carries a human `notes` summary ──
        runs = q(
            "SELECT run_id, substr(CAST(run_at AS VARCHAR),1,19) AS ts, scoring_version, "
            "git_commit, sector_count, notes FROM score_run ORDER BY run_id DESC"
        ) if has("score_run") else []
        ov["runs"] = runs
        ov["latest_run_id"] = runs[0]["run_id"] if runs else None

        # ── BAKE ONLY the latest run's snapshot (+ the previous run's ranks for movement
        #    deltas). Any OTHER run is loaded on demand from the lake in the browser
        #    (DuckDB-WASM, one partition per run_id). This keeps overview.json BOUNDED no
        #    matter how many runs accrue — the per-run data does not grow the first payload.
        def _run_holdings(rid: str) -> dict:
            out: dict = {}
            if not (rid and has("portfolio_holding")):
                return out
            for r in q(
                "SELECT portfolio_id, rank_in_portfolio, sector_id, primary_etf, weight_pct, "
                "composite, momentum, entry_price, narrative_maturity FROM portfolio_holding "
                f"WHERE run_id = '{rid}' ORDER BY portfolio_id, rank_in_portfolio"
            ):
                out.setdefault(r["portfolio_id"], []).append(
                    {k: v for k, v in r.items() if k != "portfolio_id"})
            return out

        latest_rid = ov["latest_run_id"]
        ov["latest"] = {"ranking": [], "rank_moves": [], "holdings": {}}
        ov["prev_ranking"] = []
        if latest_rid and has("sector_snapshot"):
            ov["latest"]["ranking"] = q(
                "SELECT sector_id, rank, composite, momentum, catalyst_alignment, crowding_risk, "
                f"narrative_maturity, primary_etf, regime_state FROM sector_snapshot "
                f"WHERE run_id = '{latest_rid}' ORDER BY rank")
            ov["latest"]["rank_moves"] = q(
                "SELECT sector_id, event_type, from_rank, to_rank, delta FROM rank_event "
                f"WHERE run_id = '{latest_rid}' ORDER BY abs(coalesce(delta, 99)) DESC"
            ) if has("rank_event") else []
            ov["latest"]["holdings"] = _run_holdings(latest_rid)
            prev_rid = runs[1]["run_id"] if len(runs) > 1 else None
            if prev_rid:
                ov["prev_ranking"] = q(
                    f"SELECT sector_id, rank FROM sector_snapshot WHERE run_id = '{prev_rid}'")

        # The most recent run that actually BUILT portfolios — so the Portfolios view is
        # populated by default even when the very latest score-run skipped portfolio build.
        ov["latest_holdings"] = {"run_id": None, "by_pid": {}}
        for r in runs:
            h = _run_holdings(r["run_id"])
            if h:
                ov["latest_holdings"] = {"run_id": r["run_id"], "by_pid": h}
                break

        # ── per-run summary: WHAT changed at each run (movers, top-N entries/exits, new
        #    catalysts in the window) so the runs timeline tells a story, not just a date.
        #    Computed at build time from full rankings; only the small digest is shipped.
        if runs and has("sector_snapshot"):
            run_rank = {}
            for rr in runs:
                rid = rr["run_id"]
                run_rank[rid] = {x["sector_id"]: x["rank"]
                                 for x in q(f"SELECT sector_id, rank FROM sector_snapshot WHERE run_id = '{rid}'")}
            events = []
            if _EVENT_CAT.exists():
                for f in sorted(_EVENT_CAT.glob("*.json")):
                    try:
                        d = json.loads(f.read_text(encoding="utf-8"))
                        events.append({"id": d.get("id", f.stem),
                                       "date": (d.get("detected_at") or d.get("created_at") or "")[:10]})
                    except Exception:  # noqa: BLE001
                        pass
            for i, rr in enumerate(runs):
                cur = run_rank.get(rr["run_id"], {})
                prev_run = runs[i + 1] if i + 1 < len(runs) else None
                pv = run_rank.get(prev_run["run_id"], {}) if prev_run else {}
                movers = sorted(((s, pv[s] - rk) for s, rk in cur.items() if s in pv and pv[s] != rk),
                                key=lambda x: -abs(x[1]))
                up = [{"sector_id": s, "delta": d} for s, d in movers if d > 0][:3]
                down = [{"sector_id": s, "delta": d} for s, d in movers if d < 0][:3]
                entered = [s for s, rk in cur.items() if rk <= 10 and (s not in pv or pv[s] > 10)] if pv else []
                exited = [s for s, rk in pv.items() if rk <= 10 and (s not in cur or cur[s] > 10)] if pv else []
                new_cats = []
                if prev_run:
                    lo, hi = prev_run["ts"][:10], rr["ts"][:10]
                    new_cats = [e["id"] for e in events if e["date"] and lo < e["date"] <= hi]
                rr["summary"] = {"movers_up": up, "movers_down": down,
                                 "entered": entered[:5], "exited": exited[:5], "new_catalysts": new_cats}

        # ── portfolios: NAV series + risk metrics + construction methodology (current) ──
        cfgs = _portfolio_configs()
        ov["portfolios"] = []
        if has("portfolio_nav"):
            for row in q("SELECT DISTINCT portfolio_id FROM portfolio_nav ORDER BY portfolio_id"):
                pid = row["portfolio_id"]
                series = q(
                    "SELECT date, nav, benchmark_nav, return_pct, vs_benchmark_pct, benchmark_etf, kind "
                    f"FROM portfolio_nav WHERE portfolio_id = '{pid}' ORDER BY date"
                )
                if not series:
                    continue
                last = series[-1]
                cfg = cfgs.get(pid, {})
                ov["portfolios"].append({
                    "portfolio_id": pid,
                    "name": cfg.get("name", pid),
                    "description": (cfg.get("description") or "").strip(),
                    "construction": cfg.get("construction"),
                    "kind": last.get("kind"),
                    "return_pct": last.get("return_pct"),
                    "vs_benchmark_pct": last.get("vs_benchmark_pct"),
                    "benchmark_etf": last.get("benchmark_etf"),
                    "metrics": _series_metrics([s["nav"] for s in series]),
                    "bench_metrics": _series_metrics([s["benchmark_nav"] for s in series]),
                    "n_days": len(series),
                    "nav": _downsample([s["nav"] for s in series]),
                    "benchmark_nav": _downsample([s["benchmark_nav"] for s in series]),
                })
            ov["portfolios"].sort(key=lambda p: (p.get("return_pct") is None, -(p.get("return_pct") or 0)))

        # ── dislocation lens (opportunities / diversifiers / regime watch) ──
        ov["dislocation"] = None
        if has("dislocation"):
            meta = q(
                "SELECT max(run_id) AS run_id FROM dislocation"
            )
            run_id = meta[0]["run_id"] if meta and meta[0].get("run_id") is not None else None
            if run_id is not None:
                m = q(
                    "SELECT DISTINCT window_days, benchmark, market_window_pct FROM dislocation "
                    f"WHERE run_id = '{run_id}'"
                )
                ov["dislocation"] = {
                    "run_id": run_id,
                    "meta": (m or [None])[0],
                    "opportunities": q(
                        "SELECT sector_id, primary_etf, drawdown_pct, contagion_explained_pct, "
                        "idiosyncratic_pct, contagion_fraction, catalyst_alignment, opportunity_score "
                        f"FROM dislocation WHERE run_id = '{run_id}' AND lens = 'opportunity' "
                        "ORDER BY opportunity_score DESC"),
                    "diversifiers": q(
                        "SELECT sector_id, primary_etf, composite, mean_corr_to_stressed, "
                        "diversifier_score FROM dislocation WHERE run_id = '{}' AND lens = 'diversifier' "
                        "ORDER BY diversifier_score DESC".format(run_id)),
                    "regime": q(
                        "SELECT sector_id, primary_etf, regime_state, drawdown_pct, catalyst_alignment, "
                        f"composite FROM dislocation WHERE run_id = '{run_id}' AND regime_state <> 'intact' "
                        "ORDER BY regime_state, drawdown_pct"),
                }
    finally:
        con.close()

    (dist / "overview.json").write_text(json.dumps(ov, ensure_ascii=False), encoding="utf-8")
    return {
        "runs": len(ov.get("runs") or []),
        "latest_ranking": len((ov.get("latest") or {}).get("ranking") or []),
        "portfolios": len(ov.get("portfolios") or []),
        "dislocation": bool(ov.get("dislocation")),
    }


def build(dist: Path = _DIST) -> dict:
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)

    for name in _STATIC:
        src = _SITE / name
        if src.exists():
            shutil.copy(src, dist / name)

    manifest: dict[str, list[str]] = {}
    total = 0
    for table in lake.TABLES:
        files = sorted(lake.table_dir(table).glob("*.parquet"))
        if not files:
            continue
        urls = []
        for fp in files:
            rel = fp.relative_to(_LAKE)
            dest = dist / "data" / "lake" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(fp, dest)
            urls.append(f"data/lake/{rel.as_posix()}")
            total += 1
        manifest[table] = urls

    (dist / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    docs = _bake_docs(dist)
    overview = _bake_overview(dist)
    return {"tables": len(manifest), "parquet_files": total, "docs": docs,
            "overview": overview, "dist": str(dist)}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = build()
    print(f"Built {r['dist']}: {r['parquet_files']} parquet across {r['tables']} tables")
    print(f"  docs.json: {r['docs']}")
    print(f"  overview.json: {r['overview']}")
    print("Preview: python -m http.server -d dist 8000  ->  http://localhost:8000")


if __name__ == "__main__":
    main()
