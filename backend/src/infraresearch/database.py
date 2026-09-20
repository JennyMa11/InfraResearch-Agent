from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base

_settings = get_settings()
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_evidence_scores()
    _migrate_citation_scores()
    _migrate_tool_call_errors()


def _migrate_evidence_scores(bind: Engine = engine) -> None:
    """Add v0.2 evidence score columns to existing SQLite databases."""

    if bind.dialect.name != "sqlite" or "evidence" not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns("evidence")}
    with bind.begin() as connection:
        if "retrieval_score" not in columns:
            connection.execute(
                text("ALTER TABLE evidence ADD COLUMN retrieval_score FLOAT NOT NULL DEFAULT 0")
            )
            connection.execute(text("UPDATE evidence SET retrieval_score = score"))
        if "rerank_score" not in columns:
            connection.execute(text("ALTER TABLE evidence ADD COLUMN rerank_score FLOAT"))


def _migrate_citation_scores(bind: Engine = engine) -> None:
    if bind.dialect.name != "sqlite" or "citations" not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns("citations")}
    if "support_score" not in columns:
        with bind.begin() as connection:
            connection.execute(
                text("ALTER TABLE citations ADD COLUMN support_score FLOAT NOT NULL DEFAULT 0")
            )


def _migrate_tool_call_errors(bind: Engine = engine) -> None:
    if bind.dialect.name != "sqlite" or "tool_calls" not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns("tool_calls")}
    with bind.begin() as connection:
        if "error_type" not in columns:
            connection.execute(text("ALTER TABLE tool_calls ADD COLUMN error_type VARCHAR(40)"))
        if "retryable" not in columns:
            connection.execute(
                text("ALTER TABLE tool_calls ADD COLUMN retryable INTEGER NOT NULL DEFAULT 0")
            )
        if "attempts" not in columns:
            connection.execute(
                text("ALTER TABLE tool_calls ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1")
            )


async def get_db() -> AsyncGenerator[Session, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
