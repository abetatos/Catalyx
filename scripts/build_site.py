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
from catalyx.store import movement_repo

_ROOT = Path(__file__).parents[1]
_SITE = _ROOT / "site"
_DIST = _ROOT / "dist"
_LAKE = _ROOT / "data" / "lake"
_STATIC = ("index.html", "app.js")

# Tier-1 documents (config/JSON) surfaced read-only in the dashboard alongside the lake.
_STRUCTURAL_CAT = _ROOT / "catalyx" / "config" / "structural_catalysts"
_EVENT_CAT = _ROOT / "data" / "catalysts"
_STUDIES = _ROOT / "data" / "sector_studies"
_MOVEMENTS = _ROOT / "data" / "movements"


def _bake_docs(dist: Path) -> dict:
    """Bundle the Tier-1 documents into docs.json so the page can show the full picture:
    structural + event catalysts, sector studies, movements (the real positions). Small (KB each)."""
    docs: dict[str, list] = {"catalysts_structural": [], "catalysts_event": [],
                             "studies": [], "movements": []}
    for f in sorted(_STRUCTURAL_CAT.glob("*.yaml")):
        try:
            docs["catalysts_structural"].append(yaml.safe_load(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    for src, key in ((_EVENT_CAT, "catalysts_event"), (_STUDIES, "studies"), (_MOVEMENTS, "movements")):
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


def _fx_to_eur() -> dict:
    """Map a yfinance quote currency → factor that converts ONE quoted unit into EUR.

    Handles the non-EUR vehicles in the book: USD, GBP (pounds) and GBp/GBX (pence — LSE quotes
    many ETFs in pence, 1/100 of a pound). Without this a pence-quoted line (ISPY.L ≈ 3084 GBp)
    would be marked as if it were €3084/share → a nonsense +€40k "gain" on a €500 position.
    Identity on failure → caller falls back to treating the price as already-EUR.
    """
    fx = {"EUR": 1.0, None: 1.0}
    try:
        import yfinance as yf
        eurusd = float(yf.Ticker("EURUSD=X").history(period="5d")["Close"].dropna().iloc[-1])
        if eurusd:
            fx["USD"] = 1.0 / eurusd                          # USD per EUR → EUR per USD
        eurgbp = float(yf.Ticker("EURGBP=X").history(period="5d")["Close"].dropna().iloc[-1])
        if eurgbp:
            fx["GBP"] = 1.0 / eurgbp                           # GBP per EUR → EUR per GBP
            fx["GBp"] = fx["GBX"] = (1.0 / eurgbp) / 100.0     # pence → pounds → EUR
    except Exception:  # noqa: BLE001
        pass
    return fx


def _mark_to_market(positions: dict | None) -> dict | None:
    """Mark the real book against its AVG COST using last market prices (best-effort yfinance).

    The NAV curve measures performance since the entry DATE (market-relative); it does NOT show
    your unrealized P&L vs what you PAID. This marks each holding (qty × last_price_EUR − invested_eur)
    so the Positions page + Decision Journal show the actual gain/loss vs cost. The quoted price is
    converted to EUR via the vehicle's quote currency (yfinance fast_info) — USD/GBP/GBp all handled,
    so a pence- or dollar-listed line is not mismarked. Offline/failed fetch → left unmarked.
    """
    if not positions or not positions.get("holdings"):
        return positions
    try:
        import yfinance as yf
    except Exception:  # noqa: BLE001
        return positions
    fx = _fx_to_eur()
    mv_total = 0.0
    n_marked = 0
    holds = positions["holdings"]
    for h in holds:
        try:
            tkr = yf.Ticker(h["etf"])
            hist = tkr.history(period="5d", auto_adjust=True)["Close"].dropna()
            if hist.empty:
                continue
            last = float(hist.iloc[-1])
            try:
                cur = (tkr.fast_info or {}).get("currency")
            except Exception:  # noqa: BLE001
                cur = None
            # Convert the quoted price to EUR. A KNOWN non-EUR currency whose FX rate we could not
            # fetch this build (transient yfinance failure) must NOT silently fall back to factor 1.0
            # — that would mark e.g. a 3084-GBp line as €3084/share (a phantom +€40k on €500). Skip it
            # instead, leaving the holding unmarked; only None/EUR (already-EUR) use the identity.
            factor = fx.get(cur)
            if factor is None:
                if cur in (None, "EUR"):
                    factor = 1.0
                else:
                    continue
            last_eur = last * factor
            mv = round(h["qty"] * last_eur, 2)
            h["last_price"] = round(last, 4)
            h["quote_currency"] = cur
            h["last_price_eur"] = round(last_eur, 4)
            h["market_value_eur"] = mv
            h["unrealized_eur"] = round(mv - h["invested_eur"], 2)
            h["unrealized_pct"] = round((mv / h["invested_eur"] - 1) * 100, 2) if h["invested_eur"] else None
            mv_total += mv
            n_marked += 1
        except Exception:  # noqa: BLE001 — a single bad ticker never breaks the book
            continue
    # Only report a book-level current value when EVERY holding was marked — a partial sum (some
    # legs skipped for missing prices/FX) would understate the book and read as a false loss.
    if n_marked == len(holds) and n_marked:
        inv = positions.get("total_invested_eur") or 0.0
        positions["market_value_eur"] = round(mv_total, 2)
        positions["unrealized_eur"] = round(mv_total - inv, 2)
        positions["unrealized_pct"] = round((mv_total / inv - 1) * 100, 2) if inv else None
    return positions


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


def _catalyst_exposure(pid: str) -> dict:
    """Per-catalyst decomposition of a portfolio's notional book (timeseries + time-weighted avg),
    computed by lake_query so the dashboard needs zero WASM for the first paint. {} on any error."""
    try:
        from catalyx.store import lake_query
        return lake_query.portfolio_catalyst_exposure(pid)
    except Exception:  # noqa: BLE001
        return {}


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
            "git_commit, sector_count, notes, summary FROM score_run ORDER BY run_id DESC"
        ) if has("score_run") else []
        # The pipeline (snapshot_repo.record_run) authors a deterministic change-`summary`
        # JSON per run; parse it through so the dashboard shows it verbatim (no recompute).
        for rr in runs:
            raw = rr.pop("summary", None)
            if raw:
                try:
                    rr["summary"] = json.loads(raw)
                except Exception:  # noqa: BLE001
                    rr["summary"] = None
            else:
                rr["summary"] = None
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
            # NULL-safe optional columns: the flow_proxy_* fields exist only in partitions recorded
            # after that feature landed. Selecting a non-existent column would throw → the defensive
            # q() would blank the whole ranking. So emit `NULL AS col` for any column not present.
            try:
                ss_cols = set(con.execute("SELECT * FROM sector_snapshot LIMIT 0").fetchdf().columns)
            except Exception:  # noqa: BLE001
                ss_cols = set()
            opt = lambda c: c if c in ss_cols else f"NULL AS {c}"  # noqa: E731
            ov["latest"]["ranking"] = q(
                "SELECT sector_id, rank, composite, catalyst_alignment, momentum, flow_confirmation, "
                f"{opt('flow_data_quality')}, {opt('flow_source')}, {opt('flow_proxy_ticker')}, {opt('flow_proxy_used')}, "
                f"{opt('flow_carried_from')}, {opt('flow_volume_cmf')}, {opt('flow_window_days')}, {opt('flow_days_covered')}, "
                "crowding_risk, narrative_maturity, primary_etf, regime_state "
                f"FROM sector_snapshot WHERE run_id = '{latest_rid}' ORDER BY rank")
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

        # ── fallback change-summary for any run the pipeline didn't author one for
        #    (older runs are backfilled via `snapshot_repo backfill-summaries`; this only
        #    fires for a run missing the column entirely). Same schema, computed at build.
        missing = [rr for rr in runs if rr.get("summary") is None]
        if missing and has("sector_snapshot"):
            run_rank = {rr["run_id"]: {x["sector_id"]: x["rank"]
                        for x in q(f"SELECT sector_id, rank FROM sector_snapshot WHERE run_id = '{rr['run_id']}'")}
                        for rr in runs}
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
                if rr.get("summary") is not None:
                    continue
                cur = run_rank.get(rr["run_id"], {})
                prev_run = runs[i + 1] if i + 1 < len(runs) else None
                pv = run_rank.get(prev_run["run_id"], {}) if prev_run else {}
                movers = sorted(((s, pv[s] - rk) for s, rk in cur.items() if s in pv and pv[s] != rk),
                                key=lambda x: -abs(x[1]))
                new_cats = []
                if prev_run:
                    lo, hi = prev_run["ts"][:10], rr["ts"][:10]
                    new_cats = [{"id": e["id"]} for e in events if e["date"] and lo < e["date"] <= hi]
                rr["summary"] = {
                    "movers_up": [{"sector_id": s, "delta": d} for s, d in movers if d > 0][:5],
                    "movers_down": [{"sector_id": s, "delta": d} for s, d in movers if d < 0][:5],
                    "entered": [s for s, rk in cur.items() if rk <= 10 and (s not in pv or pv[s] > 10)][:8],
                    "exited": [s for s, rk in pv.items() if rk <= 10 and (s not in cur or cur[s] > 10)][:8],
                    "new_catalysts": new_cats, "regime": None, "breadth": None,
                }

        # ── portfolios: prefer the LIVE walk-forward track record (mode='live') as the headline;
        # the mode='backtest' rows are a HYPOTHETICAL reference shown only until live history accrues
        # (the live curve starts at track_record inception and grows one run at a time).
        cfgs = _portfolio_configs()
        try:
            from catalyx.config import weights as _w
            inception = _w.track_record_inception()
        except Exception:  # noqa: BLE001
            inception = None
        ov["track_inception"] = inception
        ov["portfolios"] = []
        if has("portfolio_nav"):
            for row in q("SELECT DISTINCT portfolio_id FROM portfolio_nav ORDER BY portfolio_id"):
                pid = row["portfolio_id"]
                rows = q(
                    "SELECT date, nav, benchmark_nav, return_pct, vs_benchmark_pct, benchmark_etf, kind, mode "
                    f"FROM portfolio_nav WHERE portfolio_id = '{pid}' ORDER BY date"
                )
                if not rows:
                    continue
                kind = rows[-1].get("kind")
                live = [r for r in rows if r.get("mode") == "live"]
                ref = [r for r in rows if r.get("mode") != "live"]   # backtest/forward (+ real has mode null)
                is_live = len(live) >= 2                              # a real curve needs ≥2 points
                shown = live if is_live else (ref or live)
                if not shown:
                    continue
                last = shown[-1]
                cfg = cfgs.get(pid, {})
                track_mode = "real" if kind == "real" else ("live" if is_live else "accruing")
                ov["portfolios"].append({
                    "portfolio_id": pid,
                    "name": cfg.get("name", pid),
                    "description": (cfg.get("description") or "").strip(),
                    "construction": cfg.get("construction"),
                    "kind": kind,
                    "track_mode": track_mode,             # live | accruing | real
                    "inception": inception,
                    "live_points": len(live),
                    # while 'accruing', the curve shown is the hypothetical backtest (flag it as such)
                    "is_reference_curve": track_mode == "accruing",
                    "return_pct": last.get("return_pct"),
                    "vs_benchmark_pct": last.get("vs_benchmark_pct"),
                    "benchmark_etf": last.get("benchmark_etf"),
                    "metrics": _series_metrics([s["nav"] for s in shown]),
                    "bench_metrics": _series_metrics([s["benchmark_nav"] for s in shown]),
                    "n_days": len(shown),
                    # dates aligned 1:1 with nav/benchmark_nav (same source rows + identical
                    # downsample indices) → an x-axis for the axed NAV line chart on Positions.
                    "dates": _downsample([str(s["date"])[:10] for s in shown]),
                    "nav": _downsample([s["nav"] for s in shown]),
                    "benchmark_nav": _downsample([s["benchmark_nav"] for s in shown]),
                    # catalyst decomposition of the notional book, per rebalance + time-weighted avg
                    "catalyst_exposure": _catalyst_exposure(pid),
                })
            # `catalyx` (the flagship composite book) is ALWAYS pinned first; the rest by return desc.
            ov["portfolios"].sort(key=lambda p: (
                p.get("portfolio_id") != "catalyx", p.get("return_pct") is None, -(p.get("return_pct") or 0)))

        # ── real book (Positions page): the actual money, treated like a portfolio but kept SEPARATE
        # from the model strategies. NAV/metrics come from the kind='real' portfolio_nav row; the
        # holdings + movements + catalyst exposure are derived from the Tier-1 movement files.
        ov["positions_book"] = next((p for p in ov["portfolios"] if p.get("kind") == "real"), None)
        ov["portfolios"] = [p for p in ov["portfolios"] if p.get("kind") != "real"]
        try:
            ov["positions"] = _mark_to_market(movement_repo.positions())
            ov["catalyst_ledger"] = movement_repo.catalyst_ledger()
        except Exception:  # noqa: BLE001 — never let a movement read break the build
            ov["positions"], ov["catalyst_ledger"] = None, []

        # ── committed capital + cash (dry powder). The full book is allocated up front but
        # deployed progressively as catalysts fire; cash = committed − cost basis of open
        # positions. Surfaced on the Positions page so an undeployed balance is explicit. ──
        try:
            from catalyx.config import weights as _w
            cap = _w.total_capital_eur()
        except Exception:  # noqa: BLE001
            cap = None
        ov["total_capital_eur"] = cap
        if cap is not None and ov.get("positions"):
            invested = ov["positions"].get("total_invested_eur") or 0.0
            ov["positions"]["total_capital_eur"] = cap
            ov["positions"]["cash_eur"] = round(cap - invested, 2)
            ov["positions"]["deployed_pct"] = round(invested / cap * 100, 1) if cap else None

        # ── nav_compare (Positions "Performance vs S&P 500"): an honest apples-to-apples view —
        # the REAL book + each model's LIVE walk-forward curve + the SPY benchmark, all indexed 100
        # from inception and date-aligned (NO backtest mixed in, unlike the accruing portfolio cards).
        # Same measure as the model portfolios (return vs SPY + vol/Sharpe/maxDD). Fills in as the
        # live track record accrues (≥2 daily points). None until any live/real point exists.
        ov["nav_compare"] = None
        if has("portfolio_nav"):
            cmp_rows = q(
                "SELECT portfolio_id, kind, CAST(date AS VARCHAR) AS date, nav, benchmark_nav, "
                "return_pct, vs_benchmark_pct, benchmark_etf FROM portfolio_nav "
                "WHERE mode = 'live' OR kind = 'real' ORDER BY date")
            by_pid: dict = {}
            for r in cmp_rows:
                d = (r.get("date") or "")[:10]
                if not d or (inception and d < inception):
                    continue
                r["date"] = d
                by_pid.setdefault(r["portfolio_id"], []).append(r)
            if by_pid:
                dates = sorted({r["date"] for rs in by_pid.values() for r in rs})
                bench_etf = next((r["benchmark_etf"] for rs in by_pid.values()
                                  for r in rs if r.get("benchmark_etf")), "SPY")

                def _aligned(rs: list[dict], key: str) -> list:
                    """Series aligned to the union `dates`, REBASED to 100 at its first in-window
                    point (so all curves share a common start), forward-filled across gaps."""
                    m = {r["date"]: r[key] for r in rs if r.get(key) is not None}
                    base = next((m[d] for d in dates if d in m), None)
                    if not base:
                        return [None] * len(dates)
                    out, last = [], None
                    for d in dates:
                        if d in m:
                            last = round(m[d] / base * 100.0, 4)
                        out.append(last)
                    return out

                bench_src = max(by_pid.values(), key=len)  # the longest series carries the SPY line
                bench_nav = _aligned(bench_src, "benchmark_nav")
                series = []
                for pid, rs in by_pid.items():
                    is_real = rs[-1].get("kind") == "real"
                    navs = _aligned(rs, "nav")
                    series.append({
                        "portfolio_id": pid,
                        "name": "My book" if is_real else cfgs.get(pid, {}).get("name", pid),
                        "kind": rs[-1].get("kind"),
                        "is_real": is_real,
                        "nav": _downsample(navs),
                        "return_pct": rs[-1].get("return_pct"),
                        "vs_benchmark_pct": rs[-1].get("vs_benchmark_pct"),
                        "metrics": _series_metrics([v for v in navs if v is not None]),
                    })
                series.sort(key=lambda s: (not s["is_real"], -(s["return_pct"] or 0)))
                bmet = _series_metrics([v for v in bench_nav if v is not None]) or {}
                _bn = [v for v in bench_nav if v is not None]
                if _bn:
                    bmet["ret_pct"] = round(_bn[-1] - 100.0, 2)
                ov["nav_compare"] = {
                    "inception": inception,
                    "benchmark_etf": bench_etf,
                    "dates": _downsample(dates),
                    "series": series,
                    "benchmark_nav": _downsample(bench_nav),
                    "benchmark_metrics": bmet or None,
                }

        # rotation targets ANCHORED to the real book's holdings (healthy sectors least correlated
        # to what you already own) — computed by dislocation --anchor-sectors → portfolio_rotation.
        ov["positions_rotation"] = None
        if has("portfolio_rotation"):
            meta = q("SELECT max(run_id) AS run_id FROM portfolio_rotation")
            rid = meta[0]["run_id"] if meta and meta[0].get("run_id") is not None else None
            if rid:
                ov["positions_rotation"] = q(
                    "SELECT sector_id, primary_etf, composite, mean_corr_to_stressed AS corr_to_book, "
                    f"diversifier_score FROM portfolio_rotation WHERE run_id = '{rid}' "
                    "AND lens = 'diversifier' ORDER BY diversifier_score DESC LIMIT 5")

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

        # ── entry-timing overlay (micro-tension + event overhang, recommend-only) ──
        # The timing answers WHEN to enter — it only makes sense ATTACHED to a sector you might
        # act on (a dislocation opportunity OR a fundamentals pick from the ranking). So we bake it
        # as a by_sector MAP for the whole run (every assessed sector, not just the caveated ones):
        # the Overview merges it INTO each opportunity ticket, and the sector detail shows it too.
        ov["entry_timing"] = None
        if has("entry_timing"):
            meta = q("SELECT max(run_id) AS run_id FROM entry_timing")
            run_id = meta[0]["run_id"] if meta and meta[0].get("run_id") is not None else None
            if run_id is not None:
                m = q("SELECT DISTINCT as_of, vix, vix_5d_change, spy_5d_pct "
                      f"FROM entry_timing WHERE run_id = '{run_id}'")
                rows = q(
                    "SELECT sector_id, primary_etf, micro_timing_state, tension_score, rsi_14, "
                    "stretch_vs_ma20_pct, vol_ratio_10_90, return_5d_pct, trend_deadband_pct, "
                    "drawdown_from_20d_high_pct, "
                    "stabilizing, suggested_verdict, wait_until, has_upcoming_overhang, "
                    "nearest_overhang_id, nearest_overhang_date, nearest_overhang_days_until "
                    f"FROM entry_timing WHERE run_id = '{run_id}'")
                ov["entry_timing"] = {
                    "run_id": run_id, "meta": (m or [None])[0],
                    "by_sector": {r["sector_id"]: r for r in rows},
                }

        # ── exit watcher (Family 1 sell signals: stops + assumptions + regime + after-tax) ──
        # Recommend-only. Baked as a by_etf MAP (holdings are keyed by etf) so the Positions page
        # renders a per-holding Exit-watch panel + an inline action badge, and Overview raises an
        # alert for any EXIT/REDUCE. Source is the lake exit_signal table (no live network at build).
        ov["exit_signal"] = None
        if has("exit_signal"):
            meta = q("SELECT max(run_id) AS run_id FROM exit_signal")
            run_id = meta[0]["run_id"] if meta and meta[0].get("run_id") is not None else None
            if run_id is not None:
                rows = q(
                    "SELECT sector_id, etf, suggested_action, regime_state, invested_eur, weight_pct, "
                    "n_stops, n_fired, n_approaching, n_claude_check, fired_ids, approaching_ids, "
                    "claude_check_ids, loudest_fired_id, loudest_fired_severity, assumptions_total, "
                    "assumptions_violated, assumptions_weakening, unrealized_eur, unrealized_pct, "
                    "tax_due_eur, net_proceeds_eur, harvestable_loss_eur "
                    f"FROM exit_signal WHERE run_id = '{run_id}'")
                ov["exit_signal"] = {"run_id": run_id, "by_etf": {r["etf"]: r for r in rows}}

        # ── experiment ledger (closed positions scored as experiments) ──
        # One row per closed/trimmed movement: the right-thesis × right-reason verdict, after-tax
        # P&L, behavioral flags, and the in-the-moment exit_note. Source: lake movement_outcome
        # (written by catalyx.attribution.outcome at /catalyx-close). Newest first.
        ov["experiment_ledger"] = []
        if has("movement_outcome"):
            ov["experiment_ledger"] = q(
                "SELECT mov_id, executed_at, sector_id, etf, verdict_label, verdict_confidence, "
                "right_thesis, right_reason, gross_pnl_eur, after_tax_pnl_eur, return_pct, "
                "holding_days, behavioral_flags, exit_trigger_type, followed_signal, exit_reason, "
                "catalyst_materialized, "
                # exit_note lives on the Tier-1 file, not the lake row — join it in JS via DOCS if
                # needed; here surface what the lake carries.
                "asm_validated, asm_falsified, asm_unresolved "
                "FROM movement_outcome ORDER BY executed_at DESC")
    finally:
        con.close()

    # exit_note is a Tier-1 doc field (not persisted to the lake row) — splice it in from the files
    # so the ledger can show the in-the-moment reflection without a second fetch.
    if ov.get("experiment_ledger"):
        try:
            notes = {m["id"]: ((m.get("outcome") or {}).get("exit_note"))
                     for m in movement_repo.load_all()}
            for e in ov["experiment_ledger"]:
                e["exit_note"] = notes.get(e["mov_id"])
        except Exception:  # noqa: BLE001 — never let a movement read break the build
            pass

    # ── Decision Journal: OPEN experiments (the forward/hypothesis side) ──
    # Movements flagged metadata.experiment.is_experiment — the reasoning recorded AT ENTRY:
    # hypothesis, what-would-disprove, accepted risks, the technical posture, the sizing decision,
    # plus a compact risk_discipline summary (the loudest stop + assumption counts). The live P&L
    # lives on the Positions page; here we surface the DECISION, not the mark. Newest first.
    ov["journal_open"] = []
    try:
        rows = []
        for m in movement_repo.load_all():
            meta = m.get("metadata") or {}
            exp = meta.get("experiment") or {}
            if not exp.get("is_experiment"):
                continue
            rd = m.get("risk_discipline") or {}
            inv = rd.get("invalidation") or []
            asm = rd.get("assumptions") or []
            full_exit = next((i.get("condition") for i in inv if i.get("severity") == "full_exit"), None)
            asm_status = {}
            for a in asm:
                s = a.get("current_status") or "unknown"
                asm_status[s] = asm_status.get(s, 0) + 1
            rows.append({
                "mov_id": m.get("id"),
                "journal_id": exp.get("journal_id"),
                "executed_at": m.get("executed_at"),
                "sector_id": m.get("sector_id"),
                "etf": (m.get("vehicle") or {}).get("etf"),
                "amount_eur": m.get("amount_eur"),
                "conviction": m.get("conviction"),
                "trigger": m.get("trigger"),
                "attribution": m.get("attribution") or [],
                "score_context": m.get("score_context") or {},
                "hypothesis": exp.get("hypothesis"),
                "what_would_disprove": exp.get("what_would_disprove"),
                "accepted_risks": exp.get("accepted_risks") or [],
                "entry_technicals": exp.get("entry_technicals") or {},
                "sizing_decision": exp.get("sizing_decision") or {},
                "full_exit_condition": full_exit,
                "n_invalidation": len(inv),
                "n_assumptions": len(asm),
                "assumption_status": asm_status,
                "risk_note": rd.get("note"),
            })
        rows.sort(key=lambda r: str(r.get("executed_at") or ""), reverse=True)
        ov["journal_open"] = rows
    except Exception:  # noqa: BLE001 — never let a movement read break the build
        ov["journal_open"] = []

    (dist / "overview.json").write_text(json.dumps(ov, ensure_ascii=False), encoding="utf-8")
    return {
        "runs": len(ov.get("runs") or []),
        "latest_ranking": len((ov.get("latest") or {}).get("ranking") or []),
        "portfolios": len(ov.get("portfolios") or []),
        "dislocation": bool(ov.get("dislocation")),
        "entry_timing": len((ov.get("entry_timing") or {}).get("by_sector") or {}),
        "exit_signal": len((ov.get("exit_signal") or {}).get("by_etf") or {}),
        "experiment_ledger": len(ov.get("experiment_ledger") or []),
        "journal_open": len(ov.get("journal_open") or []),
    }


def build(dist: Path = _DIST) -> dict:
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)

    # Cache-busting token: index.html, app.js and the JSON/parquet it fetches must always
    # load as one coherent set. Without this, GitHub Pages can serve a fresh index.html with
    # a stale cached app.js (or vice-versa) → the DOM contract mismatches and sections blank.
    # The token versions every reference so a new deploy invalidates them together.
    token = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    for name in _STATIC:
        src = _SITE / name
        if not src.exists():
            continue
        if name == "index.html":
            html = src.read_text(encoding="utf-8")
            html = html.replace(
                '<script type="module" src="app.js"></script>',
                f'<script>window.__BUILD__="{token}";</script>\n'
                f'  <script type="module" src="app.js?v={token}"></script>')
            (dist / name).write_text(html, encoding="utf-8")
        else:
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
