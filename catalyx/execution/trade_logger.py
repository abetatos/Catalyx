"""Real-money trade log + derived real holdings (Fase D.2).

The real-money leg of the model-vs-real comparison. Each executed trade is appended to the
lake table `portfolio_trade` (partitioned by portfolio_id), carrying lineage pointers
(`thesis_id`, `run_id`) so the audit trail trade → thesis → run_id → report+sector_snapshot
is a single join. `real_holdings` reduces the trade log to net positions per ETF (qty +
average cost in EUR), which feed the same `nav_engine` as model holdings.

All money is EUR (CLAUDE.md: P&L in EUR; non-EUR fills converted at execution date). Spanish
CGT on realized gains is computed separately via `tax_engine` at close.

CLI:
    uv run python -m catalyx.execution.trade_logger log real --etf COPX --side buy --qty 10 \\
        --price 92.5 --fees 1.2 --date 2026-06-05 [--thesis-id ... --run-id ...]
    uv run python -m catalyx.execution.trade_logger holdings real
    uv run python -m catalyx.execution.trade_logger trades real
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from catalyx.store import lake

_TRADE_TABLE = "portfolio_trade"
_SIDES = ("buy", "sell")


def log_trade(portfolio_id: str, etf: str, side: str, qty: float, price: float,
              date: str | None = None, fees: float = 0.0, currency: str = "EUR",
              eur_value: float | None = None, thesis_id: str | None = None,
              run_id: str | None = None, note: str | None = None,
              lake_dir: Path | None = None) -> dict:
    """Append one executed trade to the portfolio's log (append-only; rewrites the partition)."""
    import pandas as pd

    if side not in _SIDES:
        raise ValueError(f"side must be one of {_SIDES}, got {side!r}")
    date = date or datetime.now(timezone.utc).date().isoformat()
    if eur_value is None:
        gross = qty * price
        eur_value = round(gross + fees if side == "buy" else gross - fees, 2)

    df = lake.read_table(_TRADE_TABLE, lake_dir=lake_dir)
    existing = df[df["portfolio_id"] == portfolio_id] if (not df.empty and "portfolio_id" in df.columns) else pd.DataFrame()
    seq = len(existing) + 1
    trade = {
        "trade_id": f"{portfolio_id}_{date}_{seq:03d}",
        "portfolio_id": portfolio_id, "date": date, "etf": etf, "side": side,
        "qty": float(qty), "price": float(price), "fees": float(fees),
        "currency": currency, "eur_value": float(eur_value),
        "thesis_id": thesis_id, "run_id": run_id, "note": note,
        "logged_at": datetime.now(timezone.utc),
    }
    combined = pd.concat([existing, pd.DataFrame([trade])], ignore_index=True)
    lake.append_partition(_TRADE_TABLE, combined, {"portfolio_id": portfolio_id},
                          overwrite=True, lake_dir=lake_dir)
    return trade


def trades(portfolio_id: str, lake_dir: Path | None = None) -> list[dict]:
    df = lake.read_table(_TRADE_TABLE, lake_dir=lake_dir)
    if df.empty or "portfolio_id" not in df.columns:
        return []
    df = df[df["portfolio_id"] == portfolio_id].sort_values(["date", "trade_id"])
    return df.to_dict(orient="records")


def real_holdings(portfolio_id: str, lake_dir: Path | None = None) -> dict:
    """Net position per ETF from the trade log: signed qty + average buy cost (EUR).

    Returns {portfolio_id, holdings:[{etf, qty, invested_eur, avg_cost, realized_eur}],
    weight_pct derived from invested cost so it can feed nav_engine}.
    """
    rows = trades(portfolio_id, lake_dir=lake_dir)
    pos: dict[str, dict] = {}
    for t in rows:
        etf = t["etf"]
        p = pos.setdefault(etf, {"etf": etf, "qty": 0.0, "invested_eur": 0.0, "realized_eur": 0.0})
        if t["side"] == "buy":
            p["qty"] += t["qty"]
            p["invested_eur"] += t["eur_value"]
        else:  # sell — realize proportional cost basis
            avg = (p["invested_eur"] / p["qty"]) if p["qty"] else 0.0
            cost = avg * t["qty"]
            p["realized_eur"] += t["eur_value"] - cost
            p["qty"] -= t["qty"]
            p["invested_eur"] -= cost

    open_pos = [p for p in pos.values() if abs(p["qty"]) > 1e-9]
    total_invested = sum(p["invested_eur"] for p in open_pos) or 1.0
    holdings = []
    for p in sorted(open_pos, key=lambda x: -x["invested_eur"]):
        holdings.append({
            "etf": p["etf"], "qty": round(p["qty"], 6),
            "invested_eur": round(p["invested_eur"], 2),
            "avg_cost": round(p["invested_eur"] / p["qty"], 4) if p["qty"] else None,
            "realized_eur": round(p["realized_eur"], 2),
            "weight_pct": round(p["invested_eur"] / total_invested * 100.0, 2),
        })
    return {"portfolio_id": portfolio_id, "holdings": holdings,
            "total_invested_eur": round(sum(p["invested_eur"] for p in open_pos), 2),
            "realized_eur": round(sum(p["realized_eur"] for p in pos.values()), 2)}


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX real-money trade log (Fase D.2)")
    sub = p.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("log", help="Log an executed trade")
    lg.add_argument("portfolio_id")
    lg.add_argument("--etf", required=True)
    lg.add_argument("--side", required=True, choices=_SIDES)
    lg.add_argument("--qty", required=True, type=float)
    lg.add_argument("--price", required=True, type=float)
    lg.add_argument("--fees", type=float, default=0.0)
    lg.add_argument("--date", default=None)
    lg.add_argument("--currency", default="EUR")
    lg.add_argument("--thesis-id", default=None)
    lg.add_argument("--run-id", default=None)
    lg.add_argument("--note", default=None)

    h = sub.add_parser("holdings", help="Show derived net real holdings")
    h.add_argument("portfolio_id")
    tr = sub.add_parser("trades", help="List the trade log")
    tr.add_argument("portfolio_id")
    args = p.parse_args()

    if args.cmd == "log":
        t = log_trade(args.portfolio_id, args.etf, args.side, args.qty, args.price,
                      date=args.date, fees=args.fees, currency=args.currency,
                      thesis_id=args.thesis_id, run_id=args.run_id, note=args.note)
        print(f"  logged {t['trade_id']}: {t['side']} {t['qty']} {t['etf']} @ {t['price']} "
              f"→ €{t['eur_value']}" + (f"  [thesis={t['thesis_id']}]" if t['thesis_id'] else ""))
    elif args.cmd == "holdings":
        r = real_holdings(args.portfolio_id)
        print(f"  {r['portfolio_id']}  invested=€{r['total_invested_eur']}  realized=€{r['realized_eur']}")
        for hld in r["holdings"]:
            print(f"    {hld['etf']:<10} qty={hld['qty']:<10} €{hld['invested_eur']:<12} "
                  f"avg={hld['avg_cost']:<10} wt={hld['weight_pct']}%")
    elif args.cmd == "trades":
        for t in trades(args.portfolio_id):
            print(f"  {t['date']}  {t['trade_id']:<24} {t['side']:<4} {t['qty']:<8} "
                  f"{t['etf']:<10} @ {t['price']:<8} €{t['eur_value']:<10} "
                  f"thesis={t.get('thesis_id')}  run={t.get('run_id')}")


if __name__ == "__main__":
    main()
