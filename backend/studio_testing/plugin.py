"""Shared fixtures. Tests run against a real PostgreSQL database (``STUDIO_TEST_DATABASE_URL``), migrated
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

# Object storage: a real S3-compatible server (infra/seaweedfs in compose). The API identity
# is the process default; tests that need another identity build their own Storage.
S3_ENDPOINT = os.environ.get("STUDIO_TEST_S3_ENDPOINT", "http://localhost:8333")
os.environ["STUDIO_S3_ENDPOINT_URL"] = S3_ENDPOINT
os.environ.setdefault("STUDIO_S3_ACCESS_KEY", "dev-api")

# Authentication: a signing key made for this test session, served as a static JWKS.
TEST_ISSUER = "http://test-idp.local/realms/studio"
TEST_AUDIENCE = "studio-api"
_TEST_KEY = None


def _signing_key():
    global _TEST_KEY
    if _TEST_KEY is None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        _TEST_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _TEST_KEY


def _jwks() -> str:
    import json as _json

    import jwt as _jwt

    jwk = _json.loads(_jwt.algorithms.RSAAlgorithm.to_jwk(_signing_key().public_key()))
    jwk.update(kid="test-key", use="sig", alg="RS256")
    return _json.dumps({"keys": [jwk]})


os.environ["STUDIO_AUTH_ISSUER"] = TEST_ISSUER
os.environ["STUDIO_AUTH_AUDIENCE"] = TEST_AUDIENCE
os.environ["STUDIO_AUTH_JWKS_JSON"] = _jwks()


def make_token(sub: str = "user-admin", roles: list[str] | None = None, *, key=None, kid: str = "test-key",
               issuer: str = TEST_ISSUER, audience: str = TEST_AUDIENCE, expires_in: int = 300, **claims) -> str:
    import time as _time

    import jwt as _jwt

    now = int(_time.time())
    payload = {"sub": sub, "iss": issuer, "aud": audience, "iat": now, "exp": now + expires_in,
               "realm_access": {"roles": roles if roles is not None else ["admin"]},
               "email": f"{sub}@studio.test", "preferred_username": sub, **claims}
    return _jwt.encode(payload, key or _signing_key(), algorithm="RS256", headers={"kid": kid})
os.environ.setdefault("STUDIO_S3_SECRET_KEY", "dev-api-secret")
DEV_KEYS = {
    "admin": ("dev-admin", "dev-admin-secret"),
    "api": ("dev-api", "dev-api-secret"),
    "worker": ("dev-worker", "dev-worker-secret"),
    "publisher": ("dev-publisher", "dev-publisher-secret"),
}

BACKEND = Path(__file__).resolve().parents[1]  # backend/


def alembic_config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["database_url"] = TEST_DB
    cfg.attributes["configure_logger"] = False
    return cfg


def reset_schema() -> None:
    """An empty database. Downgrading is not enough once tests have published versions: the
    immutability guards refuse to delete them, as they would in production."""
    from sqlalchemy import text

    engine = create_engine(TEST_DB)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture(scope="session")
def migrated() -> str:
    reset_schema()
    command.upgrade(alembic_config(), "head")
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


@pytest.fixture(scope="session")
def s3_buckets() -> None:
    from app.config import get_settings
    from app.core.storage import ensure_buckets, make_client

    s = get_settings()
    admin = make_client(s, access_key=DEV_KEYS["admin"][0], secret_key=DEV_KEYS["admin"][1])
    try:
        ensure_buckets(admin, [s.s3_library_bucket, s.s3_work_bucket])
    except Exception as exc:  # pragma: no cover - environment problem, make it obvious
        pytest.fail(f"no S3 server at {S3_ENDPOINT} ({exc}); start infra/compose.yaml or set STUDIO_TEST_S3_ENDPOINT")


@pytest.fixture
def storage_as(s3_buckets):
    """storage_as("worker", "work") -> a Storage for that identity and bucket."""
    from app.config import get_settings
    from app.core.storage import Storage

    def make(identity: str, bucket: str) -> Storage:
        key, secret = DEV_KEYS[identity]
        return Storage.for_bucket(bucket, get_settings(), access_key=key, secret_key=secret)

    return make


@pytest.fixture
def key_prefix() -> str:
    from studio_contracts.ids import uuid7

    return f"_test/{uuid7()}"


class RecordingDispatcher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, float]] = []

    def send(self, job_id, queue: str, countdown_s: float = 0) -> None:
        self.sent.append((str(job_id), queue, countdown_s))


@pytest.fixture
def dispatcher() -> Iterator[RecordingDispatcher]:
    from app.jobs.queue import set_dispatcher

    d = RecordingDispatcher()
    set_dispatcher(d)
    yield d
    set_dispatcher(None)


@pytest.fixture
def db(migrated):
    """A sessionmaker whose sessions really commit (for code that manages its own
    transactions, such as API routes and the worker supervisor)."""
    from app.core.database import get_sessionmaker

    return get_sessionmaker()


@pytest.fixture
def anon(migrated, s3_buckets, dispatcher):
    """An API client without credentials."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def api_as(migrated, s3_buckets, dispatcher):
    """api_as("creator") -> an API client whose requests carry a token with that role."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    clients = []

    def make(*roles: str, sub: str | None = None):
        c = TestClient(create_app())
        c.headers["Authorization"] = f"Bearer {make_token(sub or 'user-' + '-'.join(roles or ('none',)), list(roles))}"
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.close()


@pytest.fixture
def api(api_as):
    """An API client authenticated as an administrator."""
    return api_as("admin")
