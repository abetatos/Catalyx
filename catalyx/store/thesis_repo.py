"""Read/query helpers for Thesis and ClosedThesis objects.

The JSON files in data/theses/ are the source of truth (Tier 1). This module reads
them and prints digests for skill context — there is no database. Writing the JSON
file IS the registration; no import step.

A Thesis JSON carries an `id`; a ClosedThesis JSON carries a `thesis_id`.

Callable from skills via:
    python -m catalyx.store.thesis_repo <command> [args]

Commands:
    summary                  Open theses + YTD tax snapshot for Claude context
    get <id>                 Print full JSON for one record
    set-status <id> <status> Update thesis status IN THE JSON FILE
    tax-snapshot             YTD realized gains and tax liability
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parents[2]
_THESES_DIR = _REPO_ROOT / "data" / "theses"

# Spanish CGT brackets 2026 (progressive, no short/long distinction)
_TAX_BRACKETS = [
    (6_000,   0.19),
    (50_000,  0.21),
    (200_000, 0.23),
    (float("inf"), 0.27),
]


# ── File access ───────────────────────────────────────────────────────────────

def _load_all() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not _THESES_DIR.exists():
        return out
    for f in sorted(_THESES_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return out


def _is_closed(data: dict[str, Any]) -> bool:
    return "thesis_id" in data


def _theses() -> list[dict[str, Any]]:
    return [d for d in _load_all() if not _is_closed(d)]


def _closed() -> list[dict[str, Any]]:
    return [d for d in _load_all() if _is_closed(d)]


def _find_file(record_id: str) -> Path | None:
    direct = _THESES_DIR / f"{record_id}.json"
    if direct.exists():
        return direct
    if not _THESES_DIR.exists():
        return None
    for f in _THESES_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if data.get("id") == record_id or data.get("thesis_id") == record_id:
            return f
    return None


def get_thesis(id: str) -> dict[str, Any] | None:
    for data in _load_all():
        if data.get("id") == id or data.get("thesis_id") == id:
            return data
    return None


def set_status(id: str, status: str) -> bool:
    """Update the status field in the thesis JSON file on disk. Returns True if found."""
    path = _find_file(id)
    if path is None:
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = status
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


# ── Tax snapshot ──────────────────────────────────────────────────────────────

def ytd_tax_snapshot() -> dict[str, Any]:
    """Compute YTD realized gains and Spanish CGT tax liability from closed theses."""
    current_year = date.today().year
    closed = [
        c for c in _closed()
        if str(c.get("closed_at", "")).startswith(str(current_year))
    ]

    def _pnl(c: dict[str, Any], key: str) -> float:
        return float((c.get("pnl", {}) or {}).get(key) or 0.0)

    total_gains = sum(_pnl(c, "gross_pnl_eur") for c in closed)
    total_tax = sum(_pnl(c, "tax_liability_eur") for c in closed)
    total_net = sum(_pnl(c, "net_pnl_eur") for c in closed)

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


# ── Summary ───────────────────────────────────────────────────────────────────

def active_summary() -> str:
    open_theses = [t for t in _theses() if t.get("status") in ("draft", "open")]
    open_theses.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    closed_count = len(_closed())

    lines = [f"Open/Draft Theses ({len(open_theses)}):"]
    if open_theses:
        for t in open_theses:
            entry = t.get("entry", {}) or {}
            sector_id = (t.get("sector", {}) or {}).get("sector_id")
            pct = entry.get("position_size_pct_portfolio")
            tier = entry.get("conviction_tier")
            limit_val = entry.get("entry_price_limit")
            size = f"{pct*100:.0f}%" if isinstance(pct, (int, float)) else "?%"
            limit = f"entry_limit={limit_val}" if limit_val is not None else "no_entry_limit"
            lines.append(
                f"  {t.get('id', '?'):<55} sector={sector_id or '?':<35} "
                f"tier={tier or '?'}  size={size}  [{t.get('status', '?')}]  {limit}"
            )
    else:
        lines.append("  (none)")

    snap = ytd_tax_snapshot()
    lines += [
        "",
        f"Closed Theses: {closed_count} total  |  YTD ({snap['year']}): {snap['closed_count']} closed",
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

    parser = argparse.ArgumentParser(
        description="Catalyx thesis reader (file-backed; JSON is the source of truth)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("summary")
    sub.add_parser("tax-snapshot")

    p_get = sub.add_parser("get")
    p_get.add_argument("id")

    p_status = sub.add_parser("set-status")
    p_status.add_argument("id")
    p_status.add_argument("status")

    args = parser.parse_args()

    if args.cmd == "summary":
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
