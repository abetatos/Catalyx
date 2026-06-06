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

def catalyst_ledger(movements_dir: Path | None = None) -> list[dict]:
    """Per-catalyst exposure + realized P&L, splitting each movement by attribution weight so
    no catalyst is double-counted. Unrealized P&L needs a price mark — left to the dashboard /
    nav layer; here we report invested exposure and realized P&L (the closed part of the record)."""
    led: dict[str, dict] = {}
    for m in load_all(movements_dir):
        eur = float(m.get("amount_eur") or 0.0)
        is_buy = m["action"] in _BUY_ACTIONS
        for a in m.get("attribution", []):
            cid = a["catalyst_id"]
            w = float(a.get("weight") or 0.0)
            e = led.setdefault(cid, {"catalyst_id": cid, "invested_eur": 0.0, "realized_eur": 0.0,
                                     "n_movements": 0, "sectors": set()})
            e["n_movements"] += 1
            e["sectors"].add(m.get("sector_id"))
            if is_buy:
                e["invested_eur"] += w * eur
            # realized P&L attribution on closes/trims is computed at close time (Fase 2
            # return_decomposer); the opening record alone carries no realized P&L.
    out = []
    for e in led.values():
        out.append({
            "catalyst_id": e["catalyst_id"],
            "invested_eur": round(e["invested_eur"], 2),
            "realized_eur": round(e["realized_eur"], 2),
            "n_movements": e["n_movements"],
            "sectors": sorted(s for s in e["sectors"] if s),
        })
    return sorted(out, key=lambda x: -x["invested_eur"])


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
        ldf["sectors"] = ldf["sectors"].apply(lambda s: ",".join(s))
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
