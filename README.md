# Animation Studio

An AI-assisted 3D cartoon production platform: reusable, immutably versioned characters,
outfits and assets built by deterministic Blender code from AI-interpreted specifications,
then assembled into scenes and episodes.

The architecture is in [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md).

## Branches

The platform is built as a stack of numbered branches starting from `main`, each on top of
the one before, so each step can be reviewed and merged on its own.

| Branch | What it adds |
| --- | --- |
| `v2-01-architecture` | The architecture document (SRS section 34) |
| `v2-02-backend` | Repository layout, `contracts` package, FastAPI app, Alembic baseline with immutability guards and seeded roles |
| `v2-03-storage` | S3 storage layer: library and work buckets, write-once `put_immutable` with server-side conditional writes, SHA-256 on every object, presigned URLs, per-identity access |
| `v2-04-jobs` | Jobs in PostgreSQL with Celery on Redis as transport, the Blender engine package and entry point, the worker supervisor (one Blender process per job, progress, cancel, timeout, crash retry, output upload), `blender_smoke` job, job and asset metadata API |

The earlier story-to-package pipeline lives on branches `01-schemas` to `14-build-queue`
and the two GUI/LLM branches after them (PRs #1 to #16). It is not merged into `main`; the
new phases port pieces of it when they need them.

## Layout

```
contracts/        shared Pydantic contracts (IDs, versions, job envelopes); no other deps
blender_engine/   cartoon_engine: runs only inside Blender (or the bpy wheel); entrypoint.py runs one job
workers/          Celery tasks and the supervisor that runs Blender jobs
infra/            dev service configuration (SeaweedFS identities)
backend/          FastAPI app (app/), Alembic migrations, shared test fixtures (studio_testing), tests
docs/architecture/
```

## Development

Needs Python 3.13, PostgreSQL 16 or later, Redis 7, Blender 5.2 LTS (or `make install-bpy`
for the `bpy` wheel), and an S3-compatible server. The dev server is
SeaweedFS with the identities in `infra/seaweedfs/s3.json` (MinIO no longer publishes
community images):

```
docker run -d --name weed -p 8333:8333 -v $PWD/infra/seaweedfs/s3.json:/etc/seaweedfs/s3.json \
  chrislusf/seaweedfs server -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json
```

```
python3.13 -m venv .venv && . .venv/bin/activate
make install
createdb studio && createdb studio_test           # role studio/studio, or set the URLs below
export STUDIO_DATABASE_URL=postgresql+psycopg://studio:studio@localhost:5432/studio
make migrate
(cd backend && python -m app.cli init-storage --access-key dev-admin --secret-key dev-admin-secret)
make install-bpy   # optional: Blender as a Python module for tests and wheel-mode workers
make test          # uses STUDIO_TEST_DATABASE_URL (default: .../studio_test)
make api           # http://127.0.0.1:8000/api/docs
STUDIO_S3_ACCESS_KEY=dev-worker STUDIO_S3_SECRET_KEY=dev-worker-secret STUDIO_BLENDER_MODE=wheel make worker

curl -X POST localhost:8000/api/v1/jobs -H 'content-type: application/json' \
     -d '{"type": "blender_smoke", "params": {"size_m": 2}}'
curl localhost:8000/api/v1/jobs/<id>                       # status, progress, result.asset_version_id
curl localhost:8000/api/v1/assets/versions/<version id>    # files with sha256, size, storage key
```

Settings are environment variables prefixed `STUDIO_` (see `backend/app/config.py`).
