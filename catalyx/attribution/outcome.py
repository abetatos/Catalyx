"""Closed-experiment outcome engine — the SELL-side mirror of the buy stack's score_context.

Every movement is an experiment: the OPENING movement carries the hypothesis (attribution +
score_context + risk_discipline.assumptions/invalidation); the CLOSING movement carries the
RESULT. This module turns a closed movement into a registered experiment with:

  • P&L          — realized gross + after-tax (Spanish CGT via tax_engine), return_pct, holding_days.
  • VERDICT      — the right-thesis × right-reason matrix. right_thesis = the bet made money after
                   tax; right_reason = the assumptions held / the catalyst materialized. The label
                   separates skill (repeat the pattern) from luck (don't) from variance (sound
                   reason, adverse outcome) from correct_invalidation (the discipline worked).
  • BEHAVIOR     — deviation flags from the files alone (no network): held_past_full_exit,
                   exited_intact (closed flat/down with the thesis still holding + no stop fired —
                   the 'sold too early / panicked' shape), discretionary_exit, overrode_signal.
                   Flags PROMPT a self-reflection note (outcome.exit_note); they are not verdicts.

DOCTRINE: Python computes the facts (P&L, the matrix, the deviations). The human-judged inputs —
exit_note (in-the-moment), assumption_resolution, catalyst_materialized, followed_signal — are
captured at /catalyx-close and live on the movement file (Tier-1, editable; add later realizations
to additional_notes, never overwrite exit_note). This rebuilds the deleted ClosedThesis /
right_reason_score on the Movement model. No look-ahead: it reads only the close + the prior
opening movements, never a future run.

CLI:
    uv run python -m catalyx.attribution.outcome evaluate <mov_id> [--write-back] [--no-persist] [--json]
    uv run python -m catalyx.attribution.outcome summary
    uv run python -m catalyx.attribution.outcome report          # aggregate self-learning view
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from catalyx.execution import tax_engine
from catalyx.store import lake, movement_repo

_BUY_ACTIONS = ("open", "add")
_SELL_ACTIONS = ("trim", "close")
_LOW_CONFIDENCE_DAYS = 60  # < this ⇒ verdict confidence 'low' (matches the attribution-confidence rule)


# ── P&L (network-free; same average-cost convention as movement_repo.positions) ──

def _parse_dt(s: str):
    import pandas as pd
    return pd.to_datetime(s, utc=True, errors="coerce")


def realized_pnl(close_mov: dict, opening_movs: list[dict]) -> dict:
    """Gross realized P&L of this close vs the average cost of the buys that built the position.
    Mirrors movement_repo.positions: realized = proceeds(amount_eur) − avg_cost × qty_sold; fees are
    already embedded in amount_eur (kept separately only for the Fase-5 cost decomposition)."""
    buy_qty = sum(float(m.get("qty") or 0.0) for m in opening_movs)
    buy_eur = sum(float(m.get("amount_eur") or 0.0) for m in opening_movs)
    avg_cost = (buy_eur / buy_qty) if buy_qty else 0.0

    qty_sold = float(close_mov.get("qty") or 0.0)
    proceeds = float(close_mov.get("amount_eur") or 0.0)
    cost_basis_sold = avg_cost * qty_sold
    gross = round(proceeds - cost_basis_sold, 2)
    return_pct = round(gross / cost_basis_sold * 100.0, 2) if cost_basis_sold else None

    # holding period: earliest opening buy → this close
    open_dts = [_parse_dt(m["executed_at"]) for m in opening_movs if m.get("executed_at")]
    holding_days = None
    close_dt = _parse_dt(close_mov.get("executed_at"))
    if open_dts and close_dt is not None:
        first = min(d for d in open_dts if d is not None)
        holding_days = int((close_dt - first).days)

    return {"gross_pnl_eur": gross, "return_pct": return_pct, "holding_days": holding_days,
            "cost_basis_sold_eur": round(cost_basis_sold, 2), "proceeds_eur": round(proceeds, 2),
            "avg_cost": round(avg_cost, 6) if avg_cost else None, "qty_sold": qty_sold}


def _ytd_prior_realized(close_mov: dict, all_movements: list[dict]) -> float:
    """Realized gains from OTHER sell movements earlier in the same calendar year — the YTD baseline
    tax_engine needs to place this gain in the right progressive bracket. Reconstructs each prior
    close's realized P&L from the buys of its own vehicle before that close (no look-ahead)."""
    close_dt = _parse_dt(close_mov.get("executed_at"))
    if close_dt is None:
        return 0.0
    year = close_dt.year
    prior = 0.0
    for m in all_movements:
        if m.get("id") == close_mov.get("id") or m.get("action") not in _SELL_ACTIONS:
            continue
        dt = _parse_dt(m.get("executed_at"))
        if dt is None or dt.year != year or dt > close_dt or dt == close_dt:
            continue
        etf = m["vehicle"]["etf"]
        prior_buys = [b for b in all_movements
                      if b["vehicle"]["etf"] == etf and b.get("action") in _BUY_ACTIONS
                      and (_parse_dt(b.get("executed_at")) is not None)
                      and _parse_dt(b.get("executed_at")) <= dt]
        prior += realized_pnl(m, prior_buys)["gross_pnl_eur"]
    return round(prior, 2)


def after_tax(gross_pnl_eur: float, ytd_prior: float) -> dict:
    """Incremental Spanish CGT on a positive gain; a loss is tax-free (harvestable offset)."""
    if gross_pnl_eur is None:
        return {"tax_due_eur": None, "after_tax_pnl_eur": None}
    if gross_pnl_eur <= 0:
        return {"tax_due_eur": 0.0, "after_tax_pnl_eur": round(gross_pnl_eur, 2)}
    t = tax_engine.compute_tax(gross_gain=gross_pnl_eur, ytd_prior=max(0.0, ytd_prior))
    return {"tax_due_eur": t.tax_due, "after_tax_pnl_eur": round(gross_pnl_eur - t.tax_due, 2)}


# ── Right-reason inputs: resolve assumptions at close ─────────────────────────

def resolve_assumptions(captured: list[dict] | None, opening_assumptions: list[dict]) -> list[dict]:
    """Use the close-captured assumption_resolution if present; otherwise fall back to the opening
    assumptions' last current_status (violated→falsified, holding→validated, else unresolved) so a
    verdict can still be computed before the skill captures explicit resolutions."""
    if captured:
        return captured
    fallback = []
    _map = {"violated": "falsified", "holding": "validated"}
    for a in opening_assumptions or []:
        fallback.append({"id": a.get("id"),
                         "outcome": _map.get(a.get("current_status"), "unresolved"),
                         "note": a.get("status_note")})
    return fallback


# ── Verdict matrix (pure) ─────────────────────────────────────────────────────

def compute_verdict(after_tax_pnl_eur: float | None, assumption_resolution: list[dict],
                    catalyst_materialized: bool | None, holding_days: int | None) -> dict:
    """right_thesis × right_reason → {skill, luck, variance, correct_invalidation, indeterminate}."""
    right_thesis = None if after_tax_pnl_eur is None else (after_tax_pnl_eur > 0)

    counts = Counter(r.get("outcome") for r in (assumption_resolution or []))
    validated, falsified = counts.get("validated", 0), counts.get("falsified", 0)
    unresolved = counts.get("unresolved", 0)
    total = validated + falsified + unresolved

    # right_reason: the catalyst question dominates when answered; else the assumption balance.
    if catalyst_materialized is True:
        right_reason = True
    elif catalyst_materialized is False:
        right_reason = False
    elif validated > falsified and validated >= 1:
        right_reason = True
    elif falsified > validated:
        right_reason = False
    else:
        right_reason = None  # nothing resolved either way

    label_map = {
        (True, True): "skill",                 # made money AND for the stated reason → repeat
        (True, False): "luck",                 # made money but the reason failed → don't over-learn
        (False, True): "variance",             # reason sound, outcome adverse → maybe repeat, check horizon
        (False, False): "correct_invalidation",  # lost AND reason failed → the discipline worked
    }
    label = label_map.get((right_thesis, right_reason), "indeterminate")

    # confidence: low when the reason is mostly unresolved or the hold was too short to tell.
    low = False
    if right_reason is None:
        low = True
    if total and unresolved / total >= 0.5:
        low = True
    if holding_days is not None and holding_days < _LOW_CONFIDENCE_DAYS:
        low = True
    confidence = "low" if (label != "indeterminate" and low) else ("low" if label == "indeterminate" else "high")

    note = {
        "skill": "Made money AND the stated reason held — the pattern is worth repeating.",
        "luck": "Made money but the reason FAILED — outcome ≠ process. Don't read this as a validated edge.",
        "variance": "The reason held but the outcome was adverse — sound process, bad draw or horizon too short.",
        "correct_invalidation": "Lost AND the reason failed — the falsification worked; exiting was correct.",
        "indeterminate": "Not enough resolved to judge the reason — revisit when the assumptions settle.",
    }[label]
    return {"right_thesis": right_thesis, "right_reason": right_reason, "label": label,
            "confidence": confidence, "note": note,
            "_assumptions": {"validated": validated, "falsified": falsified, "unresolved": unresolved}}


# ── Behavioral deviation flags (pure, files-only) ─────────────────────────────

def behavioral_flags(close_mov: dict, opening_movs: list[dict], gross_pnl_eur: float | None,
                     assumption_resolution: list[dict], followed_signal: bool | None) -> list[str]:
    """Deviations derivable from the movement files alone. Each flag is a PROMPT to reflect, not a
    judgement — the 'why' is yours to annotate in exit_note/additional_notes."""
    flags: list[str] = []
    close_dt = _parse_dt(close_mov.get("executed_at"))

    # gather the position's invalidation stops
    invs = []
    for m in opening_movs:
        invs.extend((m.get("risk_discipline") or {}).get("invalidation") or [])
    fired = [iv for iv in invs if iv.get("triggered")]
    fired_full_exit = [iv for iv in fired if iv.get("severity") == "full_exit"]
    any_falsified = any(r.get("outcome") == "falsified" for r in (assumption_resolution or []))

    # 1) held past your own fired full_exit stop
    for iv in fired_full_exit:
        t_at = _parse_dt(iv.get("triggered_at"))
        if t_at is not None and close_dt is not None and close_dt > t_at:
            days = int((close_dt - t_at).days)
            flags.append(f"held_past_full_exit:{iv.get('id')}:+{days}d")

    # 2) exited with the thesis still intact at a loss/flat — the 'sold too early / panic' shape
    if gross_pnl_eur is not None and gross_pnl_eur <= 0 and not fired and not any_falsified:
        flags.append("exited_intact_at_loss")

    # 3) discretionary exit — no stop fired, no assumption falsified, exit was a re-think
    if close_mov.get("trigger") in ("reconsideration", "profit_take") and not fired and not any_falsified:
        flags.append("discretionary_exit")

    # 4) you overrode your own deterministic exit signal
    if followed_signal is False:
        flags.append("overrode_signal")

    return flags


# ── Orchestrator ──────────────────────────────────────────────────────────────

def _opening_movs_for(close_mov: dict, all_movements: list[dict]) -> list[dict]:
    """The buys (open/add) of the same vehicle executed at or before this close — the position the
    close is exiting, and the carrier of the risk_discipline + cost basis."""
    etf = close_mov["vehicle"]["etf"]
    close_dt = _parse_dt(close_mov.get("executed_at"))
    out = []
    for m in all_movements:
        if m["vehicle"]["etf"] != etf or m.get("action") not in _BUY_ACTIONS:
            continue
        dt = _parse_dt(m.get("executed_at"))
        if close_dt is not None and dt is not None and dt > close_dt:
            continue
        out.append(m)
    return out


def evaluate(mov_id: str, write_back: bool = False, persist: bool = True,
             movements_dir: Path | None = None, lake_dir: Path | None = None) -> dict:
    """Evaluate one closed/trimmed movement into a full experiment outcome. Merges the human-captured
    outcome fields with the computed pnl/behavioral_flags/verdict; optionally writes the merged block
    back to the file (--write-back) and a row to the lake (validation/movement_outcome)."""
    all_movements = movement_repo.load_all(movements_dir)
    close_mov = next((m for m in all_movements if m.get("id") == mov_id), None)
    if close_mov is None:
        raise KeyError(f"movement {mov_id!r} not found")
    if close_mov.get("action") not in _SELL_ACTIONS:
        raise ValueError(f"{mov_id} is a {close_mov.get('action')} — outcomes are for close/trim only")

    opening = _opening_movs_for(close_mov, all_movements)
    captured = dict(close_mov.get("outcome") or {})

    pnl = realized_pnl(close_mov, opening)
    ytd_prior = _ytd_prior_realized(close_mov, all_movements)
    pnl.update(after_tax(pnl["gross_pnl_eur"], ytd_prior))

    opening_assumptions = []
    for m in opening:
        opening_assumptions.extend((m.get("risk_discipline") or {}).get("assumptions") or [])
    asm_res = resolve_assumptions(captured.get("assumption_resolution"), opening_assumptions)

    verdict = compute_verdict(pnl["after_tax_pnl_eur"], asm_res,
                              captured.get("catalyst_materialized"), pnl["holding_days"])
    sig = captured.get("signal_context") or {}
    flags = behavioral_flags(close_mov, opening, pnl["gross_pnl_eur"], asm_res, sig.get("followed_signal"))

    # merged outcome: keep captured human fields, overwrite the computed ones
    merged = dict(captured)
    merged["assumption_resolution"] = asm_res
    merged["pnl"] = {k: pnl[k] for k in ("gross_pnl_eur", "tax_due_eur", "after_tax_pnl_eur",
                                         "return_pct", "holding_days")}
    merged["behavioral_flags"] = flags
    merged["verdict"] = {k: verdict[k] for k in ("right_thesis", "right_reason", "label",
                                                 "confidence", "note")}

    if write_back:
        close_mov["outcome"] = merged
        movement_repo._path_for(mov_id, movements_dir).write_text(
            json.dumps(close_mov, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if persist:
        _persist_lake(close_mov, opening, pnl, verdict, flags, ytd_prior, lake_dir)

    return {"mov_id": mov_id, "sector_id": close_mov.get("sector_id"),
            "etf": close_mov["vehicle"]["etf"], "action": close_mov.get("action"),
            "attribution": [a.get("catalyst_id") for a in close_mov.get("attribution", [])],
            "pnl": merged["pnl"], "ytd_prior_realized_eur": ytd_prior,
            "assumption_resolution": asm_res, "behavioral_flags": flags, "verdict": merged["verdict"],
            "captured": {k: captured.get(k) for k in ("exit_reason", "exit_note", "exit_trigger_type",
                         "catalyst_materialized", "signal_context", "additional_notes")},
            "write_back": write_back, "persisted": persist}


def _persist_lake(close_mov: dict, opening: list[dict], pnl: dict, verdict: dict,
                  flags: list[str], ytd_prior: float, lake_dir) -> None:
    import pandas as pd
    captured = close_mov.get("outcome") or {}
    sig = captured.get("signal_context") or {}
    row = {
        "mov_id": close_mov["id"], "evaluated_at": datetime.now(timezone.utc),
        "executed_at": close_mov.get("executed_at"), "action": close_mov.get("action"),
        "sector_id": close_mov.get("sector_id"), "etf": close_mov["vehicle"]["etf"],
        "trigger": close_mov.get("trigger"), "conviction": close_mov.get("conviction"),
        "attribution_ids": ",".join(a.get("catalyst_id") for a in close_mov.get("attribution", [])),
        "gross_pnl_eur": pnl["gross_pnl_eur"], "tax_due_eur": pnl["tax_due_eur"],
        "after_tax_pnl_eur": pnl["after_tax_pnl_eur"], "return_pct": pnl["return_pct"],
        "holding_days": pnl["holding_days"], "ytd_prior_realized_eur": ytd_prior,
        "right_thesis": verdict["right_thesis"], "right_reason": verdict["right_reason"],
        "verdict_label": verdict["label"], "verdict_confidence": verdict["confidence"],
        "asm_validated": verdict["_assumptions"]["validated"],
        "asm_falsified": verdict["_assumptions"]["falsified"],
        "asm_unresolved": verdict["_assumptions"]["unresolved"],
        "catalyst_materialized": captured.get("catalyst_materialized"),
        "exit_trigger_type": captured.get("exit_trigger_type"),
        "followed_signal": sig.get("followed_signal"),
        "behavioral_flags": ",".join(flags), "n_behavioral_flags": len(flags),
        "exit_reason": captured.get("exit_reason"),
    }
    lake.append_partition("movement_outcome", pd.DataFrame([row]), {"mov_id": close_mov["id"]},
                          overwrite=True, lake_dir=lake_dir)


# ── Aggregate self-learning report ────────────────────────────────────────────

def report(movements_dir: Path | None = None) -> dict:
    """Aggregate every closed experiment into a self-learning view: verdict mix, behavioral-flag
    frequency, signal-discipline rate, after-tax win rate, and the exit-note journal."""
    all_movements = movement_repo.load_all(movements_dir)
    closes = [m for m in all_movements if m.get("action") in _SELL_ACTIONS]
    rows = []
    for m in closes:
        try:
            rows.append(evaluate(m["id"], persist=False, movements_dir=movements_dir))
        except Exception as e:  # pragma: no cover — defensive on hand-authored files
            print(f"[outcome] skip {m.get('id')}: {e}", file=sys.stderr)

    n = len(rows)
    labels = Counter(r["verdict"]["label"] for r in rows)
    flag_freq = Counter(f.split(":")[0] for r in rows for f in r["behavioral_flags"])
    triggers = Counter((r["captured"].get("exit_trigger_type") or "untagged") for r in rows)
    after_tax_vals = [r["pnl"]["after_tax_pnl_eur"] for r in rows if r["pnl"]["after_tax_pnl_eur"] is not None]
    wins = sum(1 for v in after_tax_vals if v > 0)
    holds = [r["pnl"]["holding_days"] for r in rows if r["pnl"]["holding_days"] is not None]
    followed = [r["captured"].get("signal_context", {}).get("followed_signal") for r in rows
                if (r["captured"].get("signal_context") or {}).get("followed_signal") is not None]

    return {
        "n_closed": n,
        "verdict_mix": dict(labels),
        "behavioral_flag_frequency": dict(flag_freq),
        "exit_trigger_mix": dict(triggers),
        "after_tax": {
            "total_eur": round(sum(after_tax_vals), 2),
            "win_rate_pct": round(wins / len(after_tax_vals) * 100.0, 1) if after_tax_vals else None,
            "n": len(after_tax_vals),
        },
        "avg_holding_days": round(sum(holds) / len(holds), 1) if holds else None,
        "signal_discipline": {
            "followed": sum(1 for f in followed if f),
            "overrode": sum(1 for f in followed if f is False),
            "rate_pct": round(sum(1 for f in followed if f) / len(followed) * 100.0, 1) if followed else None,
        },
        "journal": [
            {"mov_id": r["mov_id"], "label": r["verdict"]["label"],
             "after_tax_eur": r["pnl"]["after_tax_pnl_eur"],
             "exit_note": r["captured"].get("exit_note"),
             "flags": r["behavioral_flags"]}
            for r in rows
        ],
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def _print_eval(r: dict) -> None:
    v, p = r["verdict"], r["pnl"]
    icon = {"skill": "🟢 SKILL", "luck": "🟡 LUCK", "variance": "🔵 VARIANCE",
            "correct_invalidation": "🟠 CORRECT-INVALIDATION", "indeterminate": "⚪ INDETERMINATE"}
    print(f"\n  {r['mov_id']}   {r['sector_id']} ({r['etf']})   {r['action']}")
    print(f"  catalysts: {', '.join(r['attribution'])}")
    ret = f"{p['return_pct']:+.1f}%" if p["return_pct"] is not None else "—"
    print(f"  P&L: gross €{p['gross_pnl_eur']:,.2f}  tax €{p['tax_due_eur']:,.2f}  "
          f"after-tax €{p['after_tax_pnl_eur']:,.2f}  ({ret}, held {p['holding_days']}d)")
    print(f"  VERDICT: {icon.get(v['label'], v['label'])}  [{v['confidence']} confidence]")
    print(f"           right_thesis={v['right_thesis']}  right_reason={v['right_reason']}")
    print(f"           {v['note']}")
    ar = r["assumption_resolution"]
    if ar:
        mix = Counter(a.get("outcome") for a in ar)
        print(f"  assumptions: {mix.get('validated',0)}✓validated {mix.get('falsified',0)}✗falsified "
              f"{mix.get('unresolved',0)}?unresolved")
    if r["behavioral_flags"]:
        print(f"  ⚠ behavioral flags: {', '.join(r['behavioral_flags'])}")
        print(f"    → reflect: add a note via exit_note / additional_notes on why.")
    note = r["captured"].get("exit_note")
    if note:
        print(f"  exit_note: \"{note}\"")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX closed-experiment outcome engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    ev = sub.add_parser("evaluate", help="evaluate one closed/trimmed movement")
    ev.add_argument("mov_id")
    ev.add_argument("--write-back", action="store_true", help="persist the merged outcome block to the file")
    ev.add_argument("--no-persist", action="store_true", help="do not write the lake row")
    ev.add_argument("--json", action="store_true")

    sub.add_parser("summary", help="one line per closed experiment")
    rp = sub.add_parser("report", help="aggregate self-learning view")
    rp.add_argument("--json", action="store_true")

    args = p.parse_args()

    if args.cmd == "evaluate":
        r = evaluate(args.mov_id, write_back=args.write_back, persist=not args.no_persist)
        if args.json:
            print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
        else:
            _print_eval(r)
        return

    if args.cmd == "summary":
        movs = [m for m in movement_repo.load_all() if m.get("action") in _SELL_ACTIONS]
        if not movs:
            print("No closed/trimmed movements yet — outcomes are written at /catalyx-close.")
            return
        for m in movs:
            r = evaluate(m["id"], persist=False)
            v, pnl = r["verdict"], r["pnl"]
            at = f"€{pnl['after_tax_pnl_eur']:,.0f}" if pnl["after_tax_pnl_eur"] is not None else "—"
            print(f"  {m['id']:<48} {v['label']:<22} after-tax {at:<10} "
                  f"flags={len(r['behavioral_flags'])}")
        return

    if args.cmd == "report":
        r = report()
        if args.json:
            print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
            return
        if not r["n_closed"]:
            print("No closed experiments yet. Close a position via /catalyx-close to start the ledger.")
            return
        print(f"\nCATALYX — Experiment ledger   ({r['n_closed']} closed)\n")
        print(f"  Verdict mix:        {r['verdict_mix']}")
        print(f"  After-tax:          €{r['after_tax']['total_eur']:,.2f} total, "
              f"win rate {r['after_tax']['win_rate_pct']}% (n={r['after_tax']['n']})")
        print(f"  Avg holding:        {r['avg_holding_days']}d")
        sd = r["signal_discipline"]
        print(f"  Signal discipline:  followed {sd['followed']} / overrode {sd['overrode']} "
              f"({sd['rate_pct']}% followed)")
        print(f"  Behavioral flags:   {r['behavioral_flag_frequency'] or '—'}")
        print(f"  Exit triggers:      {r['exit_trigger_mix']}")
        print("\n  Journal:")
        for j in r["journal"]:
            print(f"    {j['mov_id']:<46} {j['label']:<22} €{(j['after_tax_eur'] or 0):,.0f}")
            if j["exit_note"]:
                print(f"        \"{j['exit_note']}\"")
            if j["flags"]:
                print(f"        ⚠ {', '.join(j['flags'])}")
        return


if __name__ == "__main__":
    main()
