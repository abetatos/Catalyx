"""Scan triage validator — every decided fact C0 saw must end in a recorded outcome.

WHY (docs/PLAN_v15_catalyst_capture.md, F3): on 2026-09-16 the Fed hiked. The scan's C0 read it,
used it as evidence for the gold refresh, and never registered it as an event. "Used as evidence
for another catalyst" is not a decision about the fact itself. The triage file makes the decision
explicit — one row per decided fact, with an outcome — and this validator refuses to call a scan
finished while a due central-bank meeting or the C0 bullets have no row.

File: data/reports/scan_triage_<date>.json  (separate from scan_deltas: that file is a LIST that
four consumers iterate, and a row of another shape would break all four).

    {"scan_date": "...", "items": [{"key", "source", "fact", "outcome", "catalyst_id", "reason"}]}

CLI:
    uv run python -m catalyx.store.scan_triage check --date YYYY-MM-DD [--json]   (exit 0 / 1)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parents[2]
_REPORTS_DIR = _REPO_ROOT / "data" / "reports"
_EVENTS_DIR = _REPO_ROOT / "data" / "catalysts"
_STRUCTURAL_DIR = _REPO_ROOT / "catalyx" / "config" / "structural_catalysts"

SOURCES = ("calendar", "c0")
OUTCOMES = ("registered", "refresh_of", "below_threshold", "not_an_event")
NEEDS_CATALYST = ("registered", "refresh_of")


def validate(triage: dict, state: dict, catalyst_ids: set[str]) -> list[str]:
    """Pure check. Returns human-readable problems; [] means the triage is complete."""
    problems: list[str] = []
    items = triage.get("items")
    if not isinstance(items, list):
        return ["triage has no `items` list"]

    keys = {i.get("key") for i in items if isinstance(i, dict)}
    wl = state.get("work_list") or {}
    if state.get("calendar_error"):
        problems.append(f"state could not evaluate the calendar ({state['calendar_error']}) — "
                        "fix it before triaging; due meetings are unknown")
    for d in wl.get("calendar_due") or []:
        if d["key"] not in keys:
            problems.append(f"central-bank decision {d['key']} ({d['days_ago']}d ago) has no row "
                            "in the triage — search what it decided and triage it")

    for m in wl.get("market_moves") or []:
        if m["key"] not in keys:
            problems.append(f"market move {m['key']} ({m['message']}) has no row in the triage")

    if not any(isinstance(i, dict) and i.get("source") == "c0" for i in items):
        problems.append("no `source: c0` row — C0 always yields decided/official facts; "
                        "zero rows means C0 was not triaged")

    seen: set[str] = set()
    for n, i in enumerate(items):
        if not isinstance(i, dict):
            problems.append(f"item #{n} is not an object")
            continue
        label = i.get("key") or f"item #{n}"
        if not i.get("key"):
            problems.append(f"item #{n} has no `key`")
        elif i["key"] in seen:
            problems.append(f"{label}: duplicate key")
        seen.add(i.get("key") or "")
        if i.get("source") not in SOURCES:
            problems.append(f"{label}: source {i.get('source')!r} not in {SOURCES}")
        if not i.get("fact"):
            problems.append(f"{label}: `fact` is empty")
        out = i.get("outcome")
        if out not in OUTCOMES:
            problems.append(f"{label}: outcome {out!r} not in {OUTCOMES}")
            continue
        if out in NEEDS_CATALYST:
            cid = i.get("catalyst_id")
            if not cid:
                problems.append(f"{label}: outcome {out} requires `catalyst_id`")
            elif cid not in catalyst_ids:
                problems.append(f"{label}: catalyst_id {cid!r} does not exist in data/catalysts/ "
                                "or config/structural_catalysts/")
        elif not (i.get("reason") or "").strip():
            problems.append(f"{label}: outcome {out} requires `reason`"
                            + (" (the strength_score that fell short of 55)"
                               if out == "below_threshold" else ""))
    return problems


def catalyst_ids(events_dir: Path | None = None, structural_dir: Path | None = None) -> set[str]:
    ids: set[str] = set()
    for fp in (events_dir or _EVENTS_DIR).glob("*.json"):
        ids.add(fp.stem)
    for fp in (structural_dir or _STRUCTURAL_DIR).glob("*.yaml"):
        doc = yaml.safe_load(fp.read_text(encoding="utf-8"))
        ids.add((doc or {}).get("id") or fp.stem)
    return ids


def check(date_str: str, reports_dir: Path | None = None, events_dir: Path | None = None,
          structural_dir: Path | None = None) -> list[str]:
    """Disk wrapper. A missing file is a problem, never a pass."""
    rd = reports_dir or _REPORTS_DIR
    compact = date_str.replace("-", "")
    tfile = rd / f"scan_triage_{compact}.json"
    sfile = rd / f"state_{compact}.json"
    if not sfile.exists():
        return [f"{sfile.name} not found — the pipeline requires pre_run.sh before the scan"]
    if not tfile.exists():
        return [f"{tfile.name} not found — write it (Step C0b of catalyx-scan.md)"]
    try:
        triage = json.loads(tfile.read_text(encoding="utf-8"))
        state = json.loads(sfile.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"unreadable JSON: {exc}"]
    return validate(triage, state, catalyst_ids(events_dir, structural_dir))


def main() -> None:
    ap = argparse.ArgumentParser(description="CATALYX — validate the scan triage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--date", required=True, help="YYYY-MM-DD (the scan/state date)")
    c.add_argument("--json", action="store_true")
    args = ap.parse_args()
    problems = check(args.date)
    if args.json:
        json.dump({"date": args.date, "ok": not problems, "problems": problems}, sys.stdout, indent=2)
        print()
    elif problems:
        print(f"✗ scan triage {args.date}: {len(problems)} problem(s)")
        for p in problems:
            print(f"  • {p}")
    else:
        print(f"✓ scan triage {args.date}: complete")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
