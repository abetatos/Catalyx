"""Read/query helpers for CatalystEvent and TaxonomyGapProposal objects.

The JSON files in data/catalysts/ and data/taxonomy_proposals/ are the source of
truth (Tier 1). This module just reads them and prints digests for skill context —
there is no database. Writing the JSON file IS the registration; no import step.

Callable from skills via:
    python -m catalyx.store.catalyst_repo <command> [args]

Commands:
    summary                  Compact summary for Claude context (active only)
    get <id>                 Print full JSON for one record
    set-status <id> <status> Update the status field IN THE JSON FILE
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parents[2]
_CATALYSTS_DIR = _REPO_ROOT / "data" / "catalysts"
_GAPS_DIR = _REPO_ROOT / "data" / "taxonomy_proposals"


# ── File access ───────────────────────────────────────────────────────────────

def _load_dir(directory: Path) -> list[dict[str, Any]]:
    """Parse every *.json in a directory. Skips unparseable files."""
    out: list[dict[str, Any]] = []
    if not directory.exists():
        return out
    for f in sorted(directory.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — a malformed file shouldn't break the digest
            continue
    return out


def _find_file(record_id: str) -> Path | None:
    """Locate the JSON file backing a record id (by filename, then by `id` field)."""
    for directory in (_CATALYSTS_DIR, _GAPS_DIR):
        direct = directory / f"{record_id}.json"
        if direct.exists():
            return direct
    for directory in (_CATALYSTS_DIR, _GAPS_DIR):
        for f in directory.glob("*.json") if directory.exists() else []:
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("id") == record_id:
                    return f
            except Exception:  # noqa: BLE001
                continue
    return None


def get_catalyst(id: str) -> dict[str, Any] | None:
    for data in _load_dir(_CATALYSTS_DIR):
        if data.get("id") == id:
            return data
    return None


def get_gap(id: str) -> dict[str, Any] | None:
    for data in _load_dir(_GAPS_DIR):
        if data.get("id") == id:
            return data
    return None


def set_status(id: str, status: str) -> bool:
    """Update the status field in the JSON file on disk. Returns True if found."""
    path = _find_file(id)
    if path is None:
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = status
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


# ── Summary ───────────────────────────────────────────────────────────────────

def active_summary() -> str:
    """Compact summary of active records for Claude context consumption."""
    catalysts = [c for c in _load_dir(_CATALYSTS_DIR) if c.get("status", "active") == "active"]
    catalysts.sort(key=lambda c: c.get("strength_score") or 0, reverse=True)
    gaps = [g for g in _load_dir(_GAPS_DIR) if g.get("status") not in ("promoted", "rejected")]
    gaps.sort(key=lambda g: g.get("first_detected") or "", reverse=True)

    lines: list[str] = []

    lines.append(f"Active CatalystEvents ({len(catalysts)}):")
    if catalysts:
        for c in catalysts:
            pi = c.get("is_priced_in_estimate")
            priced = f"priced_in={pi:.2f}" if isinstance(pi, (int, float)) else "priced_in=?"
            ss = c.get("strength_score")
            strength = f"strength={ss:.0f}" if isinstance(ss, (int, float)) else "strength=?"
            subtype = c.get("catalyst_subtype") or "?"
            lines.append(
                f"  {c.get('id', '?'):<45} {c.get('catalyst_type', '?')}/{subtype:<35} "
                f"{strength}  {priced}  [{c.get('status', 'active')}]"
            )
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append(f"Taxonomy gap proposals ({len(gaps)}):")
    if gaps:
        for g in gaps:
            etf = g.get("etf_candidates", {}) or {}
            etf_note = etf.get("pure_play_ticker") or "no pure-play ETF"
            lines.append(
                f"  {g.get('id', '?'):<45} [{g.get('status', '?')}]  signals={g.get('signal_count', 1)}"
                f"  first={g.get('first_detected', '?')}  ETF: {etf_note}"
            )
            if g.get("label_inferred"):
                lines.append(f"    -> {g['label_inferred']}")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    # Force UTF-8 output on Windows consoles that default to cp1252
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Catalyx catalyst reader (file-backed; JSON is the source of truth)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("summary", help="Print compact active-record summary (for Claude context)")

    p_get = sub.add_parser("get", help="Print full JSON for one record by ID")
    p_get.add_argument("id")

    p_status = sub.add_parser("set-status", help="Update status field in the JSON file")
    p_status.add_argument("id")
    p_status.add_argument("status")

    args = parser.parse_args()

    if args.cmd == "summary":
        print(active_summary())

    elif args.cmd == "get":
        record = get_catalyst(args.id) or get_gap(args.id)
        if record is None:
            print(f"Not found: {args.id}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(record, indent=2, default=str))

    elif args.cmd == "set-status":
        if not set_status(args.id, args.status):
            print(f"Not found: {args.id}", file=sys.stderr)
            sys.exit(1)
        print(f"Updated {args.id} -> status={args.status}")


if __name__ == "__main__":
    _cli()
