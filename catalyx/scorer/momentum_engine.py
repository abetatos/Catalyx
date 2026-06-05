"""Cross-sectional momentum scorer for SectorSnapshot.

Formula source: scoring_weights.yaml §MOMENTUM SCORE NORMALIZATION v1.3

Step 1 — weighted raw return per sector (PRIMARY ETF only):
    raw_momentum = return_1m × 0.20 + return_3m × 0.45 + return_6m × 0.35

Step 2 — cross-sectional percentile rank:
    momentum_score = percentile_rank(sector, all_sectors) × 100
    Bottom sector → 0, top sector → 100.

Fallback (< 5 sectors with data):
    momentum_score = (raw - min) / (max - min) × 100  (min-max normalization)

Scores are labelled data_source: "momentum_engine_v1" to distinguish from
the Phase 0 proxy stored in the raw momentum snapshot.

Usage (callable from skills):
    uv run python -m catalyx.scorer.momentum_engine
    uv run python -m catalyx.scorer.momentum_engine --snapshot data/snapshots/momentum_snapshot_2026-06-04.json
    uv run python -m catalyx.scorer.momentum_engine --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from catalyx.config import weights

_REPO_ROOT = Path(__file__).parents[2]
_SNAPSHOTS_DIR = _REPO_ROOT / "data" / "snapshots"

# Period weights — single source of truth: scoring_weights.yaml §momentum_period_weights
_MPW = weights.momentum_period_weights()
_WEIGHT_1M = _MPW["return_1m"]
_WEIGHT_3M = _MPW["return_3m"]
_WEIGHT_6M = _MPW["return_6m"]

_MIN_SECTORS_FOR_PERCENTILE = 5


# ── Helpers ────────────────────────────────────────────────────────────────────

def _latest_snapshot() -> Path | None:
    snapshots = sorted(_SNAPSHOTS_DIR.glob("momentum_snapshot_*.json"), reverse=True)
    return snapshots[0] if snapshots else None


def _primaries_from_lake(snapshot_date: str | None = None) -> tuple[str, dict] | None:
    """Read primary-ETF returns per sector from the parquet lake (Tier 2 truth).

    Returns (snapshot_date, {sector_id: {return_*_pct}}) for the latest date partition
    (or `snapshot_date` if given), or None if the lake has no momentum data yet.
    """
    try:
        import pandas as pd
        from catalyx.store import lake
    except Exception:
        return None
    df = lake.read_table("momentum")
    if df.empty or "role" not in df.columns:
        return None
    df = df[df["role"] == "primary"]
    if df.empty:
        return None
    if snapshot_date is None:
        snapshot_date = max(df["date"])
    df = df[df["date"] == snapshot_date]
    if df.empty:
        return None

    def _clean(v):
        return None if v is None or (isinstance(v, float) and pd.isna(v)) else v

    primaries = {
        row["sector_id"]: {
            "return_1m_pct": _clean(row.get("return_1m_pct")),
            "return_3m_pct": _clean(row.get("return_3m_pct")),
            "return_6m_pct": _clean(row.get("return_6m_pct")),
        }
        for _, row in df.iterrows()
    }
    return snapshot_date, primaries


def _primaries_from_json(path: Path) -> tuple[str, dict]:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot_date = snapshot.get("date", path.stem.replace("momentum_snapshot_", ""))
    primaries = {
        sid: data["primary"]
        for sid, data in snapshot.get("sectors", {}).items()
        if data.get("primary")
    }
    return snapshot_date, primaries


def _raw_momentum(returns: dict) -> float | None:
    """Weighted return from a returns dict with return_1m_pct / return_3m_pct / return_6m_pct."""
    r1 = returns.get("return_1m_pct")
    r3 = returns.get("return_3m_pct")
    r6 = returns.get("return_6m_pct")
    if r1 is None and r3 is None and r6 is None:
        return None
    # Use 0.0 for missing periods, adjust weights proportionally
    available: list[tuple[float, float]] = []
    if r1 is not None:
        available.append((r1, _WEIGHT_1M))
    if r3 is not None:
        available.append((r3, _WEIGHT_3M))
    if r6 is not None:
        available.append((r6, _WEIGHT_6M))
    total_w = sum(w for _, w in available)
    return sum(r * w for r, w in available) / total_w  # re-normalise to available periods


def _percentile_rank(value: float, all_values: list[float]) -> float:
    """Percentile rank of value in all_values. Range [0, 100]."""
    n = len(all_values)
    if n == 1:
        return 50.0
    rank = sum(1 for v in all_values if v < value) + 0.5 * sum(1 for v in all_values if v == value)
    return (rank / n) * 100.0


def _minmax_norm(value: float, mn: float, mx: float) -> float:
    if mx == mn:
        return 50.0
    return (value - mn) / (mx - mn) * 100.0


# ── Core ──────────────────────────────────────────────────────────────────────

def compute_momentum_scores(snapshot_path: Path | None = None,
                            prefer_lake: bool = True) -> dict:
    """Compute cross-sectional momentum scores from a yfinance momentum snapshot.

    Source resolution:
      • explicit `snapshot_path`  → that JSON file (back-compat / pinned input)
      • else `prefer_lake`        → the parquet lake's latest `momentum` partition
      • else / lake empty         → latest momentum_snapshot_*.json (compat fallback)

    Returns:
        {
          "snapshot_date": "YYYY-MM-DD",
          "source": "lake:momentum" | "json:<file>",
          "normalization": "percentile" | "minmax",
          "sector_count": N,
          "scores": {sector_id: {"momentum_score": X, "raw_momentum": Y, "data_source": "momentum_engine_v1"}},
          "raw_returns": {sector_id: {"return_1m": ..., "return_3m": ..., "return_6m": ...}},
        }
    """
    if snapshot_path is not None:
        snapshot_date, primaries = _primaries_from_json(snapshot_path)
        source = f"json:{snapshot_path.name}"
    else:
        lake_res = _primaries_from_lake() if prefer_lake else None
        if lake_res is not None:
            snapshot_date, primaries = lake_res
            source = "lake:momentum"
        else:
            path = _latest_snapshot()
            if path is None:
                return {"error": "No momentum data found (lake empty, no snapshot JSON). "
                                 "Run: uv run python -m catalyx.data.market_data"}
            snapshot_date, primaries = _primaries_from_json(path)
            source = f"json:{path.name}"

    raw_momentums: dict[str, float] = {}
    raw_returns: dict[str, dict] = {}

    for sector_id, primary in primaries.items():
        raw = _raw_momentum(primary)
        if raw is None:
            continue
        raw_momentums[sector_id] = raw
        raw_returns[sector_id] = {
            "return_1m": primary.get("return_1m_pct"),
            "return_3m": primary.get("return_3m_pct"),
            "return_6m": primary.get("return_6m_pct"),
            "raw_momentum": round(raw, 4),
        }

    if not raw_momentums:
        return {"error": "No sectors with return data", "source": source}

    all_raw = list(raw_momentums.values())
    use_percentile = len(all_raw) >= _MIN_SECTORS_FOR_PERCENTILE
    normalization = "percentile" if use_percentile else "minmax"

    mn = min(all_raw)
    mx = max(all_raw)

    scores: dict[str, dict] = {}
    for sector_id, raw in raw_momentums.items():
        if use_percentile:
            score = _percentile_rank(raw, all_raw)
        else:
            score = _minmax_norm(raw, mn, mx)
        scores[sector_id] = {
            "momentum_score": round(score, 1),
            "raw_momentum": round(raw, 4),
            "data_source": "momentum_engine_v1",
        }

    return {
        "snapshot_date": snapshot_date,
        "source": source,
        "normalization": normalization,
        "sector_count": len(scores),
        "scores": scores,
        "raw_returns": raw_returns,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CATALYX momentum engine — cross-sectional momentum scores from yfinance snapshot"
    )
    parser.add_argument(
        "--snapshot", type=Path, default=None,
        help="Path to momentum snapshot JSON. Default: latest in data/snapshots/."
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON only.")
    args = parser.parse_args()

    result = compute_momentum_scores(snapshot_path=args.snapshot)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"CATALYX — Momentum Engine  [{result['snapshot_date']}]  "
          f"source={result.get('source','?')}  normalization={result['normalization']}\n")
    print(f"  {'sector_id':<45} {'momentum_score':>14}  {'raw_momentum':>12}")
    print(f"  {'-'*45} {'-'*14}  {'-'*12}")

    # Sort by momentum_score descending
    sorted_sectors = sorted(result["scores"].items(), key=lambda x: x[1]["momentum_score"], reverse=True)
    for sector_id, s in sorted_sectors:
        rr = result["raw_returns"].get(sector_id, {})
        print(
            f"  {sector_id:<45} {s['momentum_score']:>14.1f}  "
            f"(raw={s['raw_momentum']:+.2f}%  "
            f"1m={rr.get('return_1m', 'n/a')}  "
            f"3m={rr.get('return_3m', 'n/a')}  "
            f"6m={rr.get('return_6m', 'n/a')})"
        )

    print("\n--- JSON output ---")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
