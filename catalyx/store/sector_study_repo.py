"""Storage and retrieval for SectorStudy objects.

Callable from skills via:
    python -m catalyx.store.sector_study_repo <command> [args]

Commands:
    init                     Create database tables
    import-dir <dir>         Import all JSON files from a directory
    import-file <file>       Import a single JSON file
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

from sqlalchemy import Column, Date, Integer, String, Text
from sqlalchemy.types import JSON

from .db import Base, get_session, init_db as _init_db


# ── Model ─────────────────────────────────────────────────────────────────────

class SectorStudy(Base):
    """Bottom-up sector analysis. Maps to schemas/sector_study.json."""
    __tablename__ = "sector_studies"

    id = Column(String(80), primary_key=True)       # study_<sector_id>
    schema_version = Column(String(10), nullable=False)
    sector_id = Column(String(80), nullable=False)
    sector_label = Column(String(200))
    study_type = Column(String(20))                 # full | summary | watch_only
    last_updated = Column(Date)
    analyst_narrative_score = Column(Integer)       # 0-100, queryable for heatmap ranking
    narrative_trend = Column(String(20))            # increasing | stable | decreasing
    raw_data = Column(JSON, nullable=False)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def upsert_study(data: dict[str, Any]) -> SectorStudy:
    session = get_session()
    try:
        row = session.get(SectorStudy, data["id"]) or SectorStudy()
        row.id = data["id"]
        row.schema_version = data.get("schema_version", "1.1")
        row.sector_id = data["sector_id"]
        row.sector_label = data.get("sector_label")
        row.study_type = data.get("study_type", "full")
        row.analyst_narrative_score = data.get("analyst_narrative_score")
        row.narrative_trend = data.get("narrative_trend")
        row.raw_data = data

        if lu := data.get("last_updated"):
            row.last_updated = date.fromisoformat(lu) if isinstance(lu, str) else lu

        session.merge(row)
        session.commit()
        return row
    finally:
        session.close()


def upsert_from_json_file(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    upsert_study(data)
    return data["id"]


def import_from_directory(directory: Path) -> int:
    files = list(directory.glob("*.json"))
    for f in files:
        upsert_from_json_file(f)
    return len(files)


def get_study(id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        row = session.get(SectorStudy, id)
        return row.raw_data if row else None
    finally:
        session.close()


def get_stale(days: int = 30) -> list[SectorStudy]:
    cutoff = date.today() - timedelta(days=days)
    session = get_session()
    try:
        return (
            session.query(SectorStudy)
            .filter((SectorStudy.last_updated == None) | (SectorStudy.last_updated < cutoff))
            .order_by(SectorStudy.last_updated)
            .all()
        )
    finally:
        session.close()


def active_summary() -> str:
    session = get_session()
    try:
        studies = session.query(SectorStudy).order_by(SectorStudy.last_updated.desc()).all()
    finally:
        session.close()

    cutoff_stale = date.today() - timedelta(days=30)
    lines = [f"Sector Studies ({len(studies)}):"]
    if studies:
        for s in studies:
            age = ""
            if s.last_updated:
                age = f"updated={s.last_updated}"
                if s.last_updated < cutoff_stale:
                    age += " [STALE]"
            else:
                age = "updated=never [STALE]"
            score = f"narrative={s.analyst_narrative_score}" if s.analyst_narrative_score is not None else "narrative=?"
            lines.append(f"  {s.id:<40} sector={s.sector_id:<35} {age}  {score}  type={s.study_type or '?'}")
    else:
        lines.append("  (none)")

    stale = [s for s in studies if not s.last_updated or s.last_updated < cutoff_stale]
    if stale:
        lines.append(f"\n  [!] {len(stale)} stale study/studies (>30 days): {', '.join(s.sector_id for s in stale)}")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Catalyx sector study repository")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    p_dir = sub.add_parser("import-dir")
    p_dir.add_argument("directory")

    p_file = sub.add_parser("import-file")
    p_file.add_argument("file")

    sub.add_parser("summary")

    p_get = sub.add_parser("get")
    p_get.add_argument("id")

    p_stale = sub.add_parser("stale")
    p_stale.add_argument("--days", type=int, default=30)

    args = parser.parse_args()

    if args.cmd == "init":
        _init_db()
        print("Database initialised.")
    elif args.cmd == "import-dir":
        n = import_from_directory(Path(args.directory))
        print(f"Imported {n} file(s) from {args.directory}")
    elif args.cmd == "import-file":
        print(f"Imported: {upsert_from_json_file(Path(args.file))}")
    elif args.cmd == "summary":
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
                print(f"  {r.id}  updated={r.last_updated}")


if __name__ == "__main__":
    _cli()
