"""Read/query helpers for SectorStudy objects.

The JSON files in data/sector_studies/ are the source of truth (Tier 1). This module
reads them and prints digests for skill context — there is no database. Writing the
JSON file IS the registration; no import step.

Callable from skills via:
    python -m catalyx.store.sector_study_repo <command> [args]

Commands:
    summary                  Compact summary for Claude context
    get <id>                 Print full JSON for one record
    stale [--days N]         List studies older than N days (default 30)
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parents[2]
_STUDIES_DIR = _REPO_ROOT / "data" / "sector_studies"


# ── File access ───────────────────────────────────────────────────────────────

def _load_all() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not _STUDIES_DIR.exists():
        return out
    for f in sorted(_STUDIES_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return out


def _last_updated(data: dict[str, Any]) -> date | None:
    lu = data.get("last_updated")
    if not lu:
        return None
    try:
        return date.fromisoformat(lu) if isinstance(lu, str) else lu
    except ValueError:
        return None


def get_study(id: str) -> dict[str, Any] | None:
    for data in _load_all():
        if data.get("id") == id:
            return data
    return None


def get_stale(days: int = 30) -> list[dict[str, Any]]:
    cutoff = date.today() - timedelta(days=days)
    stale = [s for s in _load_all()
             if _last_updated(s) is None or _last_updated(s) < cutoff]
    stale.sort(key=lambda s: _last_updated(s) or date.min)
    return stale


# ── Summary ───────────────────────────────────────────────────────────────────

def active_summary() -> str:
    studies = _load_all()
    studies.sort(key=lambda s: _last_updated(s) or date.min, reverse=True)

    cutoff_stale = date.today() - timedelta(days=30)
    lines = [f"Sector Studies ({len(studies)}):"]
    if studies:
        for s in studies:
            lu = _last_updated(s)
            if lu:
                age = f"updated={lu}"
                if lu < cutoff_stale:
                    age += " [STALE]"
            else:
                age = "updated=never [STALE]"
            ns = s.get("analyst_narrative_score")
            score = f"narrative={ns}" if ns is not None else "narrative=?"
            lines.append(
                f"  {s.get('id', '?'):<40} sector={s.get('sector_id', '?'):<35} "
                f"{age}  {score}  type={s.get('study_type') or '?'}"
            )
    else:
        lines.append("  (none)")

    stale = [s for s in studies if _last_updated(s) is None or _last_updated(s) < cutoff_stale]
    if stale:
        ids = ", ".join(s.get("sector_id", "?") for s in stale)
        lines.append(f"\n  [!] {len(stale)} stale study/studies (>30 days): {ids}")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Catalyx sector study reader (file-backed; JSON is the source of truth)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("summary")

    p_get = sub.add_parser("get")
    p_get.add_argument("id")

    p_stale = sub.add_parser("stale")
    p_stale.add_argument("--days", type=int, default=30)

    args = parser.parse_args()

    if args.cmd == "summary":
        print(active_summary())
    elif args.cmd == "get":
        record = get_study(args.id)
        if record is None:
            print(f"Not found: {args.id}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(record, indent=2, default=str))
    elif args.cmd == "stale":
        rows = get_stale(args.days)
        if not rows:
            print(f"No studies older than {args.days} days.")
        else:
            for r in rows:
                print(f"  {r.get('id', '?')}  updated={_last_updated(r)}")


if __name__ == "__main__":
    _cli()
