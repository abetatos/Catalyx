"""CATALYX — one state file per run.

WHY THIS EXISTS (v4 Phase D-b, docs/PLAN_v4_rigor_and_lean_metrics.md §5):
    The review used to thread ~8 payloads through its own context by hand: `state_<date>.json`
    from the pre-run, `scan_deltas_<date>.json` from the scan, three stdout digests from
    `score_run.sh`, the NAV lines and the rebalance table from `post_run.sh`. Every step that was
    ever added made the skill restate more of that plumbing, and the restatement — not the numbers
    — is what grew. So the run writes ONE file and the skill says "read run_<date>.json".

    This module is READ-ONLY over the lake and the Tier-1 files: it runs no scorer and persists
    nothing except its own digest. It reads a price only through the two FORWARD scorers it
    summarizes — `score_overrides` and `score_decisions` — because an edge measured over a window
    that has not closed cannot live in a lake table. It therefore assembles what the run ALREADY
    computed — run it at the END of `post_run.sh`, after the rebalance is in the lake. A section
    whose source is missing degrades to `null` plus a `missing[]` entry; it never raises, because
    a partial digest is still worth more than a traceback.

    Deliberately NOT here: prose, verdicts, recommendations. Every number in this file is one
    Python function's output, and `scripts/review_report.py` renders the same lake into the report
    — the two read the lake independently ON PURPOSE, so the report can never quote a digest that
    went stale between the two calls.

Usage:
    uv run python -m catalyx.store.run_digest              # compact summary → stdout
    uv run python -m catalyx.store.run_digest --write      # + data/reports/run_<date>.json
    uv run python -m catalyx.store.run_digest --json       # the full dict
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_REPORTS = Path("data/reports")

# The per-position fields a decision actually reads. position_metrics carries 31 columns; the ones
# below are the ones that appear in a Step 6 sentence or a rebalance row. The rest stay in the lake.
_POSITION_FIELDS = (
    "sector_id", "etf", "invested_eur", "market_value_eur", "unrealized_eur", "unrealized_pct",
    "pnl_price_eur", "pnl_fx_eur", "days_held", "max_drawdown_from_peak_pct",
    "composite_at_entry", "composite_now", "composite_drift", "rank_now", "rank_drift",
    "catalyst_freshness", "regime_state", "exit_action", "drawdown_tier",
    # Capital share vs risk share — the two are routinely far apart and only one used to be
    # reported anywhere (plan v4 A4).
    "capital_pct_of_book", "vol_common_window_pct", "risk_contribution_pct",
)

# `score_rank`, not `rank`: the universe rank is what every `reason` cites and every rendered `rk`
# column shows. `rank` is the model-book rank — null for exactly the rows whose reason names a
# number, which is how the digest used to carry `rank: null` beside "ranked below top-10 (#11)".
_REBALANCE_FIELDS = (
    "sector_id", "etf", "score_rank", "target_pct", "actual_pct", "gap_eur", "rule_action",
    "trade_eur", "tax_eur", "breakeven_pct", "reason", "regime_state", "exit_action", "flags",
    # The age of the evidence the row spends on — a BUY carries no position, so before v5 it
    # arrived with nothing qualifying the score that justified it (plan v5 E1).
    "data_age",
)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _latest_partition(table: str, lake_dir: Path | None):
    """The rows of the most recent run_id in `table`, as records. [] when the table is absent."""
    from catalyx.store import lake

    try:
        df = lake.read_table(table, lake_dir=lake_dir)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    if "run_id" in df.columns:
        df = df[df["run_id"] == sorted(df["run_id"].unique())[-1]]
    return json.loads(df.to_json(orient="records"))       # via JSON: NaN → null, ts → str


def _pick(rows: list[dict], fields: tuple[str, ...]) -> list[dict]:
    return [{k: r.get(k) for k in fields} for r in rows]


def build(as_of: str | None = None, top_n: int = 15, lake_dir: Path | None = None) -> dict:
    """Assemble the run's whole deterministic state. Never raises on a missing source."""
    from catalyx.store import lake_query

    as_of = as_of or f"{date.today():%Y-%m-%d}"
    stamp = as_of.replace("-", "")
    missing: list[str] = []

    def _try(name, fn, default=None, empty_ok=False):
        """`empty_ok` for a CHECK, where nothing found is the clean result rather than an absent
        input. Listing a passing check under MISSING reads as a hole in the run and teaches the
        reader to discount the whole list."""
        try:
            v = fn()
        except Exception as exc:
            missing.append(f"{name} ({exc})")
            return default
        if v in ([], {}, None) and not empty_ok:
            missing.append(name)
        return v

    state_path = _REPORTS / f"state_{stamp}.json"
    scan_path = _REPORTS / f"scan_deltas_{stamp}.json"
    state = _read_json(state_path) or {}
    scan = _read_json(scan_path)
    if not state:
        missing.append(f"{state_path} (run `bash scripts/pre_run.sh`)")
    if scan is None:
        missing.append(f"{scan_path} (no /catalyx-scan this run)")

    reb_rows = _try("rebalance", lambda: _latest_partition("rebalance", lake_dir), [])
    from catalyx.execution.rebalance import PRECEDENCE
    order = {a: i for i, a in enumerate(PRECEDENCE)}
    reb_rows.sort(key=lambda r: (order.get(str(r.get("rule_action")), 9),
                                 -abs(float(r.get("trade_eur") or 0))))
    actions = [r for r in reb_rows if str(r.get("rule_action")) != "HOLD"]
    r0 = reb_rows[0] if reb_rows else {}

    book_rows = _try("book_metrics", lambda: _latest_partition("book_metrics", lake_dir), [])
    positions = _try("position_metrics", lambda: _latest_partition("position_metrics", lake_dir), [])

    return {
        "as_of": as_of,
        "schema_version": "1.0",
        # Where each block came from, so a reader can go deeper without guessing the path.
        "sources": {
            "state": str(state_path) if state else None,
            "scan_deltas": str(scan_path) if scan is not None else None,
            "lake": str(lake_dir) if lake_dir else "data/lake",
            "report": f"data/reports/review_{stamp}.md",
        },
        "missing": missing,

        # ── what the pre-run established: book, attention, work list ──────────────────
        "book": state.get("book"),
        "attention": state.get("attention"),
        "work_list": state.get("work_list"),
        "lifecycle_transitions": state.get("lifecycle_transitions"),
        "stale_verdicts": state.get("stale_verdicts"),

        # ── what the scan changed (deltas the review must apply before scoring) ───────
        "scan": None if scan is None else {
            "path": str(scan_path),
            "n_indicator_updates": len(scan.get("indicator_updates") or []),
            "n_catalyst_reviews": len(scan.get("catalyst_reviews") or []),
            "n_lifecycle": len(scan.get("lifecycle_transitions") or []),
            "n_new_catalysts": len(scan.get("new_catalysts") or []),
        },

        # ── what the score run produced ───────────────────────────────────────────────
        "ranking": _try("ranking", lambda: lake_query.latest_ranking(top_n=top_n, lake_dir=lake_dir), []),
        "rank_moves": _try("rank_moves",
                           lambda: _rank_moves(lake_query.rank_moves(top_n=10, lake_dir=lake_dir)),
                           []),

        # ── what the post run produced ────────────────────────────────────────────────
        "portfolios": _try("portfolios", lambda: lake_query.portfolio_compare(lake_dir=lake_dir), []),
        "book_metrics": book_rows[0] if book_rows else None,
        "positions": _pick(positions, _POSITION_FIELDS),
        "rebalance": {
            "run_id": r0.get("run_id"),
            "strategy": r0.get("strategy"),
            "deploy_ratio": r0.get("deploy_ratio"),
            "total_capital_eur": r0.get("book_total_capital_eur"),
            "deployable_eur": r0.get("book_deployable_eur"),
            "cash_target_eur": r0.get("book_cash_target_eur"),
            "cash_actual_eur": r0.get("book_cash_actual_eur"),
            "cash_action_eur": r0.get("book_cash_action_eur"),
            "tilt_lambda": r0.get("book_tilt_lambda"),
            # The cost of NOT acting, carried beside the cost of acting (plan v4 §4 C1/C4).
            "shortfall_pp": r0.get("book_shortfall_pp"),
            "shortfall_runs": r0.get("book_shortfall_runs"),
            "shortfall_breached": r0.get("book_shortfall_breached"),
            "cash_drag_eur": r0.get("book_cash_drag_eur"),
            "cash_idle_since": r0.get("book_cash_idle_since"),
            "cash_idle_days": r0.get("book_cash_idle_days"),
            "benchmark_return_pct": r0.get("book_bench_return_pct"),
            # The counterfactual matching the decision, and a verdict that flips with the sign.
            "model_return_pct": r0.get("book_model_return_pct"),
            "cash_model_forgone_eur": r0.get("book_cash_model_forgone_eur"),
            "cash_verdict": r0.get("book_cash_verdict"),
            "n_actions": len(actions),
            "n_hold": len(reb_rows) - len(actions),
            "actions": _pick(actions, _REBALANCE_FIELDS),
            "holds": [r.get("sector_id") for r in reb_rows if str(r.get("rule_action")) == "HOLD"],
            # "¿renta vender?" and "¿parciales?" — derived from the SAME persisted rows by the
            # engine's own pure functions, so the digest never runs the engine (which reads VIX)
            # and can never disagree with the table it summarizes.
            **_swaps_and_partials(reb_rows),
        },
        # Live from the movement files, not the `catalyst_performance` snapshot: that partition
        # freezes the merge map of the day it was written, and a merged catalyst reported as two
        # rows is the double count `correlated_catalyst_cap` exists to prevent.
        "exposure": _try("exposure", _exposure, []),
        # The cap applied to the PROPOSED table, not only to the held book. Breaches only: a
        # compliant table is the normal case and printing it row by row is noise.
        "cap_breaches": _try("cap_breaches", lambda: _cap_breaches(reb_rows), [], empty_ok=True),
        "attribution_drift": _try("attribution_drift", _drift, [], empty_ok=True),
        "overrides": _try("overrides", _overrides, None),
        # The table's own record, on the same clock as the deviations from it (plan v4 §3 B4).
        "scorecard": _try("scorecard", _scorecard, None),
    }


def _exposure() -> list[dict]:
    """`exposure_eur` (full position per driver) is what the cap reads; `pct` saves every reader
    recomputing it. The weight-split `invested_eur` is P&L credit, not a risk number, and is left
    to the CLI — carrying both here invited reading the wrong one."""
    from catalyx.config import weights
    from catalyx.store import movement_repo

    total = weights.total_capital_eur() or 0.0
    out = []
    for r in movement_repo.catalyst_ledger():
        exp = float(r.get("exposure_eur") or 0.0)
        out.append({"catalyst_id": r["catalyst_id"], "exposure_eur": exp,
                    "pct": round(exp / total * 100.0, 1) if total else None,
                    "sectors": r.get("sectors") or [],
                    "absorbed_ids": r.get("absorbed_ids") or []})
    return out


def _cap_breaches(reb_rows: list[dict]) -> list[dict]:
    from catalyx.store import movement_repo

    proposed = [{"sector_id": r.get("sector_id"), "trade_eur": r.get("trade_eur")}
                for r in (reb_rows or []) if r.get("rule_action") in ("BUY", "ADD")]
    return [c for c in movement_repo.cap_check(proposed) if c["over"]]


def _drift() -> list[dict]:
    from catalyx.store import movement_repo
    return movement_repo.attribution_drift()


def _rank_moves(moves: list[dict]) -> list[dict] | dict:
    """Only the moves that are findings.

    A run where EVERY |Δ| ≥ 5 move points the same way is the denominator moving — a universe cut
    or a scoring edit — not N independent sector stories. Carrying the rows invites reading it as
    N findings and costs a KB of context to do it, so the sweep is reported as the one fact it is.
    """
    big = [m for m in (moves or []) if abs(float(m.get("delta") or 0)) >= 5]
    if not big:
        return []
    ups = sum(1 for m in big if float(m.get("delta") or 0) > 0)
    if len(big) >= 5 and ups in (0, len(big)):
        return {"uniform_sweep": True, "n": len(big),
                "direction": "up" if ups else "down",
                "note": (f"all {len(big)} moves of |Δ| ≥ 5 point the same way — the denominator "
                         f"moved (universe or scoring change), not {len(big)} findings. Rows "
                         f"withheld; `lake_query moves` has them.")}
    return big[:12]


def _swaps_and_partials(reb_rows: list[dict]) -> dict:
    """The swap ledger, its evidence line, and the partial-rung distances. Pure over `reb_rows`."""
    if not reb_rows:
        return {"swaps": [], "rank_edge_evidence": None, "partials": [], "gate": None,
                "selection_prior": None}
    try:
        from catalyx.config import weights
        from catalyx.execution import rebalance
        from catalyx.scorer import calibration

        cfg = weights.rebalance_rules()
        try:
            exp = calibration.expected_returns()
        except Exception:
            exp = {}
        try:
            ic = calibration.composite_ic()
        except Exception:
            ic = {}
        return {
            "swaps": rebalance.swap_ledger(reb_rows, cfg, horizon_days=exp.get("horizon_days")),
            "rank_edge_evidence": rebalance.rank_edge_evidence(exp, cfg),
            # What the deployment pressure rests on when the ranking's edge is not it (v5 F1).
            "selection_prior": rebalance.selection_prior(
                rebalance.rank_edge_evidence(exp, cfg),
                (reb_rows[0] or {}).get("book_tilt_lambda")),
            **_partials(rebalance.partial_rungs(reb_rows, cfg)),
            # Whether the after-tax gate may BLOCK a sale this run, and why not when it may not.
            "gate": rebalance.gate_status(exp, ic, cfg),
        }
    except Exception as exc:                                   # pragma: no cover - defensive
        return {"swaps": [], "rank_edge_evidence": {"error": str(exc)}, "partials": [],
                "gate": None, "selection_prior": None}


def _partials(parts: list[dict]) -> dict:
    """The rung definitions are the SAME on every row — they come from one config. Repeating both
    labels and `rank_min` per position said the rule five times to say five distances once."""
    if not parts:
        return {"partials": []}
    lad = next((p["ladder"] for p in parts if p.get("ladder")), None)
    rows = []
    for p in parts:
        L, ow = p.get("ladder") or {}, p.get("overweight") or {}
        rows.append({"sector_id": p.get("sector_id"), "etf": p.get("etf"),
                     "unrealized_pct": p.get("unrealized_pct"), "rank": p.get("rank"),
                     "action": p.get("action"), "live": p.get("live"),
                     # Distance to each rung; None = already met.
                     "ladder_need_gain_pct": (None if L.get("gain_met")
                                              else L.get("need_gain_pct")),
                     "ladder_rank_ok": L.get("rank_ok"),
                     "overweight_need_pp": (None if ow.get("met") else ow.get("need_pp"))})
    return {"partials": rows,
            "partial_rungs": {"ladder": (lad or {}).get("label"),
                              "ladder_rank_min": (lad or {}).get("rank_min"),
                              "overweight": ((parts[0].get("overweight") or {}).get("label"))}}


def _scorecard() -> dict | None:
    from catalyx.execution import rebalance

    res = rebalance.score_decisions()
    sc = res.get("scorecard") or {}
    # The buckets and the sample size — not the priced rows, which are re-derivable from the lake.
    return {"horizon_days": res.get("horizon_days"), "n_pending": len(res.get("pending") or []),
            "rows": sc.get("rows"), "effective_windows": sc.get("effective_windows"),
            "scoreable": sc.get("scoreable"), "note": sc.get("note")}


def _overrides() -> dict | None:
    from catalyx.execution import rebalance

    res = rebalance.score_overrides()
    if not isinstance(res, dict):
        return None
    # The tally and the suspension verdict — not the per-override rows, which are in the lake.
    return {k: res.get(k) for k in ("n_logged", "n_scored", "by_author", "suspension", "note")
            if k in res}


def render(d: dict) -> str:
    """The compact read: what changed, what the rules say, what is missing. ~30 lines."""
    out: list[str] = [f"CATALYX — run digest  {d['as_of']}"]
    b, reb = d.get("book") or {}, d.get("rebalance") or {}
    bm = d.get("book_metrics") or {}

    if b:
        out.append(f"  BOOK      {b.get('n_positions')} positions · invested €{b.get('invested_eur')} · "
                   f"unrealized {b.get('unrealized_pct')}% · realized YTD €{b.get('realized_ytd_eur')}")
    if bm:
        out.append(f"  SHAPE     deployed {bm.get('deployed_pct')}% · TWR {bm.get('twr_pct')}% · "
                   f"HHI {bm.get('hhi')} (effective N {bm.get('effective_n')}) · model overlap "
                   f"{bm.get('model_overlap_pct')}% · reliable={bm.get('metrics_reliable')}")
    risky = sorted((p for p in d.get("positions") or []
                    if p.get("risk_contribution_pct") is not None),
                   key=lambda p: -(p["risk_contribution_pct"]))
    if risky:
        out.append(f"  RISK      book vol {bm.get('book_vol_from_cov_pct')}% over "
                   f"{bm.get('risk_window_days')}d · capital % → risk %:")
        for p in risky:
            flag = ("  ← NEGATIVE, lowers book vol" if p["risk_contribution_pct"] < 0
                    else "  ← carries more risk than capital"
                    if p.get("capital_pct_of_book")
                    and p["risk_contribution_pct"] >= p["capital_pct_of_book"] * 1.3 else "")
            out.append(f"     {str(p['sector_id']):<32}{p.get('capital_pct_of_book'):>6}% → "
                       f"{p['risk_contribution_pct']:>6}%{flag}")
    if reb.get("n_actions") is not None:
        cash = reb.get("cash_action_eur")
        verb = "DEPLOY" if (cash or 0) > 0 else ("RAISE" if (cash or 0) < 0 else "—")
        lam = reb.get("tilt_lambda")
        out.append(f"  RULES     {reb['n_actions']} action(s), {reb['n_hold']} HOLD · "
                   f"deploy ratio {reb.get('deploy_ratio')} · cash {verb} €{abs(cash or 0):,.0f}"
                   + (f" · tilt λ={float(lam):.2f}" if lam == lam and lam is not None else ""))
        for r in reb.get("actions", []):
            out.append(f"     {str(r.get('rule_action')):<7}{str(r.get('sector_id')):<34}"
                       f"{str(r.get('etf') or '—'):<9}€{float(r.get('trade_eur') or 0):>8,.0f}  "
                       f"{r.get('reason')}")

    drag = reb.get("cash_drag_eur")
    if drag is not None and drag == drag:
        verdict = reb.get("cash_verdict")
        head = ("CASH DRAG" if verdict == "cost" else
                "CASH SAVED" if verdict == "saved" else "CASH     ")
        mdl, mdl_eur = reb.get("model_return_pct"), reb.get("cash_model_forgone_eur")
        line = (f"  {head} €{float(reb.get('cash_actual_eur') or 0):,.0f} idle since "
                f"{reb.get('cash_idle_since')} ({int(float(reb.get('cash_idle_days') or 0))}d)"
                f" · vs bench {float(reb.get('benchmark_return_pct') or 0):+.2f}% → "
                f"€{abs(float(drag)):,.0f} {'forgone' if float(drag) > 0 else 'avoided'}")
        if mdl is not None and mdl == mdl and mdl_eur is not None and mdl_eur == mdl_eur:
            line += (f" · vs the model book {float(mdl):+.2f}% → €{abs(float(mdl_eur)):,.0f} "
                     f"{'forgone' if float(mdl_eur) > 0 else 'avoided'}")
        out.append(line)
    if reb.get("shortfall_breached"):
        out.append(f"  SHORTFALL {float(reb.get('shortfall_pp') or 0):.1f}pp below the deploy rule "
                   f"for {int(reb.get('shortfall_runs') or 0)} consecutive review(s) — execute or "
                   f"log an override naming the shortfall")

    sc = d.get("scorecard") or {}
    if sc.get("rows"):
        out.append(f"  SCORECARD {sc.get('horizon_days')}d forward · "
                   f"~{sc.get('effective_windows')} independent window(s)")
        for r in sc["rows"]:
            edge = r.get("rule_edge_pp")
            out.append(f"     {str(r['action']):<9}n={r['n']:<4}"
                       f"edge {'—' if edge is None else f'{edge:+.2f}pp':<10}{r['verdict']}")
    elif sc:
        out.append(f"  SCORECARD nothing scoreable yet · {sc.get('n_pending')} row(s) pending a "
                   f"complete {sc.get('horizon_days')}d window")

    swaps = reb.get("swaps") or []
    if swaps:
        ev = (reb.get("rank_edge_evidence") or {}).get("line", "n/a")
        tf = sum(w["friction_eur"] for w in swaps)
        tm = sum(w["moved_eur"] for w in swaps)
        out.append(f"  SWAPS     €{tm:,.0f} rotated · friction €{tf:,.2f} · breakeven "
                   f"{tf / tm * 100:.2f}% over {swaps[0].get('horizon_days') or 63}d — {ev}")
        for w in swaps:
            out.append(f"     {w['from_sector']} → {w['to_sector']}  €{w['moved_eur']:,.0f}  "
                       f"b/e {w['breakeven_pct']:.2f}%")
    live = [p for p in (reb.get("partials") or []) if p.get("live")]
    near = [p for p in (reb.get("partials") or []) if not p.get("live")]
    if reb.get("partials"):
        out.append(f"  PARTIALS  {len(live)} rung(s) firing"
                   + (f": {', '.join(str(p['sector_id']) for p in live)}" if live else "")
                   + (f" · {len(near)} position(s) short of a rung" if near else ""))

    g = reb.get("gate") or {}
    if g:
        ic = g.get("ic")
        out.append(f"  GATE      after-tax gate {'ARMED' if g.get('armed') else 'stands aside'} · "
                   f"composite IC {'n/a' if ic is None else f'{ic:+.3f}'} · "
                   f"~{g.get('windows')} window(s) — {g.get('why')}")
    sp = reb.get("selection_prior")
    if sp:
        out.append(f"  PRIOR     {sp['note']}")

    for c in d.get("cap_breaches") or []:
        out.append(f"  CAP       {c['catalyst_id']} → {c['post_pct']:.1f}% after the table "
                   f"(cap {c['cap_pct']:.0f}%, over by €{c['over_by_eur']:,.0f}) · "
                   f"{', '.join(c['sectors'])}")

    att = d.get("attention") or {}
    if att:
        flagged = att.get("positions_needing_action") or []
        out.append(f"  ATTENTION {len(flagged)} flagged position(s){': ' + ', '.join(map(str, flagged)) if flagged else ''} · "
                   f"{att.get('stale_verdicts')} stale verdict(s) · "
                   f"{att.get('pending_lifecycle')} pending lifecycle")
    wl = d.get("work_list") or {}
    if wl:
        out.append(f"  WORK LIST must={len(wl.get('must_reverify') or [])} "
                   f"should={len(wl.get('should_reverify') or [])} "
                   f"decision-relevant sectors={len(wl.get('sectors_decision_relevant') or [])}")
    sc = d.get("scan")
    if sc:
        out.append(f"  SCAN      {sc['n_indicator_updates']} indicator · {sc['n_catalyst_reviews']} review · "
                   f"{sc['n_lifecycle']} lifecycle · {sc['n_new_catalysts']} new")

    for p in d.get("portfolios") or []:
        alpha = (f"  ·  execution alpha {p['execution_alpha_pp']:+}pp vs {p.get('execution_alpha_vs')}"
                 if p.get("execution_alpha_pp") is not None else "")
        mode = p.get("mode")
        mode = "—" if mode is None or mode != mode else str(mode)     # NaN: the real book has no mode
        out.append(f"  NAV       {str(p.get('portfolio_id')):<14}{mode:<10}"
                   f"{p.get('return_pct')}%  vs bench {p.get('vs_benchmark_pct')}pp{alpha}")

    top = d.get("ranking") or []
    if top:
        out.append("  TOP       " + " · ".join(f"{r.get('sector_id')} {r.get('composite')}"
                                               for r in top[:5]))
    if d.get("missing"):
        out.append("  MISSING   " + "; ".join(d["missing"]))
    out.append(f"\n  Full state: data/reports/run_{d['as_of'].replace('-', '')}.json")
    return "\n".join(out)


def write(d: dict) -> Path:
    _REPORTS.mkdir(parents=True, exist_ok=True)
    path = _REPORTS / f"run_{d['as_of'].replace('-', '')}.json"
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX — assemble the run's one state file")
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p.add_argument("--top", type=int, default=15, help="ranking rows to carry (default 15)")
    p.add_argument("--write", action="store_true", help="write data/reports/run_<date>.json")
    p.add_argument("--json", action="store_true", help="print the full dict instead of the summary")
    args = p.parse_args()

    d = build(as_of=args.date, top_n=args.top)
    if args.write:
        path = write(d)
        print(f"✅ wrote {path}  ({path.stat().st_size / 1024:.1f} KB)")
    if args.json:
        print(json.dumps(d, indent=2, ensure_ascii=False, default=str))
    else:
        print(render(d))


if __name__ == "__main__":
    main()
