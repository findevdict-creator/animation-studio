from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.schemas import DownloadOut, JobEventOut, JobOut, JobSubmit, Page
from app.core.auth import Principal, audit, require
from app.core.database import get_session
from app.core.errors import ApiError
from app.core.storage import Storage
from app.jobs import service
from app.jobs.queue import get_dispatcher
from app.models.jobs import Job, JobEvent

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _out(job: Job) -> JobOut:
    out = JobOut.model_validate(job)
    out.has_log = job.log_key is not None
    return out


def _get(session: Session, job_id: uuid.UUID) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "job_not_found", str(job_id))
    return job


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut, summary="Submit a background job")
def submit_job(
    body: JobSubmit,
    response: Response,
    session: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None, max_length=200),
    who: Principal = Depends(require("job.submit")),
) -> JobOut:
    job, created = service.submit(session, body.type, body.params, idempotency_key=idempotency_key,
                                  requested_by=who.user_id)
    if created:
        audit(session, who, "job.submitted", "job", job.id, type=job.type)
    session.commit()  # the row exists before anything can pick the id up (outbox, 11.2)
    if created:
        get_dispatcher().send(job.id, job.queue)
        service.mark_enqueued(session, job.id)
        session.commit()
    else:
        response.status_code = status.HTTP_200_OK
    session.refresh(job)
    return _out(job)


@router.get("", response_model=Page[JobOut], summary="List jobs, newest first")
def list_jobs(
    session: Session = Depends(get_session),
    status_: str | None = Query(default=None, alias="status"),
    type_: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: uuid.UUID | None = None,
    _: Principal = Depends(require("job.read")),
) -> Page[JobOut]:
    q = select(Job).order_by(Job.id.desc()).limit(limit + 1)
    if status_:
        q = q.where(Job.status == status_)
    if type_:
        q = q.where(Job.type == type_)
    if cursor:
        q = q.where(Job.id < cursor)  # UUIDv7 ids sort by creation time
    rows = list(session.scalars(q))
    more = len(rows) > limit
    rows = rows[:limit]
    return Page(items=[_out(j) for j in rows], next_cursor=str(rows[-1].id) if more else None)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, session: Session = Depends(get_session),
            _: Principal = Depends(require("job.read"))) -> JobOut:
    return _out(_get(session, job_id))


@router.get("/{job_id}/events", response_model=list[JobEventOut])
def job_events(job_id: uuid.UUID, session: Session = Depends(get_session), after: int = 0,
               _: Principal = Depends(require("job.read"))) -> list[JobEventOut]:
    _get(session, job_id)
    rows = session.scalars(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > after).order_by(JobEvent.id))
    return [JobEventOut.model_validate(r) for r in rows]


@router.get("/{job_id}/log", response_model=DownloadOut, summary="Presigned URL for the captured process log")
def job_log(job_id: uuid.UUID, session: Session = Depends(get_session),
            _: Principal = Depends(require("job.read"))) -> DownloadOut:
    job = _get(session, job_id)
    if not job.log_key:
        raise ApiError(404, "job_log_missing", "this job has no captured log yet")
    work = Storage.for_bucket("work")
    return DownloadOut(url=work.presign_get(job.log_key), expires_in_s=work.presign_expiry_s)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: uuid.UUID, session: Session = Depends(get_session),
               who: Principal = Depends(require("job.cancel"))) -> JobOut:
    job = service.request_cancel(session, job_id)
    audit(session, who, "job.cancel_requested", "job", job.id)
    session.commit()
    return _out(job)


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: uuid.UUID, session: Session = Depends(get_session),
              who: Principal = Depends(require("job.cancel"))) -> JobOut:
    job = service.retry(session, job_id)
    audit(session, who, "job.retried", "job", job.id)
    session.commit()
    get_dispatcher().send(job.id, job.queue)
    service.mark_enqueued(session, job.id)
    session.commit()
    session.refresh(job)
    return _out(job)
