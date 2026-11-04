"""Job state in PostgreSQL (architecture 11.2). Celery only carries job ids; every state
change goes through these functions, and the ones a worker calls are compare-and-set so a
duplicate delivery or a late cancel cannot corrupt a job."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models.jobs import Job, JobEvent
from studio_contracts.jobs import JOB_PARAMS, JOB_PERMISSIONS, JOB_QUEUES, TERMINAL_STATUSES, JobStatus, JobType


def _now() -> datetime:
    return datetime.now(timezone.utc)


def event(session: Session, job_id: uuid.UUID, kind: str, message: str = "", data: dict[str, Any] | None = None) -> None:
    session.add(JobEvent(job_id=job_id, kind=kind, message=message[:4000], data=data))


def extra_permissions(job_type: str) -> tuple[str, ...]:
    try:
        return JOB_PERMISSIONS.get(JobType(job_type), ())
    except ValueError:
        return ()


def submit(
    session: Session,
    job_type: str,
    params: dict[str, Any],
    idempotency_key: str | None = None,
    requested_by: uuid.UUID | None = None,
    max_attempts: int = 3,
) -> tuple[Job, bool]:
    """Validate and insert a job. Returns (job, created). The same idempotency key with the
    same request returns the first job; with a different request it is a conflict."""
    try:
        jtype = JobType(job_type)
    except ValueError:
        raise ApiError(422, "unknown_job_type", f"no job type {job_type!r}") from None
    try:
        clean = JOB_PARAMS[jtype].model_validate(params).model_dump(mode="json")
    except ValidationError as exc:
        raise ApiError(422, "invalid_job_params", "job params failed validation", errors=exc.errors(include_url=False, include_context=False)) from None

    if idempotency_key:
        existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.type != jtype.value or existing.params != clean:
                raise ApiError(409, "idempotency_conflict", "this Idempotency-Key was used for a different request")
            return existing, False

    job = Job(
        type=jtype.value, queue=JOB_QUEUES[jtype].value, status=JobStatus.queued.value, params=clean,
        idempotency_key=idempotency_key, requested_by=requested_by, max_attempts=max_attempts,
        progress=0.0, progress_message="", attempt=0,
    )
    session.add(job)
    session.flush()
    event(session, job.id, "status", "queued")
    return job, True


def mark_enqueued(session: Session, job_id: uuid.UUID) -> None:
    session.execute(update(Job).where(Job.id == job_id).values(enqueued_at=_now()))


def stale_queued(session: Session, older_than: timedelta) -> list[uuid.UUID]:
    """Jobs committed but never (or long ago) handed to the broker: the outbox sweeper
    re-sends these, so a crash between commit and send never loses a job."""
    cutoff = _now() - older_than
    rows = session.scalars(
        select(Job.id).where(Job.status == JobStatus.queued.value, or_(Job.enqueued_at.is_(None), Job.enqueued_at < cutoff))
    )
    return list(rows)


def claim(session: Session, job_id: uuid.UUID, worker_host: str) -> Job | None:
    """queued -> running, once. Returns None when another worker has it or it was cancelled."""
    row = session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.queued.value)
        .values(status=JobStatus.running.value, attempt=Job.attempt + 1, started_at=_now(),
                heartbeat_at=_now(), worker_host=worker_host, error=None)
        .returning(Job.id)
    ).first()
    if row is None:
        return None
    job = session.get(Job, job_id, populate_existing=True)
    event(session, job_id, "status", "running", {"attempt": job.attempt, "worker": worker_host})
    return job


def report_progress(session: Session, job_id: uuid.UUID, progress: float, message: str = "") -> None:
    session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_([JobStatus.running.value, JobStatus.cancel_requested.value]))
        .values(progress=max(0.0, min(1.0, progress)), progress_message=message[:1000], heartbeat_at=_now())
    )


def cancel_requested(session: Session, job_id: uuid.UUID) -> bool:
    return session.scalar(select(Job.status).where(Job.id == job_id)) == JobStatus.cancel_requested.value


def finish(
    session: Session,
    job_id: uuid.UUID,
    status: JobStatus,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    **extra: Any,
) -> None:
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"{status} is not terminal")
    values: dict[str, Any] = {"status": status.value, "finished_at": _now(), "result": result, "error": error, **extra}
    if status == JobStatus.succeeded:
        values["progress"] = 1.0
    session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_([JobStatus.running.value, JobStatus.cancel_requested.value]))
        .values(**values)
    )
    event(session, job_id, "status", status.value, error)


def requeue_for_retry(session: Session, job_id: uuid.UUID, error: dict[str, Any]) -> bool:
    """running -> queued for another attempt, if attempts remain. Returns whether it did."""
    row = session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.running.value, Job.attempt < Job.max_attempts)
        .values(status=JobStatus.queued.value, error=error, enqueued_at=None, progress=0.0)
        .returning(Job.id)
    ).first()
    if row is not None:
        event(session, job_id, "status", "queued for retry", error)
    return row is not None


def request_cancel(session: Session, job_id: uuid.UUID) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "job_not_found", str(job_id))
    if job.status == JobStatus.queued.value:
        job.status, job.finished_at = JobStatus.cancelled.value, _now()
        event(session, job_id, "status", "cancelled before start")
    elif job.status == JobStatus.running.value:
        job.status = JobStatus.cancel_requested.value
        event(session, job_id, "status", "cancel requested")
    elif job.status != JobStatus.cancel_requested.value:
        raise ApiError(409, "job_not_cancellable", f"job is {job.status}")
    session.flush()
    return job


def retry(session: Session, job_id: uuid.UUID) -> Job:
    """A user retry of a failed, timed-out or cancelled job: a fresh attempt budget."""
    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "job_not_found", str(job_id))
    if job.status not in (JobStatus.failed.value, JobStatus.timed_out.value, JobStatus.cancelled.value):
        raise ApiError(409, "job_not_retryable", f"job is {job.status}")
    job.status = JobStatus.queued.value
    job.max_attempts = job.attempt + job.max_attempts
    job.finished_at = None
    job.enqueued_at = None
    job.progress = 0.0
    event(session, job_id, "status", "queued by user retry")
    session.flush()
    return job
