"""Tests run against a real PostgreSQL database (``STUDIO_TEST_DATABASE_URL``), migrated
with Alembic once per session. Each test runs inside a transaction that is rolled back."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine
from sqlalchemy.orm import Session

TEST_DB = os.environ.get(
    "STUDIO_TEST_DATABASE_URL", "postgresql+psycopg://studio:studio@localhost:5432/studio_test"
)
os.environ["STUDIO_DATABASE_URL"] = TEST_DB
os.environ.setdefault("STUDIO_LOG_JSON", "false")

BACKEND = Path(__file__).resolve().parents[1]


def alembic_config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["database_url"] = TEST_DB
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def migrated() -> str:
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return TEST_DB


@pytest.fixture
def connection(migrated: str) -> Iterator[Connection]:
    engine = create_engine(migrated)
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            yield conn
        finally:
            tx.rollback()
    engine.dispose()


@pytest.fixture
def session(connection: Connection) -> Iterator[Session]:
    with Session(bind=connection, join_transaction_mode="create_savepoint") as s:
        yield s
