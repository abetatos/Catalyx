"""Backfill indicator value_history from real market data (yfinance) + harvested notes.

Why: the continuous intensity scorer (intensity_engine, v1.5) uses an empirical
percentile of each indicator's own value_history once >= min_history_points accrue,
falling back to a saturating threshold curve before that. Market-priced indicators
(copper spot, ETF AUM-change proxies) have decades of real history available — pulling
it activates the percentile path immediately and de-compresses their scores. Report-based
indicators (capex, COFER, lead times, ...) are not on any feed; for those we seed the
handful of values explicitly stated in the YAML notes (honest, no fabricated points).

SPEC maps (catalyst file, indicator id) → a source:
  - {"source": "yfinance", "ticker": ..., "transform": "usd_per_tonne" | "monthly_pct_change",
     "period": "5y"}  → fetches a monthly series and writes it as value_history
  - {"source": "notes", "points": [(date, value), ...]}  → writes the listed observations

value_history is written most-recent-first (matching intensity.history convention).
current_value is NOT touched — the engine includes it in the percentile sample at scoring time.

Usage:
    uv run python -m catalyx.data.backfill_history            # backfill all mapped indicators
    uv run python -m catalyx.data.backfill_history --dry-run  # print what would be written

After running, recompute scores:
    uv run python -m catalyx.scorer.intensity_engine --all --write-back --period <YYYY-Qn>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ruamel.yaml import YAML

_CATALYSTS_DIR = Path(__file__).parents[1] / "config" / "structural_catalysts"

# COMEX copper (HG=F) is quoted in USD/lb; LME indicators are USD/tonne.
_LB_PER_TONNE = 2204.62

# ── Backfill specification ───────────────────────────────────────────────────
# Only market-priced indicators reach min_history_points from a feed. Report
# indicators get the values explicitly written in their YAML notes (no invented points).
SPEC: dict[str, dict[str, dict]] = {
    "cb_gold_accumulation.yaml": {
        # Gold ETF AUM monthly change → proxied by GLD monthly price return.
        "ind_03": {"source": "yfinance", "ticker": "GLD", "transform": "monthly_pct_change", "period": "5y"},
        # WGC CB net purchases (tonnes/quarter) — values cited in notes.
        "ind_01": {"source": "notes", "points": [("2022-09-30", 399), ("2025-12-31", 330), ("2026-03-31", 290)]},
    },
    "copper_datacenter_demand.yaml": {
        "ind_02": {"source": "yfinance", "ticker": "HG=F", "transform": "usd_per_tonne", "period": "5y"},
        # Analyst DC copper demand estimate (tonnes/yr) — Goldman 500k → WoodMac 1.0M.
        "ind_04": {"source": "notes", "points": [("2025-12-31", 500000), ("2026-03-01", 1000000)]},
    },
    "energy_transition_grid.yaml": {
        "ind_03": {"source": "yfinance", "ticker": "HG=F", "transform": "usd_per_tonne", "period": "5y"},
        # Transformer lead time (months) — note ">18 months" prior.
        "ind_01": {"source": "notes", "points": [("2025-12-31", 18), ("2026-03-01", 22)]},
    },
    "nato_rearmament.yaml": {
        # EU defense ETF AUM flows → proxied by DFNS.L monthly price return.
        "ind_03": {"source": "yfinance", "ticker": "DFNS.L", "transform": "monthly_pct_change", "period": "5y"},
    },
}


def _fetch_series(ticker: str, transform: str, period: str) -> list[tuple[str, float]]:
    """Return [(date_iso, value), ...] oldest-first from a monthly yfinance series."""
    import yfinance as yf

    close = yf.Ticker(ticker).history(period=period, interval="1mo")["Close"].dropna()
    if transform == "usd_per_tonne":
        series = close * _LB_PER_TONNE
        return [(ts.date().isoformat(), round(float(v), 0)) for ts, v in series.items()]
    if transform == "monthly_pct_change":
        series = close.pct_change().dropna()
        return [(ts.date().isoformat(), round(float(v), 4)) for ts, v in series.items()]
    raise ValueError(f"unknown transform: {transform}")


def _build_value_history(spec: dict) -> list[dict]:
    """Resolve a spec into a value_history list, most-recent-first."""
    if spec["source"] == "yfinance":
        points = _fetch_series(spec["ticker"], spec["transform"], spec.get("period", "5y"))
    elif spec["source"] == "notes":
        points = [(d, v) for d, v in spec["points"]]
    else:
        raise ValueError(f"unknown source: {spec['source']}")
    points.sort(key=lambda p: p[0], reverse=True)  # most-recent-first
    return [{"date": d, "value": v} for d, v in points]


def backfill(dry_run: bool = False) -> None:
    """Fetch the mapped indicators' history and write it to the parquet lake (Tier 2 truth).

    No longer touches the YAML files — value_history is externalized to
    data/lake/indicators/. The intensity engine reads the lake first.
    """
    from catalyx.store import indicator_history

    ry = YAML()
    ry.preserve_quotes = True

    for filename, ind_specs in SPEC.items():
        path = _CATALYSTS_DIR / filename
        with path.open("r", encoding="utf-8") as fh:
            cat = ry.load(fh)
        catalyst_id = cat.get("id")

        histories: dict[str, list[dict]] = {}
        for ind_id, spec in ind_specs.items():
            vh = _build_value_history(spec)
            src = spec.get("ticker", "notes")
            histories[ind_id] = [{**p, "source": src} for p in vh]
            print(f"  {filename}:{ind_id} ← {src}  ({len(vh)} points, "
                  f"{vh[-1]['value']}..{vh[0]['value']})")

        if not dry_run and histories:
            n = indicator_history.write_catalyst(catalyst_id, histories)
            print(f"    → lake: {catalyst_id} ({n} rows)")

    if dry_run:
        print("\n(dry run — no files written)")
    else:
        print("\nDone. Recompute: uv run python -m catalyx.scorer.intensity_engine --all --write-back --period <YYYY-Qn>")


def migrate_yaml_to_lake(dry_run: bool = False) -> int:
    """One-off: copy the inline `value_history` embedded in every structural-catalyst YAML
    into the parquet lake (no network). Run once when externalizing; the YAML field then
    becomes a deprecated fallback. Returns total rows written."""
    from catalyx.store import indicator_history

    ry = YAML()
    total = 0
    for path in sorted(_CATALYSTS_DIR.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as fh:
            cat = ry.load(fh)
        catalyst_id = cat.get("id")
        histories: dict[str, list[dict]] = {}
        for ind in cat.get("indicators", []):
            vh = ind.get("value_history")
            if vh:
                histories[ind["id"]] = [
                    {"date": e.get("date"), "value": e.get("value"), "source": "yaml_migrated"}
                    for e in vh if e.get("value") is not None
                ]
        points = sum(len(v) for v in histories.values())
        print(f"  {path.name}: {catalyst_id} → {points} points across {len(histories)} indicators")
        if histories and not dry_run:
            total += indicator_history.write_catalyst(catalyst_id, histories)
    if dry_run:
        print("\n(dry run — nothing written)")
    else:
        print(f"\nMigrated {total} observations into data/lake/indicators/.")
    return total


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Backfill indicator value_history into the parquet lake")
    parser.add_argument("--dry-run", action="store_true", help="Print planned writes without modifying files")
    parser.add_argument("--migrate-yaml", action="store_true",
                        help="One-off: copy inline value_history from the YAMLs into the lake (no network)")
    args = parser.parse_args()
    if args.migrate_yaml:
        print("CATALYX — Migrate inline value_history → lake\n")
        migrate_yaml_to_lake(dry_run=args.dry_run)
        return
    print("CATALYX — Indicator history backfill (→ lake)\n")
    backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
