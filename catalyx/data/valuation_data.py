"""Fund-level valuation series — best-effort accumulation (v7 O3).

No valuation exists anywhere in CATALYX (dislocation is price-vs-price). Retail access to
fund valuation is poor: yfinance `.info` exposes trailingPE / priceToBook for SOME funds,
mostly US-listed. So this records whatever is available, per run date, into the lake table
`valuation` — a series being accumulated so a future value-anchor test is possible, with a
coverage flag instead of a pretense. Read by nothing today; that is the point (K1's lesson:
calibration blocks on data nobody collected in time).

CLI:
    uv run python -m catalyx.data.valuation_data [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from catalyx.data.flow_data import SECTOR_FLOW_TICKERS

_FIELDS = ("trailingPE", "priceToBook", "yield", "trailingAnnualDividendYield")


def fetch_fund_valuation(ticker: str) -> dict | None:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception:  # noqa: BLE001
        return None
    out = {f: info.get(f) for f in _FIELDS}
    out = {k: (round(float(v), 4) if isinstance(v, (int, float)) else None)
           for k, v in out.items()}
    return out if any(v is not None for v in out.values()) else None


def compute() -> dict:
    """One row per sector: first ticker in its chain with any valuation field."""
    today = date.today().isoformat()
    rows = []
    for sid, chain in SECTOR_FLOW_TICKERS.items():
        row = {"date": today, "sector_id": sid, "ticker": None,
               **{f: None for f in _FIELDS}}
        for t in chain:
            v = fetch_fund_valuation(t)
            if v:
                row.update({"ticker": t, **v})
                break
        rows.append(row)
    covered = sum(1 for r in rows if r["ticker"])
    return {"date": today, "rows": rows, "covered": covered, "total": len(rows)}


def write_lake(snap: dict) -> int:
    import pandas as pd
    from catalyx.store import lake

    df = pd.DataFrame(snap["rows"])
    lake.append_partition("valuation", df, {"date": snap["date"]}, overwrite=True)
    return len(df)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="CATALYX fund valuation series (best-effort)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    snap = compute()
    n = write_lake(snap)
    if args.json:
        print(json.dumps(snap, indent=2, ensure_ascii=False))
        return
    print(f"CATALYX — valuation series  [{snap['date']}]  coverage {snap['covered']}/{snap['total']} "
          f"→ lake:valuation ({n} rows)\n")
    for r in sorted(snap["rows"], key=lambda x: (x["trailingPE"] is None, x["sector_id"])):
        pe = f"{r['trailingPE']:.1f}" if r["trailingPE"] else "  — "
        pb = f"{r['priceToBook']:.2f}" if r["priceToBook"] else "  — "
        print(f"  {r['sector_id']:<40} {str(r['ticker'] or '—'):<10} PE={pe:<8} P/B={pb}")


if __name__ == "__main__":
    main()
