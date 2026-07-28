#!/usr/bin/env python3
"""CATALYX Claude Code hooks (cross-platform).

Replaces the earlier PowerShell hooks, which were dead on macOS/Linux (`powershell`
absent) and used a non-existent `$env:TOOL_OUTPUT` interface. Claude Code passes the
hook payload as JSON on stdin; this reads it and emits a short reminder to stdout.

Usage (from .claude/settings.json):  python3 guard.py <mode>
  mode ∈ {pre-edit, post-edit, post-bash}

Never blocks (always exits 0) — these are advisory nudges, not gates.
"""
import json
import re
import sys


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool_input = payload.get("tool_input") or {}

    if mode == "pre-edit":
        f = tool_input.get("file_path", "") or ""
        if re.search(r"schemas/|sector_taxonomy|catalyst_taxonomy|scoring_weights|structural_catalysts/", f):
            print("📋 CONFIG/SCHEMA FILE — read CLAUDE.md §Schema Change Protocol before editing")

    elif mode == "post-edit":
        f = tool_input.get("file_path", "") or ""
        if re.search(r"schemas/", f):
            print("⚠ SCHEMA MODIFIED — (1) bump schema_version (2) migrate data/ JSON files "
                  "(3) add a CLAUDE.md Recent Changes note")
        elif "sector_taxonomy" in f:
            print("⚠ TAXONOMY MODIFIED — (1) etf_universe.yaml coverage for new sectors "
                  "(2) grep data/movements/ for removed sector_ids (3) scoring_weights demand_driver")
        elif re.search(r"structural_catalysts/", f):
            print("⚠ STRUCTURAL CATALYST MODIFIED — (1) update intensity history if current_score changed "
                  "(2) status_last_reviewed (3) linked_event_catalyst_ids is current")

    elif mode == "post-bash":
        cmd = tool_input.get("command", "") or ""
        if re.search(r"snapshot_repo\s+record\b", cmd):
            print("↻ Score run recorded. Refresh model + real portfolios and NAV vs SPY with "
                  "`bash scripts/post_run.sh` (one call; delegate to a subagent per the review Execution Model).")

    return 0


if __name__ == "__main__":
    sys.exit(main())