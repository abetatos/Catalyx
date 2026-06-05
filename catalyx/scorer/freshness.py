"""Indicator freshness audit — deterministic staleness check by NATIVE cadence.

The monthly-review Step 1.5 gate needs to know which structural-catalyst indicators are
overdue for a refresh BEFORE the run scores `catalyst_alignment` on them. This module is the
deterministic home for that audit (previously an ad-hoc inline script that over-flagged).

Why a cadence-aware threshold matters (bug fixed 2026-06-05):
    An indicator sourced from an ANNUAL report (Gartner forecast, IBM X-Force Index, BloombergNEF
    LCOE, NATO annual report) legitimately prints one new value per year. Auditing it against a
    `quarterly` 95-day threshold flags it stale ~9 months early — a false positive. Example:
    `enterprise_cyber_spend.ind_01` (Gartner, annual) at 311 days was flagged "stale" even though
    the current_value already held the latest annual figure. Freshness must be measured against the
    indicator's OWN cadence, so `check_frequency` must be correct AND the audit must understand
    every cadence tier (daily → annual).

`check_frequency` is the single source of truth for cadence. If it is wrong, fix the YAML — do not
infer cadence here.

CLI:
    uv run python -m catalyx.scorer.freshness            # pretty table of overdue indicators
    uv run python -m catalyx.scorer.freshness --json     # machine-readable (for the skill)
    uv run python -m catalyx.scorer.freshness --all      # include fresh indicators too
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parents[2]
_STRUCTURAL_DIR = _REPO_ROOT / "catalyx" / "config" / "structural_catalysts"

# Days-since-last_date past which an indicator on this cadence is overdue.
# Each tier = one nominal period + a grace margin (late releases, reporting lag).
OVERDUE_DAYS = {
    "daily": 3,
    "weekly": 10,
    "monthly": 40,
    "quarterly": 95,
    "semiannual": 200,
    "annual": 400,
}
# Cadence used when `check_frequency` is missing or unrecognised — the most conservative
# common cadence, so an unlabelled indicator is flagged rather than silently ignored.
_DEFAULT_CADENCE = "monthly"


def _days_since(last_date: str | None, as_of: date) -> int | None:
    if not last_date:
        return None
    try:
        return (as_of - datetime.fromisoformat(str(last_date)[:10]).date()).days
    except (ValueError, TypeError):
        return None


def audit_indicators(as_of: date | None = None) -> list[dict]:
    """Return one row per indicator across all structural catalysts, with a staleness verdict.

    Each row: catalyst_id, indicator_id, cadence, last_date, days_since, threshold, stale (bool),
    plus `reason` ('overdue' | 'no_last_date' | 'fresh'). Cadence comes straight from the YAML's
    `check_frequency`; an unknown/missing cadence falls back to `_DEFAULT_CADENCE` and is noted.
    """
    as_of = as_of or date.today()
    rows: list[dict] = []
    for f in sorted(_STRUCTURAL_DIR.glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        cid = d.get("id") or d.get("catalyst_id") or f.stem
        for ind in d.get("indicators", []) or []:
            raw_cadence = (ind.get("check_frequency") or "").strip().lower()
            cadence = raw_cadence if raw_cadence in OVERDUE_DAYS else _DEFAULT_CADENCE
            threshold = OVERDUE_DAYS[cadence]
            last_date = ind.get("last_date")
            days = _days_since(last_date, as_of)
            if days is None:
                stale, reason = True, "no_last_date"
            elif days > threshold:
                stale, reason = True, "overdue"
            else:
                stale, reason = False, "fresh"
            rows.append({
                "catalyst_id": cid,
                "indicator_id": ind.get("id"),
                "name": ind.get("name"),
                "cadence": cadence,
                "cadence_mislabeled": bool(raw_cadence) and raw_cadence not in OVERDUE_DAYS,
                "last_date": str(last_date)[:10] if last_date else None,
                "days_since": days,
                "threshold_days": threshold,
                "stale": stale,
                "reason": reason,
            })
    return rows


def overdue(as_of: date | None = None) -> list[dict]:
    """Just the stale indicators — the Step 2 refresh work list."""
    return [r for r in audit_indicators(as_of) if r["stale"]]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX indicator freshness audit (by native cadence)")
    p.add_argument("--json", action="store_true", help="machine-readable output for the skill")
    p.add_argument("--all", action="store_true", help="include fresh indicators, not just overdue")
    p.add_argument("--as-of", default=None, help="audit date YYYY-MM-DD (default today)")
    args = p.parse_args()

    as_of = datetime.fromisoformat(args.as_of).date() if args.as_of else date.today()
    rows = audit_indicators(as_of) if args.all else overdue(as_of)

    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        print(f"✅ No overdue indicators as of {as_of} (audited by native cadence).")
        return
    print(f"Indicator freshness audit — as of {as_of}")
    print(f"{'catalyst':30} {'ind':7} {'cadence':11} {'last_date':11} {'days':>5} {'limit':>5}  flag")
    for r in rows:
        flag = "STALE" if r["stale"] else "ok"
        mis = " ⚠mislabel" if r["cadence_mislabeled"] else ""
        days = "n/a" if r["days_since"] is None else r["days_since"]
        print(f"{r['catalyst_id'][:30]:30} {str(r['indicator_id']):7} {r['cadence']:11} "
              f"{str(r['last_date']):11} {str(days):>5} {r['threshold_days']:>5}  {flag}{mis}")


if __name__ == "__main__":
    main()
