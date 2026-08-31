"""COT positioning — measured crowding for the commodity sleeve (v7 N2, K4 direction).

CFTC Commitments of Traders (legacy futures-only, Socrata API, public, no key). Net
speculative positioning (non-commercial long − short) as a share of open interest, scored as
the percentile of the latest reading within its own ~5y weekly history → `cot_crowding`
[0-100]. High = specs historically stretched long = crowded.

CANDIDATE COLUMN (weight 0): covers only sectors with a real futures market underneath —
that is the metals/energy sleeve where this book concentrates. Everything else stays None.

CLI:
    uv run python -m catalyx.data.cot_data          # fetch + write snapshot
    uv run python -m catalyx.data.cot_data --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).parents[2]
_SNAPSHOTS_DIR = _REPO_ROOT / "data" / "snapshots"

_API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_HISTORY_WEEKS = 260          # ~5y — the window the percentile is measured against
_TIMEOUT_S = 30

# CFTC contract market code → the sectors that ride that underlying.
COT_MARKETS: dict[str, dict] = {
    "088691": {"label": "gold_comex", "sectors": ["gold_physical", "gold_miners"]},
    "084691": {"label": "silver_comex", "sectors": ["silver_physical"]},
    "085692": {"label": "copper_comex", "sectors": ["copper_miners"]},
    "067651": {"label": "wti_nymex", "sectors": ["oil_majors_integrated"]},
}


def _percentile(value: float, history: list[float]) -> float:
    n = len(history)
    if n == 0:
        return 50.0
    below = sum(1 for v in history if v < value)
    equal = sum(1 for v in history if v == value)
    return round((below + 0.5 * equal) / n * 100.0, 1)


def fetch_market(code: str, client=None) -> dict | None:
    """Weekly net-spec/OI series for one contract, newest first. None on any failure."""
    params = {
        "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,"
                   "noncomm_positions_short_all,open_interest_all",
        "$where": f"cftc_contract_market_code='{code}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(_HISTORY_WEEKS),
    }
    try:
        c = client or httpx
        resp = c.get(_API, params=params, timeout=_TIMEOUT_S)
        rows = resp.json()
    except Exception:  # noqa: BLE001
        return None
    series = []
    for r in rows:
        try:
            oi = float(r["open_interest_all"])
            if oi <= 0:
                continue
            net = float(r["noncomm_positions_long_all"]) - float(r["noncomm_positions_short_all"])
            series.append({"date": str(r["report_date_as_yyyy_mm_dd"])[:10],
                           "net_spec_ratio": round(net / oi, 4)})
        except (KeyError, ValueError, TypeError):
            continue
    return {"code": code, "series": series} if series else None


def compute(client=None) -> dict:
    """Fetch every mapped market and score the latest reading vs its own history."""
    today = date.today().isoformat()
    markets: dict[str, dict] = {}
    sector_scores: dict[str, dict] = {}
    for code, spec in COT_MARKETS.items():
        m = fetch_market(code, client=client)
        if not m or len(m["series"]) < 52:
            markets[spec["label"]] = {"code": code, "error": "fetch failed or <52w history"}
            continue
        latest = m["series"][0]
        history = [x["net_spec_ratio"] for x in m["series"][1:]]
        pct = _percentile(latest["net_spec_ratio"], history)
        markets[spec["label"]] = {
            "code": code, "report_date": latest["date"],
            "net_spec_ratio": latest["net_spec_ratio"],
            "cot_crowding": pct, "n_history": len(history),
        }
        for sid in spec["sectors"]:
            sector_scores[sid] = {"cot_crowding": pct, "market": spec["label"],
                                  "report_date": latest["date"],
                                  "net_spec_ratio": latest["net_spec_ratio"]}
    return {"date": today, "markets": markets, "sector_scores": sector_scores}


def write_snapshot(snap: dict) -> Path:
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    p = _SNAPSHOTS_DIR / f"cot_snapshot_{snap['date'].replace('-', '')}.json"
    p.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load_latest(max_age_days: int = 14) -> dict | None:
    """Latest COT snapshot if fresh enough, else None (stale positioning is not positioning)."""
    candidates = sorted(_SNAPSHOTS_DIR.glob("cot_snapshot_*.json"), reverse=True)
    if not candidates:
        return None
    try:
        snap = json.loads(candidates[0].read_text(encoding="utf-8"))
        age = (date.today() - date.fromisoformat(snap["date"])).days
        return snap if age <= max_age_days else None
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="CATALYX COT crowding — CFTC net-spec percentile")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    snap = compute()
    path = write_snapshot(snap)
    if args.json:
        print(json.dumps(snap, indent=2, ensure_ascii=False))
        return
    print(f"CATALYX — COT crowding  [{snap['date']}] → {path.name}\n")
    for label, m in snap["markets"].items():
        if m.get("error"):
            print(f"  {label:<16} ERROR: {m['error']}")
        else:
            print(f"  {label:<16} net_spec/OI={m['net_spec_ratio']:+.3f}  "
                  f"crowding={m['cot_crowding']:>5.1f}pct  ({m['report_date']}, n={m['n_history']})")


if __name__ == "__main__":
    main()
