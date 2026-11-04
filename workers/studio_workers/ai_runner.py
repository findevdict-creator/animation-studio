"""Runs AI jobs (queue ``ai``) in the worker process: no Blender, no subprocess.

claim -> task (provider calls, validation, ai_generations row) -> cancel check -> finish.
Permanent failures (a refusal, a missing key, a spec that fails validation after repair) fail
the job at once; anything else (network, rate limit, provider 5xx) is retried with backoff.
"""

from __future__ import annotations

import logging
import socket
import uuid
from typing import Any

from app.ai.providers import LLMConfigError, LLMSettings
from app.ai.tasks import AI_TASKS
from app.ai.tasks.interpret_character import AIRefused
from app.config import Settings, get_settings
from app.core.database import get_sessionmaker
from app.core.storage import Storage
from app.jobs import service
from app.jobs.queue import Dispatcher, get_dispatcher
from studio_contracts.jobs import JOB_QUEUES, RETRY_SAFE, JobStatus, JobType
from studio_workers.supervisor import RunOutcome

log = logging.getLogger("studio.worker.ai")
PERMANENT = (AIRefused, LLMConfigError, ValueError)


class AIRunner:
    def __init__(self, settings: Settings | None = None, llm: LLMSettings | None = None,
                 work_storage: Storage | None = None, dispatcher: Dispatcher | None = None):
        self.settings = settings or get_settings()
        self.llm = llm or LLMSettings.from_app_settings(self.settings)
        self._storage = work_storage
        self._dispatcher = dispatcher
        self.sessions = get_sessionmaker()
        self.host = self.settings.worker_host or socket.gethostname()

    @property
    def storage(self) -> Storage:
        if self._storage is None:
            self._storage = Storage.for_bucket("work")
        return self._storage

    def run(self, job_id: uuid.UUID) -> RunOutcome:
        with self.sessions.begin() as s:
            job = service.claim(s, job_id, self.host)
            if job is None:
                return RunOutcome(None)
            jtype, params, attempt = JobType(job.type), dict(job.params), job.attempt
            service.report_progress(s, job_id, 0.05, "asking the model")
        task = AI_TASKS[jtype.value]
        try:
            data: dict[str, Any] = task(self.sessions, job_id, params, self.llm, self.storage,
                                        semantic_repairs=self.settings.ai_semantic_repairs)
        except PERMANENT as exc:
            return self._finish(job_id, JobStatus.failed, error={"message": str(exc)[:2000],
                                                                 "kind": type(exc).__name__})
        except Exception as exc:  # transient: retry if attempts remain
            log.warning("job %s attempt %d: %s: %s", job_id, attempt, type(exc).__name__, exc)
            error = {"message": f"{type(exc).__name__}: {exc}"[:2000], "kind": "transient"}
            with self.sessions.begin() as s:
                retried = RETRY_SAFE.get(jtype, False) and service.requeue_for_retry(s, job_id, error)
                if not retried:
                    service.finish(s, job_id, JobStatus.failed, error=error)
            if retried:
                (self._dispatcher or get_dispatcher()).send(job_id, JOB_QUEUES[jtype].value,
                                                            countdown_s=5 * 2 ** (attempt - 1))
                return RunOutcome(JobStatus.queued, retried=True)
            return RunOutcome(JobStatus.failed)
        with self.sessions() as s:
            cancelled = service.cancel_requested(s, job_id)
        if cancelled:
            return self._finish(job_id, JobStatus.cancelled, error={"message": "cancelled by request"})
        return self._finish(job_id, JobStatus.succeeded, result={"data": data})

    def _finish(self, job_id: uuid.UUID, status: JobStatus, **kw: Any) -> RunOutcome:
        with self.sessions.begin() as s:
            service.finish(s, job_id, status, **kw)
        return RunOutcome(status)
