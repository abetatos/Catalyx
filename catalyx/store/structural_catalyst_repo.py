"""Read/query helpers for StructuralCatalyst objects.

The YAML files in catalyx/config/structural_catalysts/ are the source of truth (Tier 1).
This module reads them and prints digests for skill context — there is no database, and
no sync step: editing the YAML is the only write path.

Callable from skills via:
    python -m catalyx.store.structural_catalyst_repo <command> [args]

Commands:
    summary             Compact summary for Claude context
    get <id>            Print full YAML content as JSON for one record
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

_YAML_DIR = Path(__file__).parents[2] / "catalyx" / "config" / "structural_catalysts"


# ── File access ───────────────────────────────────────────────────────────────

def _load_all() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not _YAML_DIR.exists():
        return out
    for f in sorted(_YAML_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out.append(data)
        except Exception:  # noqa: BLE001
            continue
    return out


def get_catalyst(id: str) -> dict[str, Any] | None:
    for data in _load_all():
        if data.get("id") == id:
            return data
    return None


# ── Summary ───────────────────────────────────────────────────────────────────

def active_summary() -> str:
    rows = [r for r in _load_all() if r.get("status") != "deactivated"]
    rows.sort(key=lambda r: (r.get("intensity", {}) or {}).get("current_score") or 0, reverse=True)

    lines = [f"Structural Catalysts ({len(rows)}):"]
    if rows:
        for r in rows:
            rank = f"rank={r['user_rank']}" if r.get("user_rank") else "unranked"
            maturity = r.get("narrative_maturity") or "?"
            isc = (r.get("intensity", {}) or {}).get("current_score")
            intensity = f"{isc:.0f}" if isinstance(isc, (int, float)) else "?"
            lines.append(
                f"  {r.get('id', '?'):<45} intensity={intensity:<6} [{r.get('status', 'active')}]  "
                f"{rank}  maturity={maturity}"
            )
            if r.get("title"):
                lines.append(f"    -> {r['title']}")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Catalyx structural catalyst reader (file-backed; YAML is the source of truth)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("summary")

    p_get = sub.add_parser("get")
    p_get.add_argument("id")

    args = parser.parse_args()

    if args.cmd == "summary":
        print(active_summary())
    elif args.cmd == "get":
        record = get_catalyst(args.id)
        if record is None:
            print(f"Not found: {args.id}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(record, indent=2, default=str))


if __name__ == "__main__":
    _cli()
