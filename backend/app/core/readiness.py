"""Readiness checks. Each dependency (database, Redis, object storage) registers one."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import text

from app.core.database import get_engine

Check = Callable[[], None]
CHECKS: dict[str, Check] = {}


def register(name: str) -> Callable[[Check], Check]:
    def deco(fn: Check) -> Check:
        CHECKS[name] = fn
        return fn

    return deco


@register("database")
def _database() -> None:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def run_checks() -> dict[str, str]:
    results: dict[str, str] = {}
    for name, fn in CHECKS.items():
        try:
            fn()
            results[name] = "ok"
        except Exception as exc:  # report, never raise: this is a status endpoint
            results[name] = f"error: {type(exc).__name__}: {exc}"[:300]
    return results


@register("storage")
def _storage() -> None:
    from app.core.storage import Storage

    Storage.for_bucket("work").ping()
    Storage.for_bucket("library").ping()


@register("broker")
def _broker() -> None:
    import redis

    from app.config import get_settings

    redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=2).ping()
