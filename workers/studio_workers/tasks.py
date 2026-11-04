"""Celery tasks. The worker process imports this module::

    celery -A studio_workers.tasks worker -Q blender --concurrency 1
    celery -A studio_workers.tasks beat        # runs the outbox sweeper
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from app.config import get_settings
from app.core.database import get_sessionmaker
from app.core.logging import configure_logging
from app.jobs import service
from app.jobs.queue import RUN_JOB_TASK, celery_app, get_dispatcher
from app.models.jobs import Job
from studio_workers.supervisor import Supervisor

configure_logging(get_settings().log_level, get_settings().log_json)
log = logging.getLogger("studio.worker")
app = celery_app()
SWEEP_TASK = "studio.sweep_outbox"
app.conf.beat_schedule = {"sweep-outbox": {"task": SWEEP_TASK, "schedule": 60.0}}


@app.task(name=RUN_JOB_TASK, acks_late=True)
def run_job(job_id: str) -> str:
    outcome = Supervisor().run(uuid.UUID(job_id))
    return outcome.status.value if outcome.status else "not_claimed"


@app.task(name=SWEEP_TASK)
def sweep_outbox() -> int:
    """Re-send queued jobs that never reached the broker (or were lost in it)."""
    sessions = get_sessionmaker()
    with sessions.begin() as s:
        stale = service.stale_queued(s, timedelta(minutes=5))
        jobs = {j.id: j.queue for j in s.query(Job).filter(Job.id.in_(stale))} if stale else {}
    for job_id, queue in jobs.items():
        get_dispatcher().send(job_id, queue)
        with sessions.begin() as s:
            service.mark_enqueued(s, job_id)
    if jobs:
        log.info("outbox sweeper re-sent %d jobs", len(jobs))
    return len(jobs)
