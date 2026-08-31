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

    Everything here is READ-ONLY over the lake and the Tier-1 files. It runs no scorer and never
    persists — so it is cheap, repeatable, and safe to re-run after appending prose (use --stdout
    if you do not want the file rewritten). It reads a price in exactly two places, §5 and §5b,
    and unavoidably: an override's edge and a rule's forward return are *forward returns*, which
    no lake table can hold until the window closes. Everything else — including §3b/§3c/§4b/§4c —
    is rebuilt from the persisted rows with the engine's own pure functions, on purpose.

Usage:
    uv run python scripts/review_report.py [--date YYYY-MM-DD] [--top 15] [--stdout] [--check]
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
    # `return_pct` on the kind='real' row is TIME-WEIGHTED (contributions neutralized) — that is
    # what makes it comparable to the benchmark and to the model books, and why it does not equal
    # the broker's P&L. `mwr_pct` (money-weighted) is carried on the real book's final row.
    out = _table(rows, [("portfolio_id", "strategy"), ("kind", "kind"), ("date", "as of"),
                        ("nav", "NAV"), ("return_pct", "return % (TWR)"),
                        ("mwr_pct", "MWR %"),
                        ("benchmark_etf", "vs"), ("vs_benchmark_pct", "vs bench pp")],
                 {"nav": _num(2), "return_pct": _num(2), "mwr_pct": _num(2),
                  "vs_benchmark_pct": _num(2)})
    notes = ["_`vs bench pp` is the DIFFERENCE in index points, not the benchmark's own return — "
             "a book at −1% while the benchmark is at +4% reads −5pp here._",
             "_TWR neutralizes contributions (comparable to the benchmark); MWR is the IRR on "
             "your actual cash flows. Neither is the broker's mark-to-market vs cost._"]

    # Two curves that stop on different days are not comparable, and this table sorts them by
    # return as if they were. post_run.sh rebuilds the model NAVs each run; between runs they lag.
    dates = {str(r.get("date"))[:10] for r in rows if r.get("date")}
    if len(dates) > 1:
        notes.insert(0, f"> ⚠ these curves end on DIFFERENT dates ({', '.join(sorted(dates))}) — "
                        f"not comparable. Re-run `scripts/post_run.sh`.")

    # EXECUTION ALPHA — the real book against the model book it implements. The one number that
    # says whether the rule table or the human has been adding value, and nothing computed it.
    real = next((r for r in rows if r.get("kind") == "real"), None)
    if real is not None:
        a, note = real.get("execution_alpha_pp"), real.get("execution_alpha_note")
        if a is not None:
            who = ("the human's deviations ADDED value" if a > 0 else
                   "the model book would have done better")
            notes.insert(0, f"**Execution alpha vs `{real.get('execution_alpha_vs')}`: "
                            f"{a:+.2f}pp** — {who}.")
        elif note:
            notes.insert(0, f"_Execution alpha: {note}_")
    return out + "\n\n" + "\n\n".join(notes)


def _rebalance_rows(lake_dir: Path | None = None) -> list[dict]:
    from catalyx.store import lake

    df = lake.read_table("rebalance", lake_dir=lake_dir)
    if df.empty or "run_id" not in df.columns:
        return []
    latest = sorted(df["run_id"].unique())[-1]
    df = df[df["run_id"] == latest]
    from catalyx.execution.rebalance import PRECEDENCE
    order = {a: i for i, a in enumerate(PRECEDENCE)}
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
            ("trade_eur", "trade €"), ("tax_eur", "CGT €"), ("breakeven_pct", "b/e %"),
            ("reason", "reason")]
    fmt = {k: _num(1) for k in ("target_pct", "actual_pct")}
    fmt.update({k: _num(0) for k in ("gap_eur", "trade_eur", "tax_eur")})
    fmt["breakeven_pct"] = _num(2)
    body = _table(rows, cols, fmt)

    # CASH is a position and is priced like one; TOTAL proves the book closes on 100%. Rebuilt
    # from the book-level constants `rebalance.persist` carries on every row, so the report needs
    # no re-run of the engine. Absent on partitions written before v4 — then the rows print alone.
    r0 = rows[0]
    total = r0.get("book_total_capital_eur")
    if total:
        tgt_pct = sum(float(r.get("target_pct") or 0) for r in rows)
        act_pct = sum(float(r.get("actual_pct") or 0) for r in rows)
        cash_t = float(r0.get("book_cash_target_eur") or 0)
        cash_a = float(r0.get("book_cash_actual_eur") or 0)
        act_eur = float(r0.get("book_cash_action_eur") or 0)
        verb = "DEPLOY" if act_eur > 0 else ("RAISE" if act_eur < 0 else "HOLD")
        body += (f"\n| **CASH** | — | — | {cash_t / total * 100:.1f} | "
                 f"{cash_a / total * 100:.1f} | {-act_eur:,.0f} | **{verb}** | {-act_eur:,.0f} "
                 f"| 0 | — | rule holds {cash_t / total * 100:.0f}% in cash; you hold "
                 f"{cash_a / total * 100:.0f}% |")
        body += (f"\n| **TOTAL** | | | {tgt_pct + cash_t / total * 100:.1f} | "
                 f"{act_pct + cash_a / total * 100:.1f} | 0 | | | | | |")

    depl = r0.get("deploy_ratio")
    note = (f"\n\nRule deployment ratio this run: **{float(depl):.0%}** of committed capital."
            if depl is not None else "")
    note += _tilt_note(r0.get("book_tilt_lambda"))
    note += _gate_note()
    return body + note + (
        "\n\n> `b/e %` is the **breakeven**: the friction of the trade (CGT + spread) as a "
        "percentage of the capital it moves — what the destination must outperform the source by "
        "for the rotation to have been worth making. It replaces `net edge €`, which multiplied "
        "the trade by a rank-bucket mean return one noisy window old and printed ±€1 beside a "
        "real tax bill. The forecast is still in the lake; it no longer drives the table."
        "\n\n> Actions come from `rebalance.decide_action`, fixed precedence "
        "`SELL > REDUCE > TRIM > RE-SCORE > ADD > BUY > HOLD`. `RE-SCORE` moves no money — it is "
        "what a rank-based SELL degrades to when the sector was absent from too many recent "
        "runs to have a rank worth selling on. `watch`/`monitor`/`consider` are not in the "
        "enum. Deviating is allowed **only** as a logged override "
        "(`rebalance override <sector> <action> --reason … --author …`).")


def section_swaps() -> str:
    """The swap ledger — what each rotation costs and the hurdle it must clear (plan v4 A3).

    Built from the PERSISTED rebalance rows by the same pure functions the engine uses, not by
    re-running `rebalance.build()` — this file fetches no price and runs no scorer, and the engine
    reads VIX. Same inputs, same output, none of the cost.
    """
    from catalyx.config import weights
    from catalyx.execution import rebalance
    from catalyx.scorer import calibration

    rows = _safe(lambda: _rebalance_rows(), [])
    if not rows:
        return _MISSING
    cfg = weights.rebalance_rules()
    exp = _safe(lambda: calibration.expected_returns(), {})
    swaps = _safe(lambda: rebalance.swap_ledger(rows, cfg,
                                                horizon_days=(exp or {}).get("horizon_days")), [])
    if not swaps:
        return "_(no rotation this run — every sale is funded to cash, priced on its own row)_"
    ev = _safe(lambda: rebalance.rank_edge_evidence(exp, cfg), {})
    pairs = [{**w, "pair": f"{w['from_sector']} → {w['to_sector']}"} for w in swaps]
    cols = [("pair", "swap"), ("moved_eur", "moved €"), ("tax_eur", "CGT €"),
            ("spread_eur", "spread €"), ("friction_eur", "friction €"),
            ("breakeven_pct", "**breakeven %**")]
    fmt = {"moved_eur": _num(0)}
    fmt.update({k: _num(2) for k in ("tax_eur", "spread_eur", "friction_eur", "breakeven_pct")})
    h = swaps[0].get("horizon_days") or 63
    total_f = sum(w["friction_eur"] for w in swaps)
    total_m = sum(w["moved_eur"] for w in swaps)
    return _table(pairs, cols, fmt) + (
        f"\n\n**Total:** €{total_m:,.0f} rotated · friction €{total_f:,.2f} · weighted breakeven "
        f"**{rebalance.breakeven_pct(total_f, total_m):.2f}%** over {h} days."
        f"\n\n**Evidence for the spread these swaps are betting on:** "
        f"{(ev or {}).get('line', 'n/a')}"
        f"\n\n> Every input above is observable today — tax bracket, spread, notional. Nothing is "
        f"forecast. The rule fires on its own trigger (rank-out, regime, exit watcher), never on "
        f"an expected return; the breakeven is the claim being accepted — that the rank signal is "
        f"worth more than the friction over {h} days. It is checkable against the realized spread "
        f"one horizon later, which a point estimate is not.")


def section_partials() -> str:
    """Distance to each partial-sale rung, so a partial never arrives as a surprise (A5)."""
    from catalyx.config import weights
    from catalyx.execution import rebalance

    rows = _safe(lambda: _rebalance_rows(), [])
    if not rows:
        return _MISSING
    cfg = weights.rebalance_rules()
    parts = _safe(lambda: rebalance.partial_rungs(rows, cfg), [])
    if not parts:
        return _MISSING

    out = []
    for pr in parts:
        lad = pr.get("ladder") or {}
        if not lad:
            lad_s = "no ladder configured"
        elif lad.get("rank_ok"):
            lad_s = "model no longer leads it ✓"
        elif pr.get("rank") is None:
            lad_s = "rank unknown"
        else:
            lad_s = f"still a leader (rank {pr['rank']} < {lad.get('rank_min')})"
        gain_s = ("gain MET" if lad.get("gain_met") else
                  f"needs {lad['need_gain_pct']:+.1f}%" if lad.get("need_gain_pct") is not None
                  else "gain unknown")
        ow = pr.get("overweight") or {}
        out.append({
            "sector_id": pr["sector_id"],
            "unrealized_pct": pr.get("unrealized_pct"), "rank": pr.get("rank"),
            "ladder": f"{gain_s} · {lad_s}",
            "overweight": (f"MET ({ow.get('over_pp'):+.1f}pp)" if ow.get("met")
                           else f"needs {ow.get('need_pp'):+.1f}pp more"),
            "live": f"**{pr['action']} live**" if pr.get("live") else "—",
        })
    lab = next((pr["ladder"]["label"] for pr in parts if pr.get("ladder")), "—")
    reduce_pct = float((cfg.get("reduce_if_any", {}) or {}).get("reduce_fraction", 0.5)) * 100
    ladder_trim = next((float(r.get("trim_fraction")) for r in (cfg.get("profit_ladder") or [])
                        if r.get("trim_fraction")), None)
    body = _table(out, [("sector_id", "sector"), ("unrealized_pct", "gain %"), ("rank", "rk"),
                        ("ladder", f"ladder rung — {lab}"),
                        ("overweight", "overweight rung"), ("live", "firing?")],
                  {"unrealized_pct": _num(1)})
    return body + (
        f"\n\n> **The whole partial-sale vocabulary:** SELL = 100% of the line · REDUCE = "
        f"{reduce_pct:.0f}% · TRIM = back to target"
        + (f" (or {ladder_trim * 100:.0f}% on a ladder rung)" if ladder_trim else "")
        + ". The two rungs are reported separately because they are measured in different units — "
        "points of TOTAL capital above target, versus % gain ON the position — and the ladder's "
        "rank leg is not a quality test: it fires once the model has STOPPED leading the name, so "
        "a rank-1 position failing it is the rule working.")


def _tilt_note(lam) -> str:
    """How much of the model's conviction tilt was EARNED this run (plan v4 §3 B1).

    Read straight off the persisted target book, never recomputed: the report must describe the
    sizing regime that actually produced the table above it, including on an old run.
    """
    if lam is None or lam != lam:
        return ""
    lam = float(lam)
    head = f"\n\n**Tilt λ = {lam:.2f}** — "
    if lam < 0.05:
        return head + (
            "the model selects the NAMES; the sizing is neutral (inverse-vol) because the "
            "ranking's measured rank IC has not earned a tilt. **Gross deployment is unchanged** "
            "— λ moves how the working capital is split, never how much of it is at work."
            "\n\n> λ = clamp(IC / target, 0, 1) × n_eff/(n_eff + prior). A negative IC clamps to "
            "zero and never inverts the book: shorting your own ranking on one non-overlapping "
            "window is a superstition with a minus sign. As independent windows accumulate and "
            "the IC turns positive, the conviction tilt returns — earned, not assumed.")
    return head + (f"{lam:.0%} of the model's conviction tilt is applied; the remaining "
                   f"{1 - lam:.0%} is shrunk toward the neutral book by the measured rank IC "
                   f"and the number of independent windows behind it.")


def _gate_note() -> str:
    """Whether the after-tax gate may BLOCK a sale this run — and, when not, exactly why not.

    Worth a line in the report because the gate NOT firing is a decision too: it is the reason
    the rule actions above stand unmodified, and the reason a negative composite IC has not been
    allowed to invert the profit-taking rule (plan v4 §3 B2).
    """
    from catalyx.config import weights
    from catalyx.execution import rebalance
    from catalyx.scorer import calibration

    g = _safe(lambda: rebalance.gate_status(calibration.expected_returns(),
                                            calibration.composite_ic(),
                                            weights.rebalance_rules()), None)
    if not isinstance(g, dict):
        return ""
    ic = g.get("ic")
    return (f"\n\n**After-tax gate: {'ARMED' if g.get('armed') else 'stands aside'}** — composite "
            f"rank IC {'n/a' if ic is None else f'{ic:+.3f}'}"
            + (f" (se {g['ic_se']:.3f} → {g.get('ic_verdict')})" if g.get("ic_se") else "")
            + f", ~{g.get('windows')} independent window(s). {g.get('why')}."
            + ("\n\n> The gate may block a taxable sale whose expected edge does not cover CGT + "
               "spread. It arms only on a joint condition — enough independent windows, |IC| above "
               "a floor, **and a positive sign**. A negative IC disables it rather than inverting "
               "it: a ranking that orders backwards is a scoring problem to fix, never a licence "
               "to trade the ranking upside down."))


def section_inaction() -> str:
    """What NOT acting cost — the other half of a ledger that has only ever priced acting.

    Every row above carries its friction to the cent. Until v4.6 the cost of leaving €6,954 idle
    for 73 days was printed nowhere, and that asymmetry is a standing thumb on the scale for
    doing nothing. Read entirely from the persisted book row — this file fetches no price.
    """
    rows = _safe(lambda: _rebalance_rows(), [])
    if not rows:
        return "_no rebalance run recorded._"
    r0 = rows[0]
    out = []
    drag, idle_since = r0.get("book_cash_drag_eur"), r0.get("book_cash_idle_since")
    cash = float(r0.get("book_cash_actual_eur") or 0.0)
    if drag is not None and drag == drag:
        days = int(float(r0.get("book_cash_idle_days") or 0))
        bench = float(r0.get("book_bench_return_pct") or 0.0)
        friction = min((float(x.get("cost_drag_eur") or 0.0) for x in rows
                        if str(x.get("rule_action")) not in ("HOLD", "RE-SCORE")
                        and float(x.get("cost_drag_eur") or 0.0) > 0), default=None)
        out.append(f"**CASH DRAG** — €{cash:,.0f} idle since {idle_since} ({days}d). "
                   f"The benchmark returned **{bench:+.2f}%** over that window → "
                   f"**€{float(drag):,.0f} forgone**."
                   + (f" For scale, the smallest friction blocking a trade below is "
                      f"€{friction:,.2f}." if friction else ""))
    pp, runs = r0.get("book_shortfall_pp"), r0.get("book_shortfall_runs")
    if pp is not None and pp == pp:
        breached = bool(r0.get("book_shortfall_breached"))
        out.append(f"**DEPLOYMENT SHORTFALL** — {float(pp):.1f}pp below the rule for "
                   f"{int(float(runs or 0))} consecutive recorded review(s). "
                   + ("**The persistence rule is breached**: execute the rows above, or log an "
                      "override naming the shortfall itself. A shortfall that survives two "
                      "reviews without a written reason is exactly what the deployment ratio was "
                      "built to make visible." if breached else
                      "Below the persistence limit."))
    unrec = _safe(_unrecorded_rows, [])
    if unrec:
        out.append(f"\n**UNRECORDED DEVIATIONS ({len(unrec)})** — rows a previous run "
                   f"recommended that produced no movement and no override. Logged as DEFER by "
                   f"`unrecorded` and priced ~21 trading days later, exactly like a deliberate "
                   f"deviation.\n")
        out.append("| run | sector | rule said | logged |")
        out.append("|---|---|---|---|")
        for u in unrec[:15]:
            out.append(f"| {u.get('run_id')} | {u.get('sector_id')} | {u.get('rule_action')} | "
                       f"{str(u.get('logged_at'))[:10]} |")
    return "\n".join(out) if out else "_nothing to price._"


def _unrecorded_rows() -> list[dict]:
    from catalyx.store import lake

    df = lake.read_table("override_log")
    if df.empty or "author" not in df.columns:
        return []
    df = df[df["author"] == "unrecorded"]
    return df.sort_values("logged_at", ascending=False).to_dict("records") if not df.empty else []


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


def section_scorecard() -> str:
    """What the TABLE's own actions earned — the other half of the override ledger (§3 B4).

    Overrides have been scored since v3; the rules they deviate FROM never were. A table whose
    own record is never priced keeps its authority by never being tested, which is exactly the
    property this project refuses everywhere else.
    """
    from catalyx.execution import rebalance

    res = _safe(lambda: rebalance.score_decisions(), None)
    if not isinstance(res, dict):
        return "_scorecard unavailable._"
    sc = res.get("scorecard") or {}
    if not sc.get("rows"):
        return (f"_Nothing scoreable yet: {len(res.get('pending') or [])} recorded row(s), no "
                f"complete {res.get('horizon_days')}d forward window. The first verdicts land "
                f"~{res.get('horizon_days')} days after the earliest recorded run._")
    body = ["| action | n | mean fwd % | vs HOLD (pp) | rule edge (pp) | verdict |",
            "|---|---:|---:|---:|---:|---|"]
    for r in sc["rows"]:
        def _f(v, suf=""):
            return "—" if v is None else f"{v:+.2f}{suf}"
        body.append(f"| **{r['action']}** | {r['n']} | {_f(r['mean_forward_pct'])} | "
                    f"{_f(r['vs_hold_pp'])} | {_f(r['rule_edge_pp'])} | {r['verdict']} |")
    body.append("")
    body.append(f"> {sc.get('note')} A positive **rule edge** means the rule was right: the "
                f"forward return is signed by the direction the rule moved money, so a SELL "
                f"scores well when the vehicle then fell. The scorecard is evidence for a config "
                f"edit, never an edit — `rebalance_rules.frozen` still means a threshold moves by "
                f"a commit and a CHANGELOG line.")
    if res.get("pending"):
        body.append(f"\n> {len(res['pending'])} row(s) still pending a complete window.")
    return "\n".join(body)


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


def section_risk() -> str:
    """Where the book's volatility actually comes from (plan v4 A4).

    Capital share answers "how much did I spend"; risk share answers "how much of what can go
    wrong is this line", and on this book they are far apart. Read from the persisted
    `position_metrics` / `book_metrics` partitions — no engine run, no price fetch.
    """
    from catalyx.store import lake

    met = _safe(_position_metric_rows, {}) or {}
    rows = [m for m in met.values() if m.get("risk_contribution_pct") is not None]
    if not rows:
        return ("_(no risk decomposition this run — it needs at least two positions with an "
                "overlapping price history)_")
    rows.sort(key=lambda r: -(r.get("risk_contribution_pct") or 0))
    for r in rows:
        cap, rc = r.get("capital_pct_of_book"), r.get("risk_contribution_pct")
        r["note"] = ("**negative** — anticorrelated enough to LOWER book vol" if rc < 0
                     else f"{rc / cap:.1f}× its capital share" if cap and rc >= cap * 1.3 else "—")
    body = _table(rows, [("sector_id", "sector"), ("etf", "ETF"),
                         ("capital_pct_of_book", "capital %"),
                         ("vol_common_window_pct", "vol %"),
                         ("risk_contribution_pct", "**risk %**"), ("note", "note")],
                  {k: _num(1) for k in ("capital_pct_of_book", "vol_common_window_pct",
                                        "risk_contribution_pct")})
    bm = _safe(lambda: lake.read_table("book_metrics"), None)
    tail = ""
    try:
        if bm is not None and not bm.empty:
            b = bm[bm["run_id"] == sorted(bm["run_id"].unique())[-1]].iloc[0]
            tail = (f"\n\n**Book:** vol {float(b['book_vol_from_cov_pct']):.1f}% over "
                    f"{int(b['risk_window_days'])} common trading days · HHI {float(b['hhi']):.0f} "
                    f"→ **effective N {float(b['effective_n']):.1f}** on {int(b['n_positions'])} "
                    f"positions.")
    except Exception:                                          # pragma: no cover - defensive
        tail = ""
    return body + tail + (
        "\n\n> `RC_i = w_i·(Σw)_i / σ_p`, summing to 100% by construction — the shares are "
        "exhaustive and comparable. Two €500 lines are not two equal bets, and every output "
        "before this described them as the same size. A **negative** contribution is a real "
        "property, not an artefact: that position is lowering total volatility, which is the "
        "first thing to defend before trimming it. Measurement only — nothing here recommends.")


def _position_metric_rows(lake_dir: Path | None = None) -> dict:
    from catalyx.store import lake

    df = lake.read_table("position_metrics", lake_dir=lake_dir)
    if df.empty or "run_id" not in df.columns:
        return {}
    df = df[df["run_id"] == sorted(df["run_id"].unique())[-1]]
    return {str(r["sector_id"]): r for r in df.to_dict("records")}


def section_exposure() -> str:
    """Combined exposure per catalyst against the correlated-catalyst cap.

    The `sectors` column rendered `—` on every row for as long as it existed: it asked for
    `n_sectors`, and `catalyst_ledger` returns `sectors`. And the % of capital and the cap — the
    only two numbers that make this a CHECK rather than a list — were never here at all.
    """
    from catalyx.config import weights
    from catalyx.store import lake_query

    rows = _safe(lambda: lake_query.catalyst_ledger(), [])
    cap = weights.correlated_catalyst_cap()
    total = weights.total_capital_eur() or 0.0
    for r in rows:
        inv = float(r.get("invested_eur") or 0.0)
        r["pct_of_capital"] = round(inv / total * 100.0, 2) if total else None
        r["headroom_eur"] = round(cap["max_combined_pct"] / 100.0 * total - inv, 2) \
            if total else None
        over = (r["pct_of_capital"] or 0) > cap["max_combined_pct"]
        r["pct_str"] = (f"{r['pct_of_capital']:.1f} ⚠ OVER CAP" if over
                        else f"{r['pct_of_capital']:.1f}") if r["pct_of_capital"] is not None else "—"
    out = _table(rows, [("catalyst_id", "catalyst"), ("invested_eur", "invested €"),
                        ("pct_str", "% of capital"), ("headroom_eur", "headroom €"),
                        ("realized_eur", "realized €"), ("sectors", "sectors")],
                 {"invested_eur": _num(0), "realized_eur": _num(0), "headroom_eur": _num(0)})
    return out + (f"\n\n_Cap: **{cap['max_combined_pct']:.0f}%** combined per shared primary "
                  f"catalyst (`correlated_catalyst_cap`, enforcement `{cap['enforcement']}`). "
                  f"Headroom is what a NEW position in that catalyst may still take._")


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
    # `check_frequency` / `overdue_by` were never keys of `freshness.overdue()` — both columns
    # rendered blank on every row. The cadence is `cadence`; "overdue by" is derived.
    for r in rows:
        d, lim = r.get("days_since"), r.get("threshold_days")
        r["overdue_by"] = (f"{int(d) - int(lim)}d" if d is not None and lim is not None else None)
        if r.get("cadence_mislabeled"):
            r["cadence"] = f"{r.get('cadence')} ⚠mislabel"
    return _table(rows, [("catalyst_id", "catalyst"), ("indicator_id", "indicator"),
                         ("last_date", "last observed"), ("cadence", "cadence"),
                         ("days_since", "days"), ("threshold_days", "limit"),
                         ("overdue_by", "overdue by")])


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

## 3b. ¿Renta vender? — the swap ledger

{section_swaps()}

## 3c. ¿Parciales? — distance to the next rung

{section_partials()}

## 4. Open positions

{section_positions()}

<!-- CLAUDE: one line of EVIDENCE per position — which assumption you checked, what the source
     said, and the date. The action column above is already decided by the rule table; your
     evidence either supports it or justifies a logged override. -->

## 4b. Risk contribution — where the volatility actually comes from

{section_risk()}

## 4c. The cost of not acting

{section_inaction()}

## 5. Overrides — deviations from the rule, and what they cost

{section_overrides()}

<!-- CLAUDE: for each override logged THIS run, one line: what the rule said, what you chose, and
     the evidence the rule was missing. -->

## 5b. Rule scorecard — what the table's own actions earned

{section_scorecard()}

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


# ── C5 — the language rule, enforced in the generator (plan v4 §4 C5) ───────
#
# `BANNED_ACTION_WORDS` is enforced on `rebalance`'s own output, where hedging was never the
# problem: a rule table cannot hedge. Hedging lives in the PROSE appended at the
# `<!-- CLAUDE: … -->` markers, and there it has been unpoliced. "Revisit next cycle" in a
# sentence is a DEFER (`overrides.defer_is_an_override`) — it must be a logged row with an author
# and a reason, not an adverb. This lint fails the report instead of trusting the narrator.

HEDGES = (
    "watch", "monitor", "consider", "optional", "maybe", "evaluate",   # BANNED_ACTION_WORDS
    "keep an eye", "revisit", "next cycle", "wait and see", "for now", "let's see",
    "no rush", "in due course", "at some point", "cautious", "prudent",
)

# The sections where a verdict lives. Prose elsewhere (macro context, catalyst narrative) is
# allowed to be tentative — that is analysis, not a decision.
LINT_SECTIONS = ("executive summary", "open positions", "the cost of not acting",
                 "overrides", "position-open recommendations")


def lint_prose(text: str) -> list[dict]:
    """Hedges inside the sections where a decision is stated. [] when the report is clean.

    Skips tables, blockquotes, headings, code and the instruction comments themselves: a `|` row
    is generated, a `>` line is a generated footnote, and neither is where a person hedges.
    """
    findings, section, in_code = [], "", False
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if line.startswith("#"):
            section = line.lstrip("# ").lower()
            continue
        if in_code or not line or line.startswith(("|", ">", "<!--", "-->", "_")):
            continue
        if not any(k in section for k in LINT_SECTIONS):
            continue
        low = line.lower()
        for w in HEDGES:
            if w in low:
                findings.append({"line": i, "section": section, "hedge": w, "text": line})
                break
    return findings


def check(path: Path) -> int:
    """Exit code for `--check`: 0 clean, 1 hedged. Prints the offending lines."""
    if not path.exists():
        print(f"✗ {path} does not exist — generate it first")
        return 1
    findings = lint_prose(path.read_text(encoding="utf-8"))
    if not findings:
        print(f"✓ {path} — no hedged verdicts in the decision sections")
        return 0
    print(f"✗ {path} — {len(findings)} hedged line(s) in sections where a decision is stated.\n"
          f"  A verdict that does not move money is HOLD, said once. \"Revisit next cycle\" is a "
          f"DEFER: log it as an override with an author and a reason, do not write it as an "
          f"adverb.\n")
    for f in findings:
        print(f"  L{f['line']:<5} [{f['hedge']}] §{f['section']}\n         {f['text'][:150]}")
    return 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--top", type=int, default=15, help="sectors in the ranking table")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing the file")
    ap.add_argument("--check", action="store_true",
                    help="lint the COMMITTED report for hedged verdicts (plan v4 C5) and exit")
    args = ap.parse_args()

    out_path = Path("data/reports") / f"review_{args.date.replace('-', '')}.md"
    if args.check:
        raise SystemExit(check(out_path))

    text = build(args.date, top_n=args.top)
    if args.stdout:
        print(text)
        return
    out = out_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"✓ {out} ({len(text):,} bytes) — append prose only at the <!-- CLAUDE: … --> markers")


if __name__ == "__main__":
    main()
