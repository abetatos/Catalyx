"""Storage and retrieval for CatalystEvent and TaxonomyGapProposal objects.

Callable from skills via:
    python -m catalyx.store.catalyst_repo <command> [args]

Commands:
    init                     Create database tables
    import-dir <dir>         Import all JSON files from a directory
    import-file <file>       Import a single JSON file
    summary                  Compact summary for Claude context (active only)
    get <id>                 Print full JSON for one record
    set-status <id> <status> Update status field
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Date, Float, Integer, String, Text
from sqlalchemy.types import JSON

from .db import Base, get_session, init_db as _init_db


# ── Models ────────────────────────────────────────────────────────────────────

class CatalystEvent(Base):
    """Discrete, timestamped macro event. Maps to schemas/catalyst_event.json."""
    __tablename__ = "catalyst_events"

    id = Column(String(80), primary_key=True)
    schema_version = Column(String(10), nullable=False)
    catalyst_type = Column(String(50), nullable=False)
    catalyst_subtype = Column(String(50))
    description = Column(Text, nullable=False)
    magnitude = Column(String(20))
    strength_score = Column(Float)
    novelty_score = Column(Integer)
    consensus_surprise = Column(Float)
    is_priced_in_estimate = Column(Float)
    relation_to_structural = Column(String(20))
    status = Column(String(20), nullable=False, default="active")
    geography = Column(JSON)
    tags = Column(JSON)
    related_catalyst_ids = Column(JSON)
    created_at = Column(String(30))   # ISO 8601 string — timezone-aware, no SQLite datetime quirks
    detected_at = Column(String(30))
    expires_at = Column(Date)
    decay_halflife_days = Column(Float)
    user_rank = Column(Integer)
    user_notes = Column(Text)
    raw_data = Column(JSON, nullable=False)  # full original JSON — source of truth for round-trips


class TaxonomyGapProposal(Base):
    """Emerging theme not yet in sector_taxonomy.yaml. Maps to schemas/taxonomy_gap_proposal.json."""
    __tablename__ = "taxonomy_gap_proposals"

    id = Column(String(80), primary_key=True)
    schema_version = Column(String(10), nullable=False)
    status = Column(String(30), nullable=False)
    first_detected = Column(Date, nullable=False)
    last_seen = Column(Date)
    signal_count = Column(Integer, nullable=False, default=1)
    label_inferred = Column(String(200))
    parent_sector_inferred = Column(String(50))
    investability_assessment = Column(String(50))
    proposed_sector_id = Column(String(80))
    promoted_date = Column(Date)
    rejection_reason = Column(Text)
    evidence = Column(JSON)           # array — queryable detail lives in raw_data
    raw_data = Column(JSON, nullable=False)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def upsert_catalyst(data: dict[str, Any]) -> CatalystEvent:
    """Insert or replace a CatalystEvent from its JSON dict."""
    session = get_session()
    try:
        row = session.get(CatalystEvent, data["id"]) or CatalystEvent()
        row.id = data["id"]
        row.schema_version = data.get("schema_version", "1.1")
        row.catalyst_type = data["catalyst_type"]
        row.catalyst_subtype = data.get("catalyst_subtype")
        row.description = data["description"]
        row.magnitude = data.get("magnitude")
        row.strength_score = data.get("strength_score")
        row.novelty_score = data.get("novelty_score")
        row.consensus_surprise = data.get("consensus_surprise")
        row.is_priced_in_estimate = data.get("is_priced_in_estimate")
        row.relation_to_structural = data.get("relation_to_structural")
        row.status = data.get("status", "active")
        row.geography = data.get("geography", [])
        row.tags = data.get("tags", [])
        row.related_catalyst_ids = data.get("related_catalyst_ids", [])
        row.created_at = data.get("created_at")
        row.detected_at = data.get("detected_at")
        row.decay_halflife_days = data.get("decay_halflife_days")
        row.user_rank = data.get("user_rank")
        row.user_notes = data.get("user_notes")
        row.raw_data = data

        if expires := data.get("expires_at"):
            row.expires_at = date.fromisoformat(expires) if isinstance(expires, str) else expires

        session.merge(row)
        session.commit()
        return row
    finally:
        session.close()


def upsert_gap(data: dict[str, Any]) -> TaxonomyGapProposal:
    """Insert or replace a TaxonomyGapProposal from its JSON dict."""
    session = get_session()
    try:
        row = session.get(TaxonomyGapProposal, data["id"]) or TaxonomyGapProposal()
        row.id = data["id"]
        row.schema_version = data.get("schema_version", "1.0")
        row.status = data["status"]
        row.signal_count = data.get("signal_count", 1)
        row.label_inferred = data.get("label_inferred")
        row.parent_sector_inferred = data.get("parent_sector_inferred")
        row.investability_assessment = data.get("investability_assessment")
        row.proposed_sector_id = data.get("proposed_sector_id")
        row.rejection_reason = data.get("rejection_reason")
        row.evidence = data.get("evidence", [])
        row.raw_data = data

        row.first_detected = date.fromisoformat(data["first_detected"])
        if last := data.get("last_seen"):
            row.last_seen = date.fromisoformat(last)
        if promoted := data.get("promoted_date"):
            row.promoted_date = date.fromisoformat(promoted)

        session.merge(row)
        session.commit()
        return row
    finally:
        session.close()


def upsert_from_json_file(path: Path) -> str:
    """Import a single JSON file. Returns the record id."""
    data = json.loads(path.read_text(encoding="utf-8"))
    record_id: str = data["id"]
    if record_id.startswith("cat_"):
        upsert_catalyst(data)
    elif record_id.startswith("gap_"):
        upsert_gap(data)
    else:
        raise ValueError(f"Unrecognised ID prefix in {path}: {record_id}")
    return record_id


def import_from_directory(directory: Path) -> int:
    """Import all *.json files from a directory. Returns count imported."""
    files = list(directory.glob("*.json"))
    for f in files:
        upsert_from_json_file(f)
    return len(files)


def get_catalyst(id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        row = session.get(CatalystEvent, id)
        return row.raw_data if row else None
    finally:
        session.close()


def get_gap(id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        row = session.get(TaxonomyGapProposal, id)
        return row.raw_data if row else None
    finally:
        session.close()


def set_status(id: str, status: str) -> bool:
    """Update status on a CatalystEvent or TaxonomyGapProposal. Returns True if found."""
    session = get_session()
    try:
        row = session.get(CatalystEvent, id) or session.get(TaxonomyGapProposal, id)
        if row is None:
            return False
        row.status = status
        row.raw_data = {**row.raw_data, "status": status}
        session.commit()
        return True
    finally:
        session.close()


def active_summary() -> str:
    """Compact summary of active records for Claude context consumption.

    Designed to replace reading N JSON files in skill context: one call returns
    a ~30-token-per-row digest instead of ~150 tokens per file.
    """
    session = get_session()
    try:
        catalysts = (
            session.query(CatalystEvent)
            .filter(CatalystEvent.status == "active")
            .order_by(CatalystEvent.strength_score.desc())
            .all()
        )
        gaps = (
            session.query(TaxonomyGapProposal)
            .filter(TaxonomyGapProposal.status.notin_(["promoted", "rejected"]))
            .order_by(TaxonomyGapProposal.first_detected.desc())
            .all()
        )
    finally:
        session.close()

    lines: list[str] = []

    lines.append(f"Active CatalystEvents ({len(catalysts)}):")
    if catalysts:
        for c in catalysts:
            priced = f"priced_in={c.is_priced_in_estimate:.2f}" if c.is_priced_in_estimate is not None else "priced_in=?"
            strength = f"strength={c.strength_score:.0f}" if c.strength_score else "strength=?"
            lines.append(
                f"  {c.id:<45} {c.catalyst_type}/{c.catalyst_subtype or '?':<35} "
                f"{strength}  {priced}  [{c.status}]"
            )
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append(f"Taxonomy gap proposals ({len(gaps)}):")
    if gaps:
        for g in gaps:
            etf = g.raw_data.get("etf_candidates", {})
            etf_note = etf.get("pure_play_ticker") or "no pure-play ETF"
            lines.append(
                f"  {g.id:<45} [{g.status}]  signals={g.signal_count}"
                f"  first={g.first_detected}  ETF: {etf_note}"
            )
            if g.label_inferred:
                lines.append(f"    -> {g.label_inferred}")
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
        description="Catalyx catalyst repository — callable from skills",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Create database tables")

    p_dir = sub.add_parser("import-dir", help="Import all JSON files from a directory")
    p_dir.add_argument("directory", help="Path to directory containing JSON files")

    p_file = sub.add_parser("import-file", help="Import a single JSON file")
    p_file.add_argument("file", help="Path to JSON file")

    sub.add_parser("summary", help="Print compact active-record summary (for Claude context)")

    p_get = sub.add_parser("get", help="Print full JSON for one record by ID")
    p_get.add_argument("id")

    p_status = sub.add_parser("set-status", help="Update status field on a record")
    p_status.add_argument("id")
    p_status.add_argument("status")

    args = parser.parse_args()

    if args.cmd == "init":
        _init_db()
        print("Database initialised.")

    elif args.cmd == "import-dir":
        n = import_from_directory(Path(args.directory))
        print(f"Imported {n} file(s) from {args.directory}")

    elif args.cmd == "import-file":
        record_id = upsert_from_json_file(Path(args.file))
        print(f"Imported: {record_id}")

    elif args.cmd == "summary":
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
        print(f"Updated {args.id} → status={args.status}")


if __name__ == "__main__":
    _cli()
