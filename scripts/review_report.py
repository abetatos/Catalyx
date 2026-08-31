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
        big = [m for m in moves if abs(float(m.get("delta") or 0)) >= 5]
        ups = sum(1 for m in big if float(m.get("delta") or 0) > 0)
        # A one-directional sweep is the DENOMINATOR moving (universe cut, scoring edit), not the
        # world. Printing it as twelve independent "rank_up" findings invites reading it as
        # twelve findings. Say what it is, once, and stop showing the rows.
        if len(big) >= 5 and (ups == len(big) or ups == 0):
            direction = "UP" if ups else "DOWN"
            out += ["", f"> ⚠ all {len(big)} moves of |Δ| ≥ 5 this run are {direction} — that is "
                        f"the denominator moving (universe or scoring change), not {len(big)} "
                        f"independent findings. Rows withheld; see `lake_query moves`."]
        elif big:
            out += ["", "**Biggest rank moves this run** (|Δ| ≥ 5)", "",
                    _table(big[:12], [("sector_id", "sector"), ("event_type", "event"),
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
    notes = ["_`vs bench pp` = difference in points (book −1% vs benchmark +4% reads −5pp). TWR "
             "neutralizes contributions; MWR is the IRR on your cash flows; neither is the "
             "broker's mark vs cost._"]

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
    # NaN → None at the parquet boundary, once. Downstream code checks `is None`, and a NaN that
    # survives it renders as a value ("rank nan") instead of the gap it is.
    rows = [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}
            for r in df.to_dict("records")]
    rows.sort(key=lambda r: (order.get(str(r.get("rule_action")), 9),
                             -abs(float(r.get("trade_eur") or 0))))
    return rows


def section_rebalance() -> str:
    rows = _safe(lambda: _rebalance_rows(), [])
    if not rows:
        return _MISSING
    # `rk` is the rank in this run's FULL ranking — one semantic, no per-row fallback. The
    # model-book rank stays internal; a column that switches meaning per row obscures both.
    cols = [("sector_id", "sector"), ("etf", "ETF"), ("score_rank", "rk"), ("target_pct", "target %"),
            ("actual_pct", "actual %"), ("gap_eur", "gap €"), ("rule_action", "**action**"),
            ("trade_eur", "trade €"), ("tax_eur", "CGT €"), ("breakeven_pct", "b/e %"),
            # The age of the evidence, next to the reason that rests on it (plan v5 E1).
            ("data_age", "data"), ("reason", "reason")]
    fmt = {k: _num(1) for k in ("target_pct", "actual_pct")}
    fmt.update({k: _num(0) for k in ("gap_eur", "trade_eur", "tax_eur", "score_rank")})
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
                 f"| 0 | — | — | rule holds {cash_t / total * 100:.0f}% in cash; you hold "
                 f"{cash_a / total * 100:.0f}% |")
        body += (f"\n| **TOTAL** | | | {tgt_pct + cash_t / total * 100:.1f} | "
                 f"{act_pct + cash_a / total * 100:.1f} | 0 | | | | | | |")

    depl = r0.get("deploy_ratio")
    note = (f"\n\nRule deployment ratio this run: **{float(depl):.0%}** of committed capital."
            if depl is not None else "")
    note += _tilt_note(r0.get("book_tilt_lambda"))
    note += _gate_note()
    note += _prior_note(r0.get("book_tilt_lambda"))
    # The rationale behind each column lives in the module docstrings and the CHANGELOG — the
    # report states semantics once, in one line, and does not re-teach them every run.
    return body + note + (
        "\n\n_`rk` = rank in this run's full ranking · `b/e %` = friction (CGT + spread) ÷ capital "
        "moved — what the destination must beat the source by · `data` = age of the catalyst "
        "evidence behind the score (`stale`/`blind` qualify the row and send it to the scan's "
        "refresh list; they never veto it — old data is a reason to re-verify, not to stop "
        "acting). Actions: fixed precedence "
        "`SELL > REDUCE > TRIM > RE-SCORE > ADD > BUY > HOLD`; deviating only as a logged override "
        "(`rebalance override <sector> <action> --reason … --author …`)._")


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
        f"\n\n_Every input is observable today; the breakeven is checkable against the realized "
        f"spread one horizon ({h}d) later._")


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
            lad_s = "no rank this run"
        else:
            lad_s = f"still a leader (rank {pr['rank']} < {lad.get('rank_min')})"
        gain_s = ("gain MET" if lad.get("gain_met") else
                  f"needs {lad['need_gain_pct']:+.1f}%" if lad.get("need_gain_pct") is not None
                  else "gain unknown")
        ow = pr.get("overweight") or {}
        out.append({
            "sector_id": pr["sector_id"],
            "unrealized_pct": pr.get("unrealized_pct"),
            "ladder": f"{gain_s} · {lad_s}",
            "overweight": (f"MET ({ow.get('over_pp'):+.1f}pp)" if ow.get("met")
                           else f"needs {ow.get('need_pp'):+.1f}pp more"),
            "live": f"**{pr['action']} live**" if pr.get("live") else "—",
        })
    lab = next((pr["ladder"]["label"] for pr in parts if pr.get("ladder")), "—")
    reduce_pct = float((cfg.get("reduce_if_any", {}) or {}).get("reduce_fraction", 0.5)) * 100
    ladder_trim = next((float(r.get("trim_fraction")) for r in (cfg.get("profit_ladder") or [])
                        if r.get("trim_fraction")), None)
    body = _table(out, [("sector_id", "sector"), ("unrealized_pct", "gain %"),
                        ("ladder", f"ladder rung — {lab}"),
                        ("overweight", "overweight rung"), ("live", "firing?")],
                  {"unrealized_pct": _num(1)})
    return body + (
        f"\n\n_SELL = 100% of the line · REDUCE = {reduce_pct:.0f}% · TRIM = back to target"
        + (f" ({ladder_trim * 100:.0f}% on a ladder rung)" if ladder_trim else "")
        + ". The rank leg fires once the model has STOPPED leading the name._")


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
            "sizing is neutral (inverse-vol); the measured rank IC has not earned a conviction "
            "tilt. Gross deployment is unchanged — λ only splits the working capital.")
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
            + f", ~{g.get('windows')} independent window(s). {g.get('why')}.")


def _prior_note(lam) -> str:
    """Name what the deployment pressure rests on when the ranking's own edge is not it (v5 F1).

    §3 asks for eight trades toward a ranking the same page reports as ordering nothing. Both can
    be true — being invested in leaders is a different prior from THIS ranking working — but until
    now the document never separated them, so it read as self-contradictory.
    """
    from catalyx.config import weights
    from catalyx.execution import rebalance
    from catalyx.scorer import calibration

    ev = _safe(lambda: rebalance.rank_edge_evidence(calibration.expected_returns(),
                                                    weights.rebalance_rules()), None)
    if not isinstance(ev, dict):
        return ""
    sp = rebalance.selection_prior(ev, lam)
    return "" if not sp else f"\n\n> **{sp['note']}**"


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
        # Two counterfactuals, and a headline that flips with the sign (plan v5 F3): the benchmark
        # answers "should I have been invested?", the model book answers the question actually on
        # trial — "should I have executed THIS table?". A ledger that can only reprove is not one.
        mdl, mdl_eur = r0.get("book_model_return_pct"), r0.get("book_cash_model_forgone_eur")
        verdict = r0.get("book_cash_verdict")
        head = ("CASH DRAG" if verdict == "cost" else
                "CASH THAT SAVED YOU" if verdict == "saved" else "CASH")
        out.append(f"**{head}** — €{cash:,.0f} idle since {idle_since} ({days}d)."
                   + (f" For scale, the smallest friction blocking a trade below is "
                      f"€{friction:,.2f}." if friction else ""))
        out.append(f"- vs benchmark (**{bench:+.2f}%**) → €{abs(float(drag)):,.0f} "
                   f"{'forgone' if float(drag) > 0 else 'avoided'}")
        if mdl is not None and mdl == mdl and mdl_eur is not None and mdl_eur == mdl_eur:
            out.append(f"- vs the `{r0.get('strategy')}` model book (**{float(mdl):+.2f}%**) → "
                       f"€{abs(float(mdl_eur)):,.0f} "
                       f"{'forgone' if float(mdl_eur) > 0 else 'avoided'} — **the policy this "
                       f"table implements**, and the one the deployment rule argues for")
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
    # One line per run, not one row per deviation: what the review acts on is "the last run asked
    # for N things and got none of them", and the 10-row list said that ten times.
    unrec = _safe(_unrecorded_rows, [])
    if unrec:
        by_run: dict[str, list[dict]] = {}
        for u in unrec:
            by_run.setdefault(str(u.get("run_id")), []).append(u)
        out.append(f"**UNRECORDED DEVIATIONS ({len(unrec)})** — recommended, never executed, never "
                   f"overridden. Logged as DEFER by `unrecorded` and priced ~21 trading days "
                   f"later, exactly like a deliberate deviation.")
        for run_id, group in sorted(by_run.items()):
            acts: dict[str, int] = {}
            for u in group:
                acts[str(u.get("rule_action"))] = acts.get(str(u.get("rule_action")), 0) + 1
            out.append(f"- `{run_id}` → {len(group)} action(s): "
                       + " · ".join(f"{n}×{a}" for a, n in sorted(acts.items()))
                       + f" ({', '.join(sorted(str(u.get('sector_id')) for u in group))})")
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
    # Two counters that measure different things, and looked contradictory side by side: the
    # backlog is every deviation waiting for its window; the claude line is the SUSPENSION gate,
    # which needs `min_scored` scored ones before it means anything (plan v5 §0.2 D4).
    pending = res.get("pending") or []
    if pending:
        lines.append(f"- **backlog**: {len(pending)} logged, none scoreable yet "
                     f"(< 21 trading days — a shorter window is a coin, not evidence)")
    lines.append(f"- **suspension gate**: {res['claude']['why']}")
    if not res["scored"] and not pending:
        lines = [res["note"]]

    # A DELIBERATE deviation is the only thing that owes the reader a written reason. The
    # `unrecorded` DEFERs are auto-logged precisely BECAUSE nobody wrote one, and §4c already
    # names them — asking for prose here too would be asking twice for the same silence.
    # `budget` DEFERs (v6 L4) are the same case one step earlier: the trade-budget precedence
    # rule chose them, not a person, the rebalance table's BUDGET line already names what was
    # held back and why, and "the evidence the rule was missing" is by construction nothing.
    # A marker that demands prose for a decision nobody made trains its reader to skip markers.
    _AUTO_AUTHORS = ("unrecorded", "budget")
    chosen = [p for p in pending if str(p.get("author")) not in _AUTO_AUTHORS]
    if chosen:
        lines.append("\n<!-- CLAUDE: for each override logged THIS run, one line: what the rule "
                     "said, what you chose, and the evidence the rule was missing. -->")
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
    body.append(f"_{sc.get('note')} Positive **rule edge** = the rule was right (the forward "
                f"return is signed by the direction it moved money). Evidence for a config edit, "
                f"never an edit — `rebalance_rules.frozen` still needs a commit._")
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
        "\n\n_`price €`+`FX €` = EUR P&L · `peak DD` from the position's own high · `score drift` "
        "= composite today − composite at purchase (negative = the model stopped believing it)._")


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
        "\n\n_Risk shares sum to 100%; a negative share LOWERS book vol — defend it before "
        "trimming it. Measurement only, nothing here recommends._")


def _position_metric_rows(lake_dir: Path | None = None) -> dict:
    from catalyx.store import lake

    df = lake.read_table("position_metrics", lake_dir=lake_dir)
    if df.empty or "run_id" not in df.columns:
        return {}
    df = df[df["run_id"] == sorted(df["run_id"].unique())[-1]]
    return {str(r["sector_id"]): r for r in df.to_dict("records")}


def _cap_check_note() -> str:
    """The cap applied to the table above §6, not only to the book below it.

    §6 checked what is held; §3 proposes what to buy; joining them was left to the reader, so a
    table could route new money into a bucket that had no headroom left and still read as
    compliant. Silent when nothing breaches.
    """
    from catalyx.store import movement_repo

    rows = _safe(lambda: _rebalance_rows(), [])
    proposed = [{"sector_id": r.get("sector_id"), "trade_eur": r.get("trade_eur")}
                for r in rows if r.get("rule_action") in ("BUY", "ADD")]
    checked = _safe(lambda: movement_repo.cap_check(proposed), [])
    breaches = [c for c in checked if c["over"]]
    if not breaches:
        return ""
    out = ["\n\n**⚠ The proposed table breaches the cap** — exposure if every BUY/ADD above is "
           "executed as printed.\n",
           "| catalyst | held € | proposed € | after € | after % | over by € | from |",
           "|---|---|---|---|---|---|---|"]
    for c in breaches:
        out.append(f"| {c['catalyst_id']} | {c['current_eur']:,.0f} | {c['proposed_eur']:,.0f} | "
                   f"{c['post_eur']:,.0f} | **{c['post_pct']:.1f}** | {c['over_by_eur']:,.0f} | "
                   f"{', '.join(c['sectors'])} |")
    out.append("\n_A BUY lands on the structural drivers its sector study names (no position "
               "exists yet to attribute); an ADD lands on the held position's own attribution, "
               "declined drivers included. Sizing down, dropping a name, or an explicit "
               "`correlation_note` — the cap is `warn`, so it does not decide, but it cannot be "
               "passed silently either._")
    return "\n".join(out)


def _drift_note() -> str:
    """Where a position's RECORDED attribution no longer matches its study's structural drivers.

    The attribution is the dated record of WHY a line was opened and is never rewritten — it is
    what the validation loop scores. But the cap is a check on what the book is exposed to TODAY,
    and it cannot see an overlap filed under a catalyst nobody attributed. Naming the gap is the
    fix; closing it is a human decision, taken in `/catalyx-open` or left standing on purpose.
    """
    from catalyx.store import movement_repo

    rows = _safe(lambda: movement_repo.attribution_drift(), [])
    if not rows:
        return ""
    out = ["\n\n**Attribution drift** — held positions whose recorded attribution omits a "
           "structural driver their study now names. The cap above cannot count an overlap that "
           "nobody attributed.\n",
           "| sector | € | recorded | unattributed today |", "|---|---|---|---|"]
    for r in rows:
        rec = ", ".join(r["recorded"]) or "—"
        out.append(f"| {r['sector_id']} | {r['amount_eur']:,.0f} | "
                   + (f"**{rec}**" if r["uncatalyzed"] else rec)
                   + f" | {', '.join(r['unattributed'])} |")
    out.append("\n_The opening attribution is never rewritten. Close a row by appending a dated "
               "`reattribution[]` entry to the movement (schema 1.3) — claiming the driver, or "
               "listing it in `not_attributed[]` with the reason it does not apply._")
    return "\n".join(out)


def section_exposure() -> str:
    """Combined exposure per catalyst against the correlated-catalyst cap.

    Reads `exposure_eur` — the FULL position behind each driver — not the weight-split
    `invested_eur`, which answers a different question (P&L credit). A cap fed the split number
    shrinks a bucket every time a position honestly declares a second driver; see
    `movement_repo.catalyst_ledger`. Rows therefore sum to MORE than the book: one euro exposed to
    two drivers is at risk in both, and that is the point of the check.

    Read LIVE from the movement files, not from the `catalyst_performance` snapshot: that
    partition is written at ingest and freezes the merge map of the day it ran, which is the
    defect this section exists to survive.
    """
    from catalyx.config import weights
    from catalyx.store import movement_repo

    rows = _safe(lambda: movement_repo.catalyst_ledger(), [])
    cap = weights.correlated_catalyst_cap()
    total = weights.total_capital_eur() or 0.0
    for r in rows:
        exp = float(r.get("exposure_eur") or 0.0)
        r["pct_of_capital"] = round(exp / total * 100.0, 2) if total else None
        r["headroom_eur"] = round(cap["max_combined_pct"] / 100.0 * total - exp, 2) \
            if total else None
        over = (r["pct_of_capital"] or 0) > cap["max_combined_pct"]
        r["pct_str"] = (f"{r['pct_of_capital']:.1f} ⚠ OVER CAP" if over
                        else f"{r['pct_of_capital']:.1f}") if r["pct_of_capital"] is not None else "—"
        # A merged catalyst keeps its absorbed id in the movement's frozen attribution. Naming
        # what was collapsed keeps the row tied to the files that produced it.
        r["catalyst_str"] = (f"{r['catalyst_id']} (+{', '.join(r['absorbed_ids'])})"
                             if r.get("absorbed_ids") else r["catalyst_id"])
        r["sectors"] = ", ".join(r.get("sectors") or []) or "—"
    out = _table(rows, [("catalyst_str", "catalyst"), ("exposure_eur", "exposure €"),
                        ("pct_str", "% of capital"), ("headroom_eur", "headroom €"),
                        ("invested_eur", "P&L credit €"), ("sectors", "sectors")],
                 {"exposure_eur": _num(0), "invested_eur": _num(0), "headroom_eur": _num(0)})
    out += (f"\n\n_Cap: **{cap['max_combined_pct']:.0f}%** combined per shared primary "
            f"catalyst (`correlated_catalyst_cap`, enforcement `{cap['enforcement']}`). "
            f"**exposure €** is the whole position behind each driver — rows sum to more than the "
            f"book on purpose, since a position with two drivers is at risk in both; **P&L credit "
            f"€** is the same money split by attribution weight and is NOT what the cap reads. "
            f"Headroom is what a NEW position in that catalyst may still take. A merged catalyst "
            f"is ONE row: its absorbed ids are named in parentheses._")
    forward = _cap_check_note()
    out += forward
    out += _drift_note()
    # The marker is emitted only when a cap is actually breached — held OR proposed. A lint that
    # demands prose where the honest answer is "nothing breached" trains people to write filler.
    if forward or any((r.get("pct_of_capital") or 0) > cap["max_combined_pct"] for r in rows):
        out += (f"\n\n<!-- CLAUDE: name each catalyst over the {cap['max_combined_pct']:.0f}% cap "
                f"and by how much, held and after the proposed table. Enforcement is "
                f"`{cap['enforcement']}` — a breach under `warn` requires an explicit "
                f"correlation_note, not a silent pass. -->")
    return out


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
    """One row per CATALYST, not per indicator. A 41-row indicator dump reads as noise and costs
    tokens every review; what the review acts on is 'which catalyst is running blind, and how
    blind'. The full per-indicator list is one `freshness` CLI call away."""
    from catalyx.scorer import freshness

    rows = _safe(lambda: freshness.overdue(), [])
    if not rows:
        return "_(no overdue indicators)_"
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(str(r.get("catalyst_id")), []).append(r)
    out = []
    for cid, group in by_cat.items():
        def _over(r):
            d, lim = r.get("days_since"), r.get("threshold_days")
            return int(d) - int(lim) if d is not None and lim is not None else 0
        worst = max(group, key=_over)
        out.append({
            "catalyst_id": cid, "n": len(group),
            "worst": f"{worst.get('indicator_id')} — {_over(worst)}d over its "
                     f"{worst.get('cadence')} cadence",
            "last_date": worst.get("last_date"),
            "mislabels": sum(1 for r in group if r.get("cadence_mislabeled")) or None,
        })
    out.sort(key=lambda r: -int(r["worst"].split("— ")[1].split("d")[0]))
    total = sum(r["n"] for r in out)
    return _table(out, [("catalyst_id", "catalyst"), ("n", "overdue"),
                        ("worst", "worst indicator"), ("last_date", "last observed"),
                        ("mislabels", "⚠mislabel")]) + (
        f"\n\n_{total} overdue indicator(s) across {len(out)} catalyst(s) — full list: "
        f"`uv run python -m catalyx.scorer.freshness`. A catalyst scored off overdue indicators "
        f"is scored off the past._")


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

## 5b. Rule scorecard — what the table's own actions earned

{section_scorecard()}

## 6. Catalyst exposure

{section_exposure()}

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


# ── F2 — the report does not get to claim it is finished (plan v5 §3 F2) ────
#
# `lint_prose` polices the prose that IS there. Nothing policed the prose that is NOT: the
# 2026-08-31 review shipped with all five judgement markers intact and passed `--check` clean.
# The deterministic half has had a generator, a lake and 490 tests behind it since v3; the
# judgement half — the executive summary, the macro read, the evidence line under each SELL, the
# opening candidates, the taxonomy decisions — had no check at all. An empty skeleton is worse
# than a visibly unfinished report: §4 orders two SELLs and the place where the evidence
# supporting them (or the override disputing them) would go is blank.

def lint_completeness(text: str) -> list[dict]:
    """Judgement markers with no prose behind them. [] when every marker has been answered.

    A marker is answered by ≥1 non-blank line between its closing `-->` and the next heading.
    The marker itself STAYS in the file — it is the anchor that lets the report be regenerated
    without losing what was written, so removing it is not how a section gets marked done.
    """
    lines = text.splitlines()
    findings, section, i = [], "", 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#"):
            section = lines[i].lstrip("# ").strip()
        elif line.startswith("<!-- CLAUDE:"):
            start = i
            while i < len(lines) and "-->" not in lines[i]:
                i += 1
            # Everything up to the next heading belongs to this marker's section.
            body, j = [], i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("#"):
                if lines[j].strip():
                    body.append(lines[j].strip())
                j += 1
            if not body:
                findings.append({"line": start + 1, "section": section or "(untitled)"})
            i = j - 1
        i += 1
    return findings


def check(path: Path) -> int:
    """Exit code for `--check`: 0 clean, 1 hedged or unfinished. Prints what is wrong.

    Two orthogonal lints, and either can fail alone: a report can be fully written and hedged, or
    unhedged because nothing was written. A freshly generated report fails completeness by
    construction — which is why `post_run.sh` does not run this and `/catalyx-review` does, at the
    point where the document is declared finished.
    """
    if not path.exists():
        print(f"✗ {path} does not exist — generate it first")
        return 1
    text = path.read_text(encoding="utf-8")
    hedges, empty = lint_prose(text), lint_completeness(text)
    if not hedges and not empty:
        print(f"✓ {path} — every judgement section answered, no hedged verdicts")
        return 0
    if empty:
        print(f"✗ {path} — INCOMPLETE: {len(empty)} judgement section(s) with no prose behind the "
              f"marker.\n  The deterministic half is done. The report is not — and a table of "
              f"verdicts with no evidence under it is the half that cannot be checked.\n")
        for f in empty:
            print(f"  L{f['line']:<5} §{f['section']}")
        if hedges:
            print()
    if hedges:
        print(f"✗ {path} — {len(hedges)} hedged line(s) in sections where a decision is stated.\n"
              f"  A verdict that does not move money is HOLD, said once. \"Revisit next cycle\" is "
              f"a DEFER: log it as an override with an author and a reason, do not write it as an "
              f"adverb.\n")
        for f in hedges:
            print(f"  L{f['line']:<5} [{f['hedge']}] §{f['section']}\n         {f['text'][:150]}")
    return 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--top", type=int, default=15, help="sectors in the ranking table")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing the file")
    ap.add_argument("--check", action="store_true",
                    help="lint the COMMITTED report — hedged verdicts (v4 C5) AND unanswered "
                         "judgement markers (v5 F2) — and exit")
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
