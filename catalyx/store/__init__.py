"""Catalyx store package.

init_all() creates all tables and imports all existing data files.
Call once after cloning or when setting up a fresh DB.
"""
from __future__ import annotations

from pathlib import Path


def init_all(data_dir: Path | None = None) -> None:
    """Create all tables and import all existing data files into the DB.

    Import order matters: structural catalysts first (referenced by catalyst events),
    then events, then studies, then theses.
    """
    from . import (
        catalyst_repo,
        sector_study_repo,
        structural_catalyst_repo,
        thesis_repo,
    )
    from .db import init_db

    root = Path(__file__).parents[2]
    data = data_dir or root / "data"

    init_db()

    structural_catalyst_repo.sync_from_directory()

    catalysts_dir = data / "catalysts"
    if catalysts_dir.exists():
        catalyst_repo.import_from_directory(catalysts_dir)

    gaps_dir = data / "taxonomy_proposals"
    if gaps_dir.exists():
        catalyst_repo.import_from_directory(gaps_dir)

    studies_dir = data / "sector_studies"
    if studies_dir.exists():
        sector_study_repo.import_from_directory(studies_dir)

    theses_dir = data / "theses"
    if theses_dir.exists():
        thesis_repo.import_from_directory(theses_dir)
