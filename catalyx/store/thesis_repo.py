"""Storage and retrieval for Thesis and ClosedThesis objects.

Callable from skills via:
    python -m catalyx.store.thesis_repo <command> [args]

Commands:
    init                     Create database tables
    import-dir <dir>         Import all JSON files from a directory
    import-file <file>       Import a single JSON file
    summary                  Open theses + YTD tax snapshot for Claude context
    get <id>                 Print full JSON for one record
    set-status <id> <status> Update thesis status
    tax-snapshot             YTD realized gains and tax liability
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

# Spanish CGT brackets 2026 (progressive, no short/long distinction)
_TAX_BRACKETS = [
    (6_000,   0.19),
    (50_000,  0.21),
    (200_000, 0.23),
    (float("inf"), 0.27),
]


# ── Models ────────────────────────────────────────────────────────────────────

class Thesis(Base):
    """Active investment thesis. Maps to schemas/thesis.json."""
    __tablename__ = "theses"

    id = Column(String(100), primary_key=True)
    schema_version = Column(String(10), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(40), nullable=False)
    sector_id = Column(String(80))
    catalyst_event_id = Column(String(80))
    catalyst_type = Column(String(20))         # event | structural
    primary_etf = Column(String(20))
    conviction_tier = Column(Integer)
    position_size_pct = Column(Float)
    entry_price_limit = Column(Float)
    created_at = Column(String(30))
    raw_data = Column(JSON, nullable=False)


class ClosedThesis(Base):
    """Closed thesis with P&L and attribution. Maps to schemas/closed_thesis.json."""
    __tablename__ = "closed_theses"

    thesis_id = Column(String(100), primary_key=True)
    schema_version = Column(String(10), nullable=False)
    closed_at = Column(String(30))
    close_reason = Column(String(40))
    sector_id = Column(String(80))
    primary_etf = Column(String(20))
    holding_days = Column(Integer)
    gross_return_pct = Column(Float)
    gross_pnl_eur = Column(Float)
    net_pnl_eur = Column(Float)
    tax_liability_eur = Column(Float)
    matrix_cell = Column(String(20))           # confirmed | bad_luck | lucky | avoided_future
    right_reason_score = Column(Float)
    raw_data = Column(JSON, nullable=False)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def upsert_thesis(data: dict[str, Any]) -> Thesis:
    session = get_session()
    try:
        row = session.get(Thesis, data["id"]) or Thesis()
        row.id = data["id"]
        row.schema_version = data.get("schema_version", "1.1")
        row.version = data.get("version", 1)
        row.status = data["status"]
        row.sector_id = data.get("sector", {}).get("sector_id")
        row.catalyst_event_id = data.get("catalyst", {}).get("catalyst_event_id")
        row.catalyst_type = data.get("catalyst", {}).get("catalyst_type")
        row.primary_etf = data.get("vehicle", {}).get("primary_etf")
        row.conviction_tier = data.get("entry", {}).get("conviction_tier")
        row.position_size_pct = data.get("entry", {}).get("position_size_pct_portfolio")
        row.entry_price_limit = data.get("entry", {}).get("entry_price_limit")
        row.created_at = data.get("created_at")
        row.raw_data = data
        session.merge(row)
        session.commit()
        return row
    finally:
        session.close()


def upsert_closed(data: dict[str, Any]) -> ClosedThesis:
    session = get_session()
    try:
        row = session.get(ClosedThesis, data["thesis_id"]) or ClosedThesis()
        row.thesis_id = data["thesis_id"]
        row.schema_version = data.get("schema_version", "1.1")
        row.closed_at = data.get("closed_at")
        row.close_reason = data.get("close_reason")
        row.sector_id = data.get("thesis_snapshot", {}).get("sector", {}).get("sector_id")
        row.primary_etf = data.get("execution", {}).get("etf_ticker")
        row.holding_days = data.get("execution", {}).get("holding_days")
        row.gross_return_pct = data.get("pnl", {}).get("gross_return_pct")
        row.gross_pnl_eur = data.get("pnl", {}).get("gross_pnl_eur")
        row.net_pnl_eur = data.get("pnl", {}).get("net_pnl_eur")
        row.tax_liability_eur = data.get("pnl", {}).get("tax_liability_eur")
        row.matrix_cell = data.get("scores", {}).get("matrix_cell")
        row.right_reason_score = data.get("scores", {}).get("right_reason_score")
        row.raw_data = data
        session.merge(row)
        session.commit()
        return row
    finally:
        session.close()


def upsert_from_json_file(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "thesis_id" in data:        # ClosedThesis
        upsert_closed(data)
        return data["thesis_id"]
    else:                          # Thesis
        upsert_thesis(data)
        return data["id"]


def import_from_directory(directory: Path) -> int:
    files = list(directory.glob("*.json"))
    for f in files:
        upsert_from_json_file(f)
    return len(files)


def get_thesis(id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        row = session.get(Thesis, id) or session.get(ClosedThesis, id)
        return row.raw_data if row else None
    finally:
        session.close()


def set_status(id: str, status: str) -> bool:
    session = get_session()
    try:
        row = session.get(Thesis, id)
        if row is None:
            return False
        row.status = status
        row.raw_data = {**row.raw_data, "status": status}
        session.commit()
        return True
    finally:
        session.close()


def ytd_tax_snapshot() -> dict[str, Any]:
    """Compute YTD realized gains and Spanish CGT tax liability from closed theses."""
    current_year = date.today().year
    session = get_session()
    try:
        closed = (
            session.query(ClosedThesis)
            .filter(ClosedThesis.closed_at.like(f"{current_year}%"))
            .all()
        )
    finally:
        session.close()

    total_gains = sum(r.gross_pnl_eur or 0.0 for r in closed)
    total_tax = sum(r.tax_liability_eur or 0.0 for r in closed)
    total_net = sum(r.net_pnl_eur or 0.0 for r in closed)

    # Determine current marginal bracket
    marginal = 0.19
    cumulative = 0.0
    for ceiling, rate in _TAX_BRACKETS:
        if total_gains <= cumulative:
            break
        marginal = rate
        cumulative += ceiling

    return {
        "year": current_year,
        "closed_count": len(closed),
        "realized_gains_eur": round(total_gains, 2),
        "tax_paid_eur": round(total_tax, 2),
        "net_pnl_eur": round(total_net, 2),
        "current_marginal_bracket_pct": marginal,
    }


def active_summary() -> str:
    session = get_session()
    try:
        open_theses = (
            session.query(Thesis)
            .filter(Thesis.status.in_(["draft", "open"]))
            .order_by(Thesis.created_at.desc())
            .all()
        )
        closed = session.query(ClosedThesis).count()
    finally:
        session.close()

    lines = [f"Open/Draft Theses ({len(open_theses)}):"]
    if open_theses:
        for t in open_theses:
            size = f"{t.position_size_pct*100:.0f}%" if t.position_size_pct else "?%"
            limit = f"entry_limit={t.entry_price_limit}" if t.entry_price_limit else "no_entry_limit"
            lines.append(
                f"  {t.id:<55} sector={t.sector_id or '?':<35} "
                f"tier={t.conviction_tier or '?'}  size={size}  [{t.status}]  {limit}"
            )
    else:
        lines.append("  (none)")

    snap = ytd_tax_snapshot()
    lines += [
        "",
        f"Closed Theses: {closed} total  |  YTD ({snap['year']}): {snap['closed_count']} closed",
        f"  Realized gains: EUR {snap['realized_gains_eur']:,.2f}",
        f"  Tax paid:        EUR {snap['tax_paid_eur']:,.2f}",
        f"  Net P&L:         EUR {snap['net_pnl_eur']:,.2f}",
        f"  Current marginal bracket: {snap['current_marginal_bracket_pct']*100:.0f}%",
    ]
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Catalyx thesis repository")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    p_dir = sub.add_parser("import-dir")
    p_dir.add_argument("directory")

    p_file = sub.add_parser("import-file")
    p_file.add_argument("file")

    sub.add_parser("summary")
    sub.add_parser("tax-snapshot")

    p_get = sub.add_parser("get")
    p_get.add_argument("id")

    p_status = sub.add_parser("set-status")
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
        print(f"Imported: {upsert_from_json_file(Path(args.file))}")
    elif args.cmd == "summary":
        print(active_summary())
    elif args.cmd == "tax-snapshot":
        print(json.dumps(ytd_tax_snapshot(), indent=2))
    elif args.cmd == "get":
        record = get_thesis(args.id)
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
