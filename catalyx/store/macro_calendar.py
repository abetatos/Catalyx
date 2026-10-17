"""Central-bank decision calendar — which known-date decisions has no scan looked at yet?

WHY (docs/PLAN_v15_catalyst_capture.md, F2): the Fed hiked on 2026-09-16 and nothing in the
pipeline went back to look. The decision date was public and already named in a registered
event's own notes. A calendar of fixed dates is deterministic, needs no LLM, and turns "did
anybody check what the Fed did?" into a list the heartbeat can print.

A meeting is DUE when its date is on or after `tracking_start`, is not in the future, and its key
(`<bank>:<YYYY-MM-DD>`) appears in no `data/reports/scan_triage_*.json`. Resolving it is the
scan's job (Step C0b); this module only measures what is open.

CLI:
    uv run python -m catalyx.store.macro_calendar [--as-of YYYY-MM-DD] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parents[2]
_CALENDAR = _REPO_ROOT / "catalyx" / "config" / "macro_calendar.yaml"
_REPORTS_DIR = _REPO_ROOT / "data" / "reports"

BANKS = ("fed", "ecb", "boj", "boe")
KINDS = ("rate_decision",)


def _iso(value, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"macro_calendar: {label} {value!r} is not an ISO date") from exc


def load(path: Path | None = None) -> dict:
    """Read and VALIDATE the calendar. A malformed file raises — it never degrades to empty."""
    cal = yaml.safe_load((path or _CALENDAR).read_text(encoding="utf-8")) or {}
    _iso(cal.get("tracking_start"), "tracking_start")
    seen: set[str] = set()
    for m in cal.get("meetings") or []:
        if m.get("bank") not in BANKS:
            raise ValueError(f"macro_calendar: unknown bank {m.get('bank')!r} (valid: {BANKS})")
        if m.get("kind") not in KINDS:
            raise ValueError(f"macro_calendar: unknown kind {m.get('kind')!r} (valid: {KINDS})")
        _iso(m.get("date"), "meeting date")
        key = meeting_key(m)
        if key in seen:
            raise ValueError(f"macro_calendar: duplicate meeting {key}")
        seen.add(key)
    if not cal.get("meetings"):
        raise ValueError("macro_calendar: no meetings — an empty calendar would read as 'none due'")
    return cal


def meeting_key(m: dict) -> str:
    return f"{m['bank']}:{m['date']}"


def resolved_keys(reports_dir: Path | None = None) -> set[str]:
    """Union of `items[].key` over every scan_triage_*.json. A triage file that cannot be read
    raises: skipping it would re-open a meeting somebody already closed, or worse hide one."""
    keys: set[str] = set()
    for fp in sorted((reports_dir or _REPORTS_DIR).glob("scan_triage_*.json")):
        doc = json.loads(fp.read_text(encoding="utf-8"))
        keys.update(i["key"] for i in doc.get("items") or [] if i.get("key"))
    return keys


def due(as_of: date, cal: dict, resolved: set[str]) -> list[dict]:
    start = _iso(cal["tracking_start"], "tracking_start")
    out = []
    for m in cal["meetings"]:
        d = _iso(m["date"], "meeting date")
        if start <= d <= as_of and meeting_key(m) not in resolved:
            out.append({"key": meeting_key(m), "bank": m["bank"], "date": m["date"],
                        "days_ago": (as_of - d).days})
    return sorted(out, key=lambda r: (r["date"], r["bank"]))


def upcoming(as_of: date, cal: dict, days: int = 14) -> list[dict]:
    end = as_of + timedelta(days=days)
    out = []
    for m in cal["meetings"]:
        d = _iso(m["date"], "meeting date")
        if as_of < d <= end:
            out.append({"key": meeting_key(m), "bank": m["bank"], "date": m["date"],
                        "days_ahead": (d - as_of).days})
    return sorted(out, key=lambda r: (r["date"], r["bank"]))


def main() -> None:
    ap = argparse.ArgumentParser(description="CATALYX — central-bank decisions not yet triaged")
    ap.add_argument("--as-of", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    cal = load()
    d, u = due(as_of, cal, resolved_keys()), upcoming(as_of, cal)
    if args.json:
        json.dump({"as_of": as_of.isoformat(), "due": d, "upcoming": u}, sys.stdout, indent=2)
        print()
        return
    print(f"CATALYX — macro calendar  {as_of}")
    print(f"  DUE (decided, no scan has triaged them): {len(d)}")
    for r in d:
        print(f"    {r['key']:<18}{r['days_ago']:>4}d ago")
    print(f"  UPCOMING (next 14d): {len(u)}")
    for r in u:
        print(f"    {r['key']:<18}in {r['days_ahead']}d")


if __name__ == "__main__":
    main()
