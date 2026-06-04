"""Read-replica storage for StructuralCatalyst objects (source of truth: YAML files).

The YAML files in catalyx/config/structural_catalysts/ remain the source of truth.
This repo imports them into the DB for queryable access from skills.

After any /catalyx-update that modifies a YAML, re-sync with:
    python -m catalyx.store.structural_catalyst_repo sync

Callable from skills via:
    python -m catalyx.store.structural_catalyst_repo <command> [args]

Commands:
    sync [--dir <dir>]  Re-import all YAML files (default: catalyx/config/structural_catalysts)
    summary             Compact summary for Claude context
    get <id>            Print full YAML content as JSON for one record
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Column, Float, Integer, String, Text
from sqlalchemy.types import JSON

from .db import Base, get_session, init_db as _init_db

_DEFAULT_YAML_DIR = Path(__file__).parents[2] / "catalyx" / "config" / "structural_catalysts"


# ── Model ─────────────────────────────────────────────────────────────────────

class StructuralCatalyst(Base):
    """Read-replica of structural_catalysts/*.yaml. Never write — sync from YAML only."""
    __tablename__ = "structural_catalysts"

    id = Column(String(80), primary_key=True)       # struct_<keyword>
    title = Column(String(200))
    catalyst_type = Column(String(50))
    status = Column(String(20), nullable=False)     # active | weakening | deactivated
    intensity_score = Column(Float)                 # intensity.current_score
    narrative_maturity = Column(String(20))         # ignored | emerging | mainstream | crowded | exhausted
    user_rank = Column(Integer)
    raw_data = Column(JSON, nullable=False)         # full parsed YAML as dict


# ── Import ────────────────────────────────────────────────────────────────────

def _parse_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def upsert_from_yaml(path: Path) -> str:
    data = _parse_yaml(path)
    session = get_session()
    try:
        row = session.get(StructuralCatalyst, data["id"]) or StructuralCatalyst()
        row.id = data["id"]
        row.title = data.get("title")
        row.catalyst_type = data.get("catalyst_type")
        row.status = data.get("status", "active")
        row.intensity_score = data.get("intensity", {}).get("current_score")
        row.narrative_maturity = data.get("narrative_maturity")
        row.user_rank = data.get("user_rank")
        row.raw_data = data
        session.merge(row)
        session.commit()
        return row.id
    finally:
        session.close()


def sync_from_directory(directory: Path | None = None) -> int:
    """Re-import all YAML files. Call after any /catalyx-update."""
    dir_ = directory or _DEFAULT_YAML_DIR
    files = list(dir_.glob("*.yaml"))
    for f in files:
        upsert_from_yaml(f)
    return len(files)


def get_catalyst(id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        row = session.get(StructuralCatalyst, id)
        return row.raw_data if row else None
    finally:
        session.close()


def active_summary() -> str:
    session = get_session()
    try:
        rows = (
            session.query(StructuralCatalyst)
            .filter(StructuralCatalyst.status != "deactivated")
            .order_by(StructuralCatalyst.intensity_score.desc())
            .all()
        )
    finally:
        session.close()

    lines = [f"Structural Catalysts ({len(rows)}):"]
    if rows:
        for r in rows:
            rank = f"rank={r.user_rank}" if r.user_rank else "unranked"
            maturity = r.narrative_maturity or "?"
            intensity = f"{r.intensity_score:.0f}" if r.intensity_score else "?"
            lines.append(
                f"  {r.id:<45} intensity={intensity:<6} [{r.status}]  "
                f"{rank}  maturity={maturity}"
            )
            if r.title:
                lines.append(f"    -> {r.title}")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Catalyx structural catalyst repository (read-replica)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    p_sync = sub.add_parser("sync", help="Re-import all YAMLs into DB")
    p_sync.add_argument("--dir", default=None, help="Override YAML directory")

    sub.add_parser("summary")

    p_get = sub.add_parser("get")
    p_get.add_argument("id")

    args = parser.parse_args()

    if args.cmd == "init":
        _init_db()
        print("Database initialised.")
    elif args.cmd == "sync":
        d = Path(args.dir) if args.dir else None
        n = sync_from_directory(d)
        print(f"Synced {n} structural catalyst(s) from YAML.")
    elif args.cmd == "summary":
        print(active_summary())
    elif args.cmd == "get":
        record = get_catalyst(args.id)
        if record is None:
            print(f"Not found: {args.id}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(record, indent=2, default=str))


if __name__ == "__main__":
    _cli()
