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


def merged_map() -> dict[str, str]:
    """`{absorbed_id: merged_into_id}` for every catalyst with `status: merged`."""
    return {d["id"]: d["merged_into"] for d in _load_all()
            if d.get("status") == "merged" and d.get("merged_into") and d.get("id")}


def resolve(catalyst_id: str, _map: dict[str, str] | None = None) -> str:
    """Follow `merged_into` to the catalyst that is actually SCORED today.

    Movements keep the catalyst id they were opened against — correctly, that is the historical
    record — but after the 2026-08-27 merges several of those ids are `status: merged` and
    `compute_all()` skips them. Anything that asks "is this position's driver still healthy?"
    must ask the SURVIVOR: re-verifying a merged file spends a search on a catalyst nothing
    scores, and reading its `status_last_reviewed` yields a fresh-looking date (the merge stamped
    it) for a thesis nobody has checked since.

    Unknown ids pass through unchanged. Chains are followed; a cycle stops rather than hangs.
    """
    m = merged_map() if _map is None else _map
    seen, cur = set(), catalyst_id
    while cur in m and cur not in seen:
        seen.add(cur)
        cur = m[cur]
    return cur


def resolve_all(catalyst_ids) -> list[str]:
    """`resolve` over a list, de-duplicated, order preserved. One YAML read for the whole list."""
    m = merged_map()
    out, seen = [], set()
    for cid in catalyst_ids or []:
        r = resolve(cid, m)
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


# ── Summary ───────────────────────────────────────────────────────────────────

def active_summary() -> str:
    # Universo v2.0: 'merged' (fusionado en otro catalizador) y role 'macro_context'
    # (regimen sin vehiculo, no posicion) tampoco son activos. Antes solo se excluia
    # 'deactivated', asi que los fusionados seguian inflando el resumen y el dashboard.
    rows = [
        r for r in _load_all()
        if r.get("status") not in ("deactivated", "merged")
        and r.get("role") != "macro_context"
    ]
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
