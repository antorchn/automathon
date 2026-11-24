"""Couche d'accès base de données (SQLite en V0, compatible PostgreSQL ensuite)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker


def is_sqlite_url(database_url: str) -> bool:
    return database_url.startswith("sqlite:")


def create_db_engine(database_url: str) -> Engine:
    connect_args: dict = {}
    if is_sqlite_url(database_url):
        connect_args = {"check_same_thread": False, "timeout": 30.0}
        db_file = make_url(database_url).database
        if db_file and db_file != ":memory:":
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        database_url,
        connect_args=connect_args,
        future=True,
        pool_pre_ping=True,
    )

    if is_sqlite_url(database_url):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=30000;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


@contextmanager
def write_tx(session: Session) -> Iterator[Session]:
    """Transaction d'écriture immédiate (évite les deadlocks SQLite lors du claim atomique)."""
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
