"""CATALYX CLI entry point.

Phase 0.5 stub. The deterministic backbone is invoked today via module CLIs
(`uv run python -m catalyx.<module> ...`), which skills call directly. The full
Typer command tree (cmd_scan/score/move/feedback) is Phase 1 work.

Until then this entry point exists so the `catalyx` console script declared in
pyproject.toml resolves to a real callable instead of a missing module. It lists
the module CLIs that are already wired and working.

Run: catalyx   (or: uv run python -m catalyx.cli.main)
"""
from __future__ import annotations

import sys

# (module, one-line description) for the deterministic CLIs that exist today.
_MODULE_CLIS: list[tuple[str, str]] = [
    ("catalyx.data.market_data", "Fetch yfinance ETF momentum snapshot"),
    ("catalyx.data.flow_data", "ETF shares_outstanding × NAV → flow_confirmation"),
    ("catalyx.scorer.intensity_engine", "Structural intensity from indicator semaphores"),
    ("catalyx.scorer.catalyst_scorer", "catalyst_alignment per sector (confirms/contradicts)"),
    ("catalyx.scorer.momentum_engine", "Cross-sectional momentum percentile rank"),
    ("catalyx.scorer.sector_scorer", "Composite SectorSnapshot score (orchestrator)"),
    ("catalyx.execution.tax_engine", "Spanish CGT 2026 progressive brackets"),
    ("catalyx.store.movement_repo", "Movements → positions + catalyst ledger (summary, positions, ledger, ingest)"),
    ("catalyx.store.catalyst_repo", "Catalyst / taxonomy-gap reader (summary, get, set-status)"),
    ("catalyx.store.snapshot_repo", "Score-run history over the parquet lake (record, history, validate)"),
]


def app() -> None:
    """Console-script entry. Lists the wired module CLIs (Phase 0.5)."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("CATALYX — Phase 0.5 (deterministic backbone via module CLIs)\n")
    print("The unified `catalyx <command>` tree is Phase 1. Today, invoke modules directly:\n")
    width = max(len(m) for m, _ in _MODULE_CLIS)
    for module, desc in _MODULE_CLIS:
        print(f"  uv run python -m {module:<{width}}  # {desc}")
    print("\nAppend --help to any module for its arguments.")


if __name__ == "__main__":
    app()
