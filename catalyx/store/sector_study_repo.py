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


# ── Core digest — the study a DECISION reads (plan v4 §5 D-d) ────────────────
#
# A study file is ~20 KB and 27 of them exist. Exactly two of their fields are consumed by the
# pipeline: `narrative_maturity` (→ crowding_risk in `snapshot_repo`, and the exhaustion test in
# `catalyst_lifecycle`) and `active_catalyst_ids` (→ the catalyst→sector map in
# `portfolio.catalyst_exposure_rows`). Everything else in the file is the RESEARCH — the reason
# those two values are what they are — and it belongs in front of a human who is rewriting the
# study, not in the context of a run that is only going to read two fields from it.
#
# `get` still returns the whole dossier; `core` returns ~400 bytes.

_CORE_FIELDS = ("sector_id", "sector_label", "last_updated", "study_type",
                "narrative_maturity", "narrative_trend", "active_catalyst_ids",
                "narrative_notes")


def core(sector_id: str) -> dict[str, Any] | None:
    """The consumed fields plus the two that let a reader judge them. None when absent.

    `last_updated` travels with it on purpose: a stale study is worse than no study — it injects
    confident, wrong full-dimension scores — so the freshness must arrive in the same breath as
    the value it qualifies, never one CLI call away.
    """
    for data in _load_all():
        if data.get("sector_id") == sector_id or data.get("id") == sector_id:
            out = {k: data.get(k) for k in _CORE_FIELDS}
            lu = _last_updated(data)
            out["age_days"] = (date.today() - lu).days if lu else None
            return out
    return None


def core_all() -> list[dict[str, Any]]:
    """Every study's core digest, freshest first."""
    rows = [c for c in (core(s.get("sector_id")) for s in _load_all()) if c]
    rows.sort(key=lambda r: (r.get("age_days") is None, r.get("age_days") or 0))
    return rows


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

    p_core = sub.add_parser("core", help="the ~400-byte digest a decision reads, not the dossier")
    p_core.add_argument("sector_id", nargs="?", default=None)
    p_core.add_argument("--all", action="store_true", help="every study's digest")
    p_core.add_argument("--json", action="store_true")

    p_stale = sub.add_parser("stale")
    p_stale.add_argument("--days", type=int, default=30)

    args = parser.parse_args()

    if args.cmd == "core":
        if args.all or not args.sector_id:
            rows = core_all()
            if args.json:
                print(json.dumps(rows, indent=2, default=str))
                return
            for r in rows:
                age = "never" if r["age_days"] is None else f"{r['age_days']}d"
                print(f"  {str(r['sector_id']):<38}{str(r['narrative_maturity'] or '?'):<12}"
                      f"{str(r['narrative_trend'] or '?'):<12}{age:>7}  "
                      f"catalysts={len(r['active_catalyst_ids'] or [])}")
            return
        rec = core(args.sector_id)
        if rec is None:
            print(f"Not found: {args.sector_id}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(rec, indent=2, default=str))
        return

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
