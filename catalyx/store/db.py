"""Database engine, session factory, and shared models.

All models in the store package inherit from Base defined here.
Call init_db() after importing all repo modules to create tables.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()

_REPO_ROOT = Path(__file__).parents[2]
_DEFAULT_DB_URL = f"sqlite:///{_REPO_ROOT / 'data' / 'catalyx.db'}"

_engine = None
_SessionFactory = None


def get_db_url() -> str:
    return os.getenv("CATALYX_DB_URL", _DEFAULT_DB_URL)


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_db_url(), echo=False)
    return _engine


def get_session() -> Session:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory()


class Base(DeclarativeBase):
    pass


class LLMLog(Base):
    """One row per LLM API call. Required for all calls in Phase 1+."""
    __tablename__ = "llm_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    model_id = Column(String(100), nullable=False)
    calling_function = Column(String(200), nullable=False)
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    total_tokens = Column(Integer)
    cost_usd = Column(Float)
    request_id = Column(String(100))
    notes = Column(Text)


def init_db() -> None:
    """Create all tables. Caller must have imported all repo modules first.

    Usage:
        from catalyx.store import catalyst_repo  # registers its models
        from catalyx.store.db import init_db
        init_db()
    """
    Base.metadata.create_all(get_engine())
