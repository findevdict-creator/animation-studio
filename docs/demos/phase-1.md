# Phase 1 demonstration: technical foundation

SRS section 31 asks for a demonstration that the acceptance criteria work, not only code.
This page records the run made on 2026-11-04 on the `v2-07-spikes` branch.

## Acceptance criteria (SRS section 30, Phase 1)

The criteria were checked against the full compose stack (`infra/compose.yaml`: PostgreSQL
16, Redis 7, SeaweedFS 4.50, Keycloak 26.4, the migration and storage-init one-shots, the API,
the Blender worker and beat), with real Keycloak logins:

```
$ docker compose -f infra/compose.yaml up -d
$ python scripts/phase1_acceptance.py
PASS  FastAPI starts successfully  (GET /health -> 200)
PASS  database, storage and broker are reachable  ({"database": "ok", "storage": "ok", "broker": "ok"})
PASS  database migrations work (the API reads the migrated schema)
PASS  requests without a token are refused  (401 unauthorized)
PASS  Keycloak login works and roles reach the API  (Creator Dev ['creator'])
PASS  a reviewer cannot submit jobs  (job.submit)
PASS  job accepted  (job 01a1a6ae-1caa-730e-b00b-fe6064415e1b)
PASS  a background job can execute Blender  (succeeded on 089434dd0a27 with Blender 5.2.3 LTS in 3.1 s; result {"object": "acceptance_cube", "vertices": 8, "dimensions_m": [2.0, 2.0, 2.0]})
PASS  the API can retrieve the resulting asset metadata  (TEST-001 draft)
      file jobs/01a1a6ae-1caa-730e-b00b-fe6064415e1b/attempt-1/out/smoke.blend  492581 bytes  sha256 164fa3f89731fdf903f3962e6b3838d593283eaa172364007085069ab662e0bb  application/x-blender
PASS  Blender saved the file to object storage, intact  (downloaded 492581 bytes through a presigned URL, sha256 matches, header b'BLENDER17-01')
PASS  the Blender log was captured

All Phase 1 acceptance criteria passed.
```

| SRS criterion | Shown by |
| --- | --- |
| FastAPI starts successfully | `GET /health`; the compose healthcheck |
| Database migrations work | the `migrate` one-shot exits 0; `test_migrations.py` runs upgrade, downgrade, upgrade and checks that the models match the migrations |
| A background job can execute Blender | the job ran in the worker container in a fresh Blender 5.2.3 process |
| Blender can save a file to object storage | the `.blend` came back through a presigned URL with a matching SHA-256 and the `BLENDER` header |
| The API can retrieve the resulting asset metadata | `GET /assets/versions/{id}` and `GET /assets/files/{id}` |

## Automated tests

```
$ python -m pytest backend/tests blender_engine/tests workers/tests
75 passed in 29.58s
```

The tests run against real services: PostgreSQL, Redis, SeaweedFS and the `bpy` 5.2.3 wheel.
Nothing is mocked except the failure modes of the Blender process, which use a fake engine
script.

| Area | What is covered |
| --- | --- |
| Contracts | UUIDv7, display codes, `major.minor` ordering and bumps, strict job envelopes |
| Database guards | published and deprecated versions frozen; illegal status changes refused; files of a published version cannot be added, changed, deleted or moved in; audit log append-only; seeded grants match the code |
| Storage | hash verification and tamper detection; write-once single and multipart uploads, refused by the server even in a race; only the publisher can write the library bucket, and the publisher cannot write the work bucket; presigned GET and PUT; unsafe keys |
| Jobs API | validation, idempotency keys (same request returns the same job, a different request returns 409), cancel, retry, events, paging, asset metadata and download |
| Worker | real Blender job to storage; duplicate delivery is a no-op; handled failures are not retried; crashes are retried with backoff and then fail; cancel kills the process group; timeouts; outputs cannot escape the workspace; the Blender process sees no credentials |
| End to end | HTTP to API to Redis to a Celery worker to Blender to S3, and back through the API |
| Auth | missing, expired, wrong-issuer, wrong-audience, wrong-key, unknown-kid and unsigned tokens refused; first login creates the user; role changes in the token are reflected and audited; permissions by role |
| Spikes | linking and overrides (with a workspace move), headless Rigify |

## Not done in Phase 1, and why

- **CI pipeline.** The repository has no GitHub Actions workflow yet. The tests need
  PostgreSQL, Redis, SeaweedFS and the 400 MB `bpy` wheel, so the workflow is its own small
  piece of work. It should come first in Phase 2.
- **Live job updates (SSE)** from architecture 11.2. Clients poll `GET /jobs/{id}` and
  `GET /jobs/{id}/events?after=` for now. SSE comes with the first frontend screen.
- **Network isolation for the Blender process.** The worker container runs read-only,
  without capabilities and under a memory limit, and the Blender process gets no
  credentials. It still shares the container's network, because the worker needs the
  database, Redis and S3. A per-job sandbox without network (a separate container or
  nsjail) is needed before untrusted uploads or LLM-written scripts run (risk R14).
- **Official Blender binary in the image.** The worker image is built and tested with the
  `bpy` wheel. The binary path (`BLENDER_SOURCE=binary`) is written but was not built here,
  because download.blender.org is not reachable from this build environment. GPU render
  workers will need it.
- **Sandbox build details.** Debian package mirrors and Docker Hub (rate limited) were
  blocked in this environment, so the images were built with `PYTHON_BASE` pointing at an
  equivalent Ubuntu 24.04 base carrying the same Python 3.13 build. The committed
  Dockerfiles default to `python:3.13-slim-bookworm`.
