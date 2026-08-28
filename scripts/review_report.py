#!/usr/bin/env python
"""CATALYX — deterministic skeleton of the review report.

WHY THIS EXISTS (v3 Phase 4, docs/PLAN_v3_lean_pipeline_rebalance.md §5):
    Most of `monthly_review_<date>.md` was Claude re-typing numbers it had just read: the ranking
    table, the NAV lines, the rebalance rows, the exposure ledger, the tax snapshot, the stale
    indicators. Every one of those is already in the lake or one CLI call away, so re-typing them
    costs tokens AND introduces the one error class a review cannot tolerate — a transcription
    that silently disagrees with the lake it came from. A number that Python owns is written by
    Python.

    What is left for Claude is the part that is actually reasoning, and it is marked in the output
    with explicit `<!-- CLAUDE: … -->` markers: the macro context, the executive summary and its
    non-obvious finding, the evidence line per position, and the reason behind any override.

    Everything here is READ-ONLY over the lake and the Tier-1 files. It runs no scorer, fetches no
    price and never persists — so it is cheap, repeatable, and safe to re-run after appending prose
    (use --stdout if you do not want the file rewritten).

Usage:
    uv run python scripts/review_report.py [--date YYYY-MM-DD] [--top 15] [--stdout]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MISSING = "_(no data — run `bash scripts/post_run.sh` first)_"


def _safe(fn, default=None):
    """A missing table must degrade to one honest line, never take down the report."""
    try:
        return fn()
    except Exception as exc:                                      # pragma: no cover - defensive
        return default if default is not None else f"_(unavailable: {exc})_"


def _table(rows: list[dict], cols: list[tuple[str, str]], fmt: dict | None = None) -> str:
    """Markdown table from records. `cols` is [(key, header)]; `fmt` optional per-key formatter."""
    if not rows:
        return _MISSING
    fmt = fmt or {}
    out = ["| " + " | ".join(h for _, h in cols) + " |",
           "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        cells = []
        for k, _ in cols:
            v = r.get(k)
            if v is None or v != v:                       # None or NaN — a gap, not a value
                cells.append("—")
            else:
                cells.append(str(fmt[k](v) if k in fmt else v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _num(digits: int = 1):
    def f(v):
        try:
            return f"{float(v):,.{digits}f}"
        except (TypeError, ValueError):
            return str(v)
    return f


# ── Sections ─────────────────────────────────────────────────────────────────

def section_ranking(top_n: int) -> str:
    from catalyx.store import lake_query

    rows = _safe(lambda: lake_query.latest_ranking(top_n=top_n), [])
    moves = _safe(lambda: lake_query.rank_moves(top_n=10), [])

    # A recorded run can predate a taxonomy change. Ranking a sector we cannot buy is not a
    # neutral row — it is the top of the table telling you to do something impossible — so the
    # ones that are no longer investable are MARKED, not silently dropped (the score is still a
    # real reading, and hiding it would also hide that the run needs rebuilding).
    investable = _safe(_investable, set()) or set()
    n_dead = 0
    for r in rows:
        if investable and r.get("sector_id") not in investable:
            r["sector_id"] = f"{r['sector_id']} ⚠"
            n_dead += 1
    cols = [("rank", "#"), ("sector_id", "sector"), ("primary_etf", "ETF"),
            ("composite", "composite"), ("catalyst_alignment", "catalyst"),
            ("momentum", "momentum"), ("crowding_risk", "crowding"),
            ("narrative_maturity", "maturity")]
    fmt = {k: _num(1) for k in ("composite", "catalyst_alignment", "momentum", "crowding_risk")}
    out = [_table(rows, cols, fmt)]
    if moves:
        # Only the moves worth a sentence. The full list is one `lake_query moves` away, and a
        # 30-row table of ±3 rank wobbles reads as signal when it is the ranking breathing.
        big = [m for m in moves if abs(float(m.get("delta") or 0)) >= 5][:12]
        if big:
            out += ["", "**Biggest rank moves this run** (|Δ| ≥ 5)", "",
                    _table(big, [("sector_id", "sector"), ("event_type", "event"),
                                 ("from_rank", "was"), ("to_rank", "now"), ("delta", "Δ")])]
    if n_dead:
        out += ["", f"> ⚠ {n_dead} of the top-{top_n} are **not investable today** (no buyable "
                    f"UCITS vehicle under the current taxonomy). This run predates the universe "
                    f"cut — re-score before treating the ranking as a candidate list."]
    return "\n".join(out)


def _investable() -> set:
    from catalyx.store import run_state
    return run_state.investable_sectors()


def section_portfolios() -> str:
    from catalyx.store import lake_query

    rows = _safe(lambda: lake_query.portfolio_compare(), [])
    return _table(rows, [("portfolio_id", "strategy"), ("kind", "kind"), ("date", "as of"),
                         ("nav", "NAV"), ("return_pct", "return %"),
                         ("benchmark_etf", "vs"), ("vs_benchmark_pct", "vs bench pp")],
                  {"nav": _num(2), "return_pct": _num(2), "vs_benchmark_pct": _num(2)})


def _rebalance_rows(lake_dir: Path | None = None) -> list[dict]:
    from catalyx.store import lake

    df = lake.read_table("rebalance", lake_dir=lake_dir)
    if df.empty or "run_id" not in df.columns:
        return []
    latest = sorted(df["run_id"].unique())[-1]
    df = df[df["run_id"] == latest]
    order = {a: i for i, a in enumerate(("SELL", "REDUCE", "TRIM", "ADD", "BUY", "HOLD"))}
    rows = df.to_dict("records")
    rows.sort(key=lambda r: (order.get(str(r.get("rule_action")), 9),
                             -abs(float(r.get("trade_eur") or 0))))
    return rows


def section_rebalance() -> str:
    rows = _safe(lambda: _rebalance_rows(), [])
    if not rows:
        return _MISSING
    cols = [("sector_id", "sector"), ("etf", "ETF"), ("rank", "rk"), ("target_pct", "target %"),
            ("actual_pct", "actual %"), ("gap_eur", "gap €"), ("rule_action", "**action**"),
            ("trade_eur", "trade €"), ("tax_eur", "CGT €"), ("net_edge_eur", "net edge €"),
            ("reason", "reason")]
    fmt = {k: _num(1) for k in ("target_pct", "actual_pct")}
    fmt.update({k: _num(0) for k in ("gap_eur", "trade_eur", "tax_eur", "net_edge_eur")})
    body = _table(rows, cols, fmt)
    depl = rows[0].get("deploy_ratio")
    note = (f"\n\nRule deployment ratio this run: **{float(depl):.0%}** of committed capital."
            if depl is not None else "")
    return body + note + (
        "\n\n> Actions come from `rebalance.decide_action`, fixed precedence "
        "`SELL > REDUCE > TRIM > ADD > BUY > HOLD`. `watch`/`monitor`/`consider` are not in the "
        "enum. Deviating is allowed **only** as a logged override "
        "(`rebalance override <sector> <action> --reason … --author …`).")


def section_overrides() -> str:
    from catalyx.execution import rebalance

    res = _safe(lambda: rebalance.score_overrides(), None)
    if not isinstance(res, dict):
        return str(res)
    lines = []
    if res["scored"]:
        lines += [_table(res["scored"],
                         [("sector_id", "sector"), ("author", "by"), ("rule_action", "rule"),
                          ("chosen_action", "chosen"), ("forward_return_pct", "ret %"),
                          ("override_edge_eur", "edge €"), ("rule_cost_eur", "rule cost €"),
                          ("reason", "reason")],
                         {"forward_return_pct": _num(2), "override_edge_eur": _num(0),
                          "rule_cost_eur": _num(0)}), ""]
    for author, t in sorted((res.get("tally") or {}).items()):
        lines.append(f"- **{author}**: {t['n']} scored · {t['wins']} beat the rule · "
                     f"net €{t['net_eur']:,.0f}")
    if res.get("pending"):
        lines.append(f"- {len(res['pending'])} logged but not yet scored "
                     f"(< 21 trading days — a shorter window is a coin, not evidence)")
    lines.append(f"- {res['claude']['why']}")
    if not res["scored"] and not res.get("pending"):
        lines = [res["note"]]
    return "\n".join(lines)


def section_positions() -> str:
    from catalyx.store import movement_repo

    book = _safe(lambda: movement_repo.positions(), {})
    holdings = (book or {}).get("holdings", [])
    if not holdings:
        return "_(no open positions)_"
    reb = {str(r.get("sector_id")): r for r in _safe(lambda: _rebalance_rows(), [])}
    met = _safe(_position_metric_rows, {}) or {}
    for h in holdings:
        r = reb.get(h.get("sector_id"), {})
        m = met.get(h.get("sector_id"), {})
        h["rule_action"] = r.get("rule_action")
        h["unrealized_pct"] = r.get("unrealized_pct")
        h["regime_state"] = r.get("regime_state")
        h["catalyst_freshness"] = r.get("catalyst_freshness")
        for k in ("days_held", "pnl_price_eur", "pnl_fx_eur",
                  "max_drawdown_from_peak_pct", "composite_drift"):
            h[k] = m.get(k)
    body = _table(holdings,
                  [("sector_id", "sector"), ("etf", "ETF"), ("invested_eur", "invested €"),
                   ("unrealized_pct", "P&L %"), ("pnl_price_eur", "price €"),
                   ("pnl_fx_eur", "FX €"), ("max_drawdown_from_peak_pct", "peak DD %"),
                   ("composite_drift", "score drift"), ("days_held", "days"),
                   ("regime_state", "regime"), ("catalyst_freshness", "freshness"),
                   ("rule_action", "**action**")],
                  {"invested_eur": _num(0), "unrealized_pct": _num(1),
                   "pnl_price_eur": _num(0), "pnl_fx_eur": _num(0),
                   "max_drawdown_from_peak_pct": _num(1), "composite_drift": _num(1)})
    return body + (
        "\n\n> `price €` + `FX €` (+ fees) = the EUR P&L: a non-EUR vehicle is two positions and "
        "only one of them was a thesis. `peak DD` is measured from the position's own high, not "
        "from cost. `score drift` is today's composite minus the one the pipeline gave that sector "
        "on the day it was bought — negative means the model has stopped believing it.")


def _position_metric_rows(lake_dir: Path | None = None) -> dict:
    from catalyx.store import lake

    df = lake.read_table("position_metrics", lake_dir=lake_dir)
    if df.empty or "run_id" not in df.columns:
        return {}
    df = df[df["run_id"] == sorted(df["run_id"].unique())[-1]]
    return {str(r["sector_id"]): r for r in df.to_dict("records")}


def section_exposure() -> str:
    from catalyx.store import lake_query

    rows = _safe(lambda: lake_query.catalyst_ledger(), [])
    return _table(rows, [("catalyst_id", "catalyst"), ("invested_eur", "invested €"),
                         ("realized_eur", "realized €"), ("n_sectors", "sectors")],
                  {"invested_eur": _num(0), "realized_eur": _num(0)})


def section_tax() -> str:
    from catalyx.execution import tax_engine
    from catalyx.store import movement_repo

    book = _safe(lambda: movement_repo.positions(), {}) or {}
    realized = float(book.get("realized_eur") or 0.0)
    rows = [f"| Realized gains YTD | €{realized:,.0f} |"]
    if realized > 0:
        t = tax_engine.compute_tax(gross_gain=realized, ytd_prior=0.0)
        rows.append(f"| Tax on realized YTD | €{t.tax_due:,.0f} |")
        rows.append(f"| Marginal bracket | {t.marginal_rate:.0%} |")
    else:
        rows.append("| Tax on realized YTD | €0 — no closing movement this calendar year |")
    return "| Metric | Value |\n|---|---|\n" + "\n".join(rows)


def section_freshness() -> str:
    from catalyx.scorer import freshness

    rows = _safe(lambda: freshness.overdue(), [])
    if not rows:
        return "_(no overdue indicators)_"
    return _table(rows, [("catalyst_id", "catalyst"), ("indicator_id", "indicator"),
                         ("last_date", "last observed"), ("check_frequency", "cadence"),
                         ("days_since", "days"), ("overdue_by", "overdue by")])


# ── Assembly ─────────────────────────────────────────────────────────────────

def build(as_of: str, top_n: int = 15) -> str:
    return f"""# CATALYX — Review {as_of}

<!-- Deterministic sections generated by scripts/review_report.py — do NOT retype these numbers.
     Regenerate with: uv run python scripts/review_report.py --date {as_of}
     The `<!-- CLAUDE: … -->` markers below are the only places prose belongs. -->

## Executive summary

<!-- CLAUDE: paste the `SUMMARY` line from the rebalance output VERBATIM as the first line —
     deployed % vs rule and floor · N rule actions · override tally. Then 3–5 bullets on the most
     important changes, including at least one NON-OBVIOUS finding. -->

## 0. Macro & geopolitical context

<!-- CLAUDE: the scan's C0 digest. Deltas vs the prior review. Any indicator whose live value
     disagrees with the stored YAML by > 10%. -->

## 1. Sector ranking (top {top_n})

{section_ranking(top_n)}

## 2. Model portfolios vs SPY

{section_portfolios()}

## 3. Rebalance — pipeline target vs the real book, in €, after tax

{section_rebalance()}

## 4. Open positions

{section_positions()}

<!-- CLAUDE: one line of EVIDENCE per position — which assumption you checked, what the source
     said, and the date. The action column above is already decided by the rule table; your
     evidence either supports it or justifies a logged override. -->

## 5. Overrides — deviations from the rule, and what they cost

{section_overrides()}

<!-- CLAUDE: for each override logged THIS run, one line: what the rule said, what you chose, and
     the evidence the rule was missing. -->

## 6. Catalyst exposure

{section_exposure()}

<!-- CLAUDE: flag any catalyst whose combined allocation breaches `correlated_catalyst_cap`
     (default 20%), with the breach amount. Flexible warning unless enforcement is "block". -->

## 7. Tax snapshot YTD

{section_tax()}

## 8. Overdue indicators

{section_freshness()}

## 9. Position-open recommendations

<!-- CLAUDE: candidates from the ranking with no open position — one context block each
     (why it ranks · crowding · entry timing · buyable UCITS vehicle · exposure fit ·
     recommendation). Then AskUserQuestion per candidate. Opening happens in /catalyx-open. -->

## 10. Taxonomy gap review

<!-- CLAUDE: one context block per pending proposal, then AskUserQuestion (promote/reject/defer).
     Never decide automatically. -->
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--top", type=int, default=15, help="sectors in the ranking table")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing the file")
    args = ap.parse_args()

    text = build(args.date, top_n=args.top)
    if args.stdout:
        print(text)
        return
    out = Path("data/reports") / f"review_{args.date.replace('-', '')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"✓ {out} ({len(text):,} bytes) — append prose only at the <!-- CLAUDE: … --> markers")


if __name__ == "__main__":
    main()
