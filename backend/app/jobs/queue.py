"""Celery configuration and the dispatcher the API uses to hand job ids to workers.

Only the job id travels through Redis. Settings follow architecture 11.2: late acks, one
task prefetched per worker process, no result backend, and a visibility timeout above the
longest job so Redis does not redeliver a running job. A redelivery that happens anyway is
harmless because ``claim`` is compare-and-set.
"""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from typing import Protocol

from celery import Celery

from app.config import get_settings

log = logging.getLogger("studio.jobs")
RUN_JOB_TASK = "studio.run_job"


@lru_cache
def celery_app() -> Celery:
    s = get_settings()
    app = Celery("studio", broker=s.redis_url)
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_ignore_result=True,
        result_backend=None,
        broker_transport_options={"visibility_timeout": s.job_visibility_timeout_s},
        broker_connection_retry_on_startup=True,
        task_default_queue="blender",
        worker_hijack_root_logger=False,
    )
    return app


class Dispatcher(Protocol):
    def send(self, job_id: uuid.UUID, queue: str, countdown_s: float = 0) -> None: ...


class CeleryDispatcher:
    def send(self, job_id: uuid.UUID, queue: str, countdown_s: float = 0) -> None:
        celery_app().send_task(RUN_JOB_TASK, args=[str(job_id)], queue=queue, countdown=countdown_s or None)


_dispatcher: Dispatcher | None = None


def get_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = CeleryDispatcher()
    return _dispatcher


def set_dispatcher(d: Dispatcher | None) -> None:
    """Tests swap in a recording dispatcher."""
    global _dispatcher
    _dispatcher = d
