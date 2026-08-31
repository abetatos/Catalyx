"""Google Trends attention — the measured counterpart of narrative maturity (v7 N3, K4).

Retail search attention per theme, scored as the percentile of the recent level (mean of the
last 4 complete weeks) within its own 5y weekly history → `trends_crowding` [0-100]. High =
retail attention historically stretched = crowded. One payload per term ON PURPOSE: batching
terms puts them on a shared scale and crushes the smaller series' granularity.

CANDIDATE COLUMN (weight 0). The source is unofficial and rate-limited; failures degrade to
partial coverage, and the snapshot freshness guard (35d) means a stale month reads None, never
a stale number. If the source proves unusable in practice, PLAN_v7 N3 closes as rejected.

CLI:
    uv run python -m catalyx.data.trends_data [--sectors a,b] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_SNAPSHOTS_DIR = _REPO_ROOT / "data" / "snapshots"

_TIMEFRAME = "today 5-y"
_RECENT_WEEKS = 4
_SLEEP_S = 3.0
_MAX_RETRIES = 2

# sector_id → the retail-attention query for its theme. Editable table; a term change resets
# comparability of that sector's snapshots (note it in CHANGELOG).
TREND_TERMS: dict[str, str] = {
    "gold_physical": "buy gold",
    "gold_miners": "gold mining stocks",
    "silver_physical": "buy silver",
    "copper_miners": "copper stocks",
    "lithium_miners": "lithium stocks",
    "uranium_miners": "uranium stocks",
    "nuclear_energy": "nuclear energy stocks",
    "solar_energy": "solar stocks",
    "oil_majors_integrated": "oil stocks",
    "grid_infrastructure_utilities": "utility stocks",
    "semiconductors_design": "semiconductor stocks",
    "ai_infrastructure_data_centers": "AI stocks",
    "robotics_automation": "robotics stocks",
    "cybersecurity_commercial": "cybersecurity stocks",
    "cloud_software_saas": "cloud stocks",
    "pharma_large_cap": "pharma stocks",
    "biotech_drug_development": "biotech stocks",
    "eu_defense_prime_contractors": "defense stocks",
    "space_defense_satellite": "space stocks",
    "crypto_infrastructure": "crypto stocks",
    "eu_retail_banking": "bank stocks",
    "agriculture_soft_commodities": "agriculture stocks",
    "water_infrastructure": "water stocks",
    "infrastructure_core": "infrastructure stocks",
    "luxury_goods": "luxury stocks",
    "consumer_india_em": "india stocks",
}


def _percentile(value: float, history: list[float]) -> float:
    n = len(history)
    if n == 0:
        return 50.0
    below = sum(1 for v in history if v < value)
    equal = sum(1 for v in history if v == value)
    return round((below + 0.5 * equal) / n * 100.0, 1)


def score_series(values: list[float], recent_weeks: int = _RECENT_WEEKS) -> float | None:
    """Percentile of the recent mean within the full history. None below 1y of data."""
    if len(values) < 52 or recent_weeks < 1:
        return None
    recent = sum(values[-recent_weeks:]) / recent_weeks
    return _percentile(recent, values)


def fetch_term(term: str, pytrends=None) -> list[float] | None:
    """Weekly interest series (complete weeks only) for one term. None on failure."""
    if pytrends is None:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
    for attempt in range(_MAX_RETRIES + 1):
        try:
            pytrends.build_payload([term], timeframe=_TIMEFRAME)
            df = pytrends.interest_over_time()
            if df.empty or term not in df.columns:
                return None
            if "isPartial" in df.columns:
                df = df[~df["isPartial"].astype(bool)]
            return [float(v) for v in df[term].tolist()]
        except Exception:  # noqa: BLE001
            if attempt < _MAX_RETRIES:
                time.sleep(_SLEEP_S * (attempt + 2))
    return None


def compute(sector_ids: list[str] | None = None, pytrends=None,
            sleep_s: float = _SLEEP_S) -> dict:
    terms = {sid: t for sid, t in TREND_TERMS.items()
             if sector_ids is None or sid in sector_ids}
    sector_scores: dict[str, dict] = {}
    errors: list[str] = []
    for i, (sid, term) in enumerate(terms.items()):
        if i and sleep_s:
            time.sleep(sleep_s)
        values = fetch_term(term, pytrends=pytrends)
        if values is None:
            errors.append(sid)
            continue
        pct = score_series(values)
        if pct is None:
            errors.append(sid)
            continue
        sector_scores[sid] = {"trends_crowding": pct, "term": term, "n_weeks": len(values)}
    return {"date": date.today().isoformat(), "sector_scores": sector_scores,
            "failed": errors}


def write_snapshot(snap: dict) -> Path:
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    p = _SNAPSHOTS_DIR / f"trends_snapshot_{snap['date'].replace('-', '')}.json"
    p.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load_latest(max_age_days: int = 35) -> dict | None:
    candidates = sorted(_SNAPSHOTS_DIR.glob("trends_snapshot_*.json"), reverse=True)
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
    p = argparse.ArgumentParser(description="CATALYX trends crowding — retail attention percentile")
    p.add_argument("--sectors", default=None, help="Comma-separated subset (default: all)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    subset = args.sectors.split(",") if args.sectors else None
    snap = compute(sector_ids=subset)
    path = write_snapshot(snap)
    if args.json:
        print(json.dumps(snap, indent=2, ensure_ascii=False))
        return
    print(f"CATALYX — trends crowding  [{snap['date']}] → {path.name}\n")
    for sid, s in sorted(snap["sector_scores"].items(), key=lambda kv: -kv[1]["trends_crowding"]):
        print(f"  {sid:<40} {s['trends_crowding']:>5.1f}pct  (\"{s['term']}\", {s['n_weeks']}w)")
    if snap["failed"]:
        print(f"\n  ⚠ failed: {', '.join(snap['failed'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
