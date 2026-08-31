"""Movement reader + derived positions / catalyst ledger (Fase 1 — Thesis→Movement).

A **Movement** is the primary capital unit (replaces Thesis). It is a Tier-1 JSON document in
`data/movements/*.json` — the source of truth, hand-droppable. Writing a file IS the registration:
drop the file, run the pipeline whenever you like. Schema: `schemas/movement.json`.

This module reads those files and derives, deterministically and network-free:
  * `positions()`   — net book per ETF (same shape as the old trade_logger.real_holdings, so it
                      feeds `nav_engine` unchanged). open/add → buy leg, trim/close → sell leg.
  * `catalyst_ledger()` — P&L and exposure attributed to each catalyst by `attribution[].weight`
                      (no double-counting). The answer to "which catalysts have won".
  * `ingest()`      — (a) point-in-time `score_context`: join each movement to the score_run that
                      was current AS OF `executed_at` (never a future run → no look-ahead);
                      (b) write-through a queryable `movement` mirror + a time-versioned
                      `catalyst_performance` snapshot to the lake (for the dashboard).

Cost basis is average-cost today (matches the legacy real_holdings). FIFO lots + the Spanish
two-month wash rule are a Fase-5 refinement (see docs/PLAN_movement_restructure.md §Fase 5).

CLI:
    uv run python -m catalyx.store.movement_repo summary
    uv run python -m catalyx.store.movement_repo get <mov_id>
    uv run python -m catalyx.store.movement_repo positions
    uv run python -m catalyx.store.movement_repo ledger
    uv run python -m catalyx.store.movement_repo ingest [--write-back]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from catalyx.store import lake

_ROOT = Path(__file__).resolve().parents[2]
_MOVEMENTS = _ROOT / "data" / "movements"
_BUY_ACTIONS = ("open", "add")
_SELL_ACTIONS = ("trim", "close")

_MOVEMENT_TABLE = "movement"
_PERF_TABLE = "catalyst_performance"
_UNCATALYZED_ID = "uncatalyzed"


# ── read ─────────────────────────────────────────────────────────────────────

def load_all(movements_dir: Path | None = None) -> list[dict]:
    d = movements_dir or _MOVEMENTS
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("mov_*.json")):
        out.append(json.loads(f.read_text(encoding="utf-8")))
    out.sort(key=lambda m: (m.get("executed_at", ""), m.get("id", "")))
    return out


def get(mov_id: str, movements_dir: Path | None = None) -> dict | None:
    for m in load_all(movements_dir):
        if m.get("id") == mov_id:
            return m
    return None


def _path_for(mov_id: str, movements_dir: Path | None = None) -> Path:
    return (movements_dir or _MOVEMENTS) / f"{mov_id}.json"


# ── derived: positions ───────────────────────────────────────────────────────

def positions(movements_dir: Path | None = None) -> dict:
    """Net book per ETF from the movement files. Same shape as the legacy real_holdings so it
    feeds nav_engine: {holdings:[{etf, sector_id, qty, invested_eur, avg_cost, realized_eur,
    weight_pct}], total_invested_eur, realized_eur}."""
    # amount_eur is the full cash that moved (fees already embedded). The `fees` field is kept
    # for the rebalance-simulator cost decomposition, not re-applied to the cost basis here.
    pos: dict[str, dict] = {}
    for m in load_all(movements_dir):
        etf = m["vehicle"]["etf"]
        qty = float(m.get("qty") or 0.0)
        eur = float(m.get("amount_eur") or 0.0)
        p = pos.setdefault(etf, {"etf": etf, "sector_id": m.get("sector_id"),
                                 "qty": 0.0, "invested_eur": 0.0, "realized_eur": 0.0})
        if m["action"] in _BUY_ACTIONS:
            p["qty"] += qty
            p["invested_eur"] += eur
        elif m["action"] in _SELL_ACTIONS:
            # Guard against a sell with no (or insufficient) prior position: a stray trim/close
            # would otherwise book the full proceeds as realized P&L against a zero cost basis
            # and leave a negative qty that abs() later mistakes for an open short. Cap the sold
            # qty at what is held and warn — these files are hand-authored, so this is bad input.
            if qty - p["qty"] > 1e-9:
                print(f"[movement_repo] WARNING {m.get('id')}: {m['action']} of {qty} {etf} "
                      f"exceeds held qty {p['qty']:.6f} — capping to held.", file=sys.stderr)
                qty = p["qty"]
            avg = (p["invested_eur"] / p["qty"]) if p["qty"] else 0.0
            cost = avg * qty
            p["realized_eur"] += eur - cost
            p["qty"] -= qty
            p["invested_eur"] -= cost

    open_pos = [p for p in pos.values() if abs(p["qty"]) > 1e-9]
    total_invested = sum(p["invested_eur"] for p in open_pos) or 1.0
    holdings = []
    for p in sorted(open_pos, key=lambda x: -x["invested_eur"]):
        holdings.append({
            "etf": p["etf"], "sector_id": p["sector_id"], "qty": round(p["qty"], 6),
            "invested_eur": round(p["invested_eur"], 2),
            "avg_cost": round(p["invested_eur"] / p["qty"], 4) if p["qty"] else None,
            "realized_eur": round(p["realized_eur"], 2),
            "weight_pct": round(p["invested_eur"] / total_invested * 100.0, 2),
        })
    return {"holdings": holdings,
            "total_invested_eur": round(sum(p["invested_eur"] for p in open_pos), 2),
            "realized_eur": round(sum(p["realized_eur"] for p in pos.values()), 2)}


# ── derived: catalyst ledger ─────────────────────────────────────────────────

def effective_attribution(m: dict) -> tuple[list[dict], str | None, set[str]]:
    """The attribution to use for PRESENT-TENSE questions, and the date it was decided.

    `attribution[]` answers "why was this opened" — a dated judgement, frozen, the input the
    validation loop scores. `reattribution[]` (schema 1.3) is the append-only log of what the
    position is held for NOW. The last entry wins; the original is never touched. Returns
    (attribution, as_of, deliberately_not_attributed) — the third is what a human reviewed and
    decided NOT to claim, so a drift report can stop re-raising a question already answered.
    """
    entries = m.get("reattribution") or []
    if not entries:
        return list(m.get("attribution", [])), None, set()
    last = max(entries, key=lambda e: str(e.get("as_of") or ""))
    return (list(last.get("attribution") or m.get("attribution", [])),
            str(last.get("as_of") or "") or None,
            {str(c) for c in (last.get("not_attributed") or [])})


def catalyst_ledger(movements_dir: Path | None = None,
                    resolve_merged: bool = True) -> list[dict]:
    """Per-catalyst credit and per-catalyst RISK — two different numbers, and the cap needs the
    second one.

    `invested_eur` splits each movement by attribution weight: the P&L-attribution number, so no
    catalyst is credited twice for one euro of return. `exposure_eur` is the FULL position behind
    every driver it names: the risk number, and the one `correlated_catalyst_cap` must read.

    Using the weight-split number as a risk cap gets the sign of the incentive backwards. The grid
    position (€500, split 0.7 grid / 0.3 AI capex) contributed €150 to the AI-capex row — but if
    AI capex breaks, the whole €500 is at risk. Nobody owns 30% of a utilities ETF. Worse: naming
    a SECOND driver on a position LOWERED its weight on the first, so declaring more exposure
    bought you more headroom. On this book that understated the largest correlated bucket by €350
    (€1,650 reported vs €2,000 real, against a €2,000 cap).

    Unrealized P&L needs a price mark — left to the dashboard / nav layer; here we report
    exposure and realized P&L (the closed part of the record).

    A MERGE MUST COLLAPSE THE BUCKETS. `attribution[].catalyst_id` is frozen at the moment the
    movement was written; a catalyst merged afterwards keeps its absorbed id in that record — as
    it must, since the lake indexes its indicator history by that id. But the LEDGER is a risk
    control, and reporting an absorbed id as its own row is precisely the double count
    `correlated_catalyst_cap` exists to prevent.

    On this book it was not hypothetical. `struct_copper_datacenter_demand` was merged into
    `struct_ai_capex_supercycle`, and §6 reported them as two rows — €1,000 and €650 — of one
    economic driver. Combined they are **€1,650 (16.5%) against a 20% cap**, so the ledger
    published €1,350 of headroom where €350 existed, on the single largest exposure in the book.
    CLAUDE.md already required following `merged_into` before reading a catalyst; the ledger was
    the one reader that did not.

    `absorbed_ids` names which retired ids fed each row, so the collapse is auditable and a
    reader can still tie a number back to the movement file that produced it.
    """
    merged: dict[str, str] = {}
    if resolve_merged:
        try:
            from catalyx.store import structural_catalyst_repo as scr
            merged = scr.merged_map()
        except Exception:                                      # pragma: no cover - defensive
            merged = {}

    led: dict[str, dict] = {}
    for m in load_all(movements_dir):
        eur = float(m.get("amount_eur") or 0.0)
        is_buy = m["action"] in _BUY_ACTIONS
        attribution, reattributed_at, _ = effective_attribution(m)
        for a in attribution:
            raw = a["catalyst_id"]
            cid = merged.get(raw, raw)
            w = float(a.get("weight") or 0.0)
            e = led.setdefault(cid, {"catalyst_id": cid, "invested_eur": 0.0, "exposure_eur": 0.0,
                                     "realized_eur": 0.0, "n_movements": 0, "sectors": set(),
                                     "absorbed": set(), "reattributed": set()})
            e["n_movements"] += 1
            e["sectors"].add(m.get("sector_id"))
            if raw != cid:
                e["absorbed"].add(raw)
            if reattributed_at:
                e["reattributed"].add(m.get("sector_id"))
            if is_buy:
                e["invested_eur"] += w * eur
                e["exposure_eur"] += eur
            # realized P&L attribution on closes/trims is computed at close time (Fase 2
            # return_decomposer); the opening record alone carries no realized P&L.
    out = []
    for e in led.values():
        out.append({
            "catalyst_id": e["catalyst_id"],
            "exposure_eur": round(e["exposure_eur"], 2),
            "invested_eur": round(e["invested_eur"], 2),
            "realized_eur": round(e["realized_eur"], 2),
            "n_movements": e["n_movements"],
            "sectors": sorted(s for s in e["sectors"] if s),
            "absorbed_ids": sorted(e["absorbed"]),
            "reattributed_sectors": sorted(s for s in e["reattributed"] if s),
        })
    return sorted(out, key=lambda x: -x["exposure_eur"])


def cap_check(proposed: list[dict], movements_dir: Path | None = None) -> list[dict]:
    """The book's exposure AFTER the proposed table, against `correlated_catalyst_cap`.

    The cap has always been a check on what a NEW position may take — "headroom is what a new
    position in that catalyst may still take" — and the rebalance table is where new positions are
    proposed. But the two lived in different sections and nobody joined them, so a table could
    print €1,560 of buys into a bucket with €0 of headroom and read as compliant. On this book it
    did: executing as printed puts `struct_ai_capex_supercycle` at 35.6% against a 20% cap.

    Which drivers a proposed trade lands on depends on whether the position exists yet:
      · BUY  — not held, so there is no attribution to read. Today's structural drivers from the
        sector study are the honest estimate of what the money would be exposed to.
      · ADD  — held, so the position's effective attribution governs, INCLUDING a driver the
        review deliberately declined. Re-deriving it from the study would quietly overrule a
        judgement someone already wrote down with a reason.

    Returns one row per affected catalyst. `over` is the flag; enforcement (`warn` vs `block`)
    stays with the caller, exactly as for the standing cap.
    """
    try:
        from catalyx.config import weights
        from catalyx.execution import portfolio
        from catalyx.store import structural_catalyst_repo as scr
        smap, merged = portfolio._sector_catalyst_map(), scr.merged_map()
        structural = {str(d.get("id")) for d in scr._load_all() if d.get("id")}
        cap_pct = float(weights.correlated_catalyst_cap()["max_combined_pct"])
        total = float(weights.total_capital_eur() or 0.0)
    except Exception:                                          # pragma: no cover - defensive
        return []
    if not total:
        return []

    held: dict[str, dict] = {}
    for m in load_all(movements_dir):
        if m["action"] in _BUY_ACTIONS:
            held.setdefault(str(m.get("sector_id") or ""), m)

    current = {r["catalyst_id"]: r["exposure_eur"] for r in catalyst_ledger(movements_dir)}
    added: dict[str, float] = {}
    by_catalyst: dict[str, set] = {}
    for p in proposed or []:
        eur = float(p.get("trade_eur") or 0.0)
        sid = str(p.get("sector_id") or "")
        if eur <= 0 or not sid:
            continue
        mov = held.get(sid)
        if mov is not None:
            attribution, _, _ = effective_attribution(mov)
            drivers = {merged.get(a["catalyst_id"], a["catalyst_id"]) for a in attribution}
        else:
            drivers = {merged.get(c, c) for c in (smap.get(sid) or [])}
        for c in drivers & structural:
            added[c] = added.get(c, 0.0) + eur
            by_catalyst.setdefault(c, set()).add(sid)

    out = []
    for cid, add_eur in added.items():
        post = current.get(cid, 0.0) + add_eur
        pct = post / total * 100.0
        out.append({
            "catalyst_id": cid,
            "current_eur": round(current.get(cid, 0.0), 2),
            "proposed_eur": round(add_eur, 2),
            "post_eur": round(post, 2),
            "post_pct": round(pct, 1),
            "cap_pct": cap_pct,
            "over_by_eur": round(post - cap_pct / 100.0 * total, 2),
            "over": pct > cap_pct,
            "sectors": sorted(by_catalyst.get(cid, ())),
        })
    return sorted(out, key=lambda r: -r["post_pct"])


def attribution_drift(movements_dir: Path | None = None) -> list[dict]:
    """Open positions whose RECORDED attribution no longer matches today's sector→catalyst map.

    Two different questions live in this file and must not be conflated:
      · `attribution[]` — WHY the position was opened. A dated judgement, the input the validation
        loop scores. Rewriting it would destroy the record, so nothing here rewrites it.
      · today's sector study — WHAT the position is exposed to now.

    They drift, and the drift is invisible until it matters. `pharma_large_cap` was opened
    2026-06-16 as a defensive line with `attribution: [uncatalyzed 1.0]`; its study now names
    three active catalysts, two of which (`struct_biopharma_patent_cliff_ma`) it shares with
    `biotech_drug_development` — a €978 BUY sitting on the same table. The cap check cannot see
    that overlap while the €500 is filed under `uncatalyzed`.

    Surfacing it is the fix; the resolution is a human one, and both halves of it are recorded in
    `reattribution[]` (schema 1.3): `attribution[]` for what the position IS now held for, and
    `not_attributed[]` for a driver the review looked at and declined to claim. A declined driver
    stops appearing here — a check that re-raises a question already answered teaches its reader
    to skip the table, which is worse than not running it.

    STRUCTURAL ONLY. `correlated_catalyst_cap` is written "per shared primary STRUCTURAL
    catalyst" — event catalysts decay and are not what makes two positions rise and fall
    together. Reporting every `cat_*` a study happens to list would bury the two cases that
    matter under a dozen that do not.
    """
    try:
        from catalyx.execution import portfolio
        from catalyx.store import structural_catalyst_repo as scr
        smap, merged = portfolio._sector_catalyst_map(), scr.merged_map()
        structural = {str(d.get("id")) for d in scr._load_all() if d.get("id")}
    except Exception:                                          # pragma: no cover - defensive
        return []

    held = {h["sector_id"] for h in positions(movements_dir).get("holdings", [])}
    out = []
    for m in load_all(movements_dir):
        sid = str(m.get("sector_id") or "")
        if sid not in held or m["action"] not in _BUY_ACTIONS:
            continue
        attribution, reattributed_at, declined = effective_attribution(m)
        recorded = {merged.get(a["catalyst_id"], a["catalyst_id"]) for a in attribution}
        declined = {merged.get(c, c) for c in declined}
        current = {merged.get(c, c) for c in (smap.get(sid) or [])} & structural
        missing = sorted(current - recorded - declined)
        if not missing:
            continue
        out.append({
            "sector_id": sid, "movement_id": m.get("id"),
            "amount_eur": float(m.get("amount_eur") or 0.0),
            "recorded": sorted(recorded), "current": sorted(current), "unattributed": missing,
            "uncatalyzed": recorded == {_UNCATALYZED_ID},
            "reattributed_at": reattributed_at,
        })
    return sorted(out, key=lambda r: -r["amount_eur"])



# ── point-in-time score context ──────────────────────────────────────────────

def point_in_time_context(sector_id: str, executed_at: str, lake_dir: Path | None = None) -> dict | None:
    """The score_run that was current AS OF executed_at for this sector (latest snapshot with
    snapshot_at <= executed_at). Returns None if no such run exists — never reaches into a
    future run (no look-ahead)."""
    try:
        ss = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    except Exception:
        return None
    if ss is None or ss.empty or "sector_id" not in ss.columns:
        return None
    import pandas as pd
    rows = ss[ss["sector_id"] == sector_id].copy()
    if rows.empty:
        return None
    cutoff = pd.to_datetime(executed_at, utc=True)
    rows["_at"] = pd.to_datetime(rows["snapshot_at"], utc=True, errors="coerce")
    rows = rows[rows["_at"] <= cutoff]
    if rows.empty:
        return None
    r = rows.sort_values("_at").iloc[-1]
    def val(col):
        return None if col not in rows.columns or pd.isna(r[col]) else r[col]
    return {
        "run_id": val("run_id"),
        "rank": int(r["rank"]) if "rank" in rows.columns and not pd.isna(r["rank"]) else None,
        "composite": val("composite"),
        "catalyst_alignment": val("catalyst_alignment"),
        "momentum": val("momentum"),
        "flow": val("flow_confirmation"),
        "crowding": val("crowding_risk"),
        "regime_state": val("regime_state"),
    }


def _is_empty_context(ctx: dict | None) -> bool:
    if not ctx:
        return True
    return ctx.get("run_id") in (None, "") and ctx.get("rank") is None


# ── ingest: enrich + lake mirror + ledger snapshot ───────────────────────────

def ingest(write_back: bool = False, movements_dir: Path | None = None,
           lake_dir: Path | None = None) -> dict:
    """Backfill point-in-time score_context (only where empty, and only from a run as-of
    executed_at), write a queryable `movement` mirror to the lake, and append a time-versioned
    `catalyst_performance` snapshot. The Tier-1 files stay the source of truth; --write-back also
    persists the enriched score_context back into the files."""
    import pandas as pd

    movements = load_all(movements_dir)
    enriched = 0
    mirror_rows = []
    for m in movements:
        ctx = m.get("score_context")
        if _is_empty_context(ctx):
            pit = point_in_time_context(m["sector_id"], m["executed_at"], lake_dir=lake_dir)
            if pit and pit.get("run_id"):
                m["score_context"] = pit
                enriched += 1
                if write_back:
                    _path_for(m["id"], movements_dir).write_text(
                        json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sc = m.get("score_context") or {}
        mirror_rows.append({
            "id": m["id"], "executed_at": m["executed_at"], "action": m["action"],
            "sector_id": m["sector_id"], "etf": m["vehicle"]["etf"],
            "currency": m["vehicle"].get("currency"),
            "amount_eur": m.get("amount_eur"), "qty": m.get("qty"), "price": m.get("price"),
            "fees": m.get("fees", 0.0), "trigger": m.get("trigger"),
            "conviction": m.get("conviction"),
            "attribution_json": json.dumps(m.get("attribution", []), ensure_ascii=False),
            "score_run_id": sc.get("run_id"), "score_composite": sc.get("composite"),
            "score_catalyst_alignment": sc.get("catalyst_alignment"),
            "score_regime_state": sc.get("regime_state"),
            "run_id": m.get("run_id"),
            "ingested_at": datetime.now(timezone.utc),
        })

    # mirror — one partition per sector_id (overwrite: full rebuild from the files = truth)
    if mirror_rows:
        mdf = pd.DataFrame(mirror_rows)
        for sector_id, group in mdf.groupby("sector_id"):
            lake.append_partition(_MOVEMENT_TABLE, group, {"sector_id": sector_id},
                                  overwrite=True, lake_dir=lake_dir)

    # catalyst ledger snapshot (time-versioned by as_of date)
    as_of = datetime.now(timezone.utc).date().isoformat()
    led = catalyst_ledger(movements_dir)
    if led:
        ldf = pd.DataFrame(led)
        for col in ("sectors", "absorbed_ids", "reattributed_sectors"):
            if col in ldf.columns:
                ldf[col] = ldf[col].apply(lambda s: ",".join(s or []))
        ldf["as_of"] = as_of
        lake.append_partition(_PERF_TABLE, ldf, {"as_of": as_of}, overwrite=True, lake_dir=lake_dir)

    return {"movements": len(movements), "score_context_enriched": enriched,
            "catalysts_in_ledger": len(led), "as_of": as_of, "write_back": write_back}


def summary(movements_dir: Path | None = None) -> dict:
    movements = load_all(movements_dir)
    by_action: dict[str, int] = {}
    for m in movements:
        by_action[m["action"]] = by_action.get(m["action"], 0) + 1
    pos = positions(movements_dir)
    return {"n_movements": len(movements), "by_action": by_action,
            "open_positions": len(pos["holdings"]),
            "total_invested_eur": pos["total_invested_eur"],
            "realized_eur": pos["realized_eur"]}


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX movement reader + derived positions/ledger")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary")
    g = sub.add_parser("get"); g.add_argument("mov_id")
    sub.add_parser("positions")
    sub.add_parser("ledger")
    ing = sub.add_parser("ingest"); ing.add_argument("--write-back", action="store_true")
    args = p.parse_args()

    if args.cmd == "summary":
        print(json.dumps(summary(), indent=2, ensure_ascii=False, default=str))
    elif args.cmd == "get":
        m = get(args.mov_id)
        print(json.dumps(m, indent=2, ensure_ascii=False) if m else f"not found: {args.mov_id}")
    elif args.cmd == "positions":
        print(json.dumps(positions(), indent=2, ensure_ascii=False, default=str))
    elif args.cmd == "ledger":
        print(json.dumps(catalyst_ledger(), indent=2, ensure_ascii=False, default=str))
    elif args.cmd == "ingest":
        print(json.dumps(ingest(write_back=args.write_back), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
