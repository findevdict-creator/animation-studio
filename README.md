# Animation Studio

An AI-assisted 3D cartoon production platform: reusable, immutably versioned characters,
outfits and assets built by deterministic Blender code from AI-interpreted specifications,
then assembled into scenes and episodes.

The architecture is in [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md);
the phase demonstrations are in [docs/demos/](docs/demos/) ([Phase 1](docs/demos/phase-1.md),
[Phase 2](docs/demos/phase-2.md), [Phase 3](docs/demos/phase-3.md)).

## Branches

The platform is built as a stack of numbered branches starting from `main`, each on top of
the one before, so each step can be reviewed and merged on its own.

| Branch | What it adds |
| --- | --- |
| `v2-01-architecture` | The architecture document (SRS section 34) |
| `v2-02-backend` | Repository layout, `contracts` package, FastAPI app, Alembic baseline with immutability guards and seeded roles |
| `v2-03-storage` | S3 storage layer: library and work buckets, write-once `put_immutable` with server-side conditional writes, SHA-256 on every object, presigned URLs, per-identity access |
| `v2-04-jobs` | Jobs in PostgreSQL with Celery on Redis as transport, the Blender engine package and entry point, the worker supervisor (one Blender process per job, progress, cancel, timeout, crash retry, output upload), `blender_smoke` job, job and asset metadata API |
| `v2-05-auth` | OIDC bearer tokens (JWKS, issuer, audience, expiry), users created on first login with roles synced from the token, permission checks from the database, audit entries, `/me`, dev Keycloak realm |
| `v2-06-docker` | API and Blender worker images, a compose stack (PostgreSQL, Redis, SeaweedFS, Keycloak, migrations, API, worker, beat) and the Phase 1 acceptance script |
| `v2-07-spikes` | Blender spikes kept as regression tests (link and override assembly, headless Rigify), spike report, Phase 1 demonstration record, architecture updated with the results |
| `v2-08-character-spec` | Phase 2: `CharacterSpecification`, the `family_cartoon@1.0` style profile, the `cartoon_humanoid@1.0` rig profile, spec validation and resolution, `/characters/spec/validate` and profile endpoints |
| `v2-09-ai-interpret` | LLM providers ported from `studio/llm` to `app/ai/providers`, the `interpret_character` AI job (closed proposal schema, validation, repair, `ai_generations` log), the `ai` worker queue |
| `v2-10-character-geometry` | The procedural stand-in template `TPL-HUMANOID-STANDIN@0.1`: skeleton landmarks, skin-modifier body, head with eyes, eyelids, brows and mouth driven by face states, the 43 facial shape keys of the rig profile, four hair styles, style-profile materials |
| `v2-11-character-rig` | Rigify rig fitted to the template and generated headless, eye bones tracking `eye_target`, `face_ctrl` properties driving all facial shape keys with simple-expression drivers, deterministic skin weights and mask groups, the character validator (structure and motion checks) |
| `v2-12-character-previews` | The `generate_character` Blender job (build, rig, save, reopen, validate, turntable MP4, labelled expression sheet, GLB), the CHAR draft outcome, the Phase 2 acceptance script and demonstration record |
| `v2-13-character-library` | Character identity and version tables (migration 0005) with database guards that freeze a build in review and a published version for good, the library service (create, drafts based on any version, spec edits with `If-Match`, generate linked to its draft, submit gated on a current validated build, review approve/reject, deprecate, discard) and the `/characters` API with gallery search and presigned file links |
| `v2-14-character-publisher` | The `publish_character` job on its own `publish` queue and `worker-publisher` service (publisher storage identity): SHA-256 check of every draft file, fresh-process Blender re-validation of the exact `character.blend`, version number claimed by a write-once marker, write-once library copy with `manifest.json`, one-transaction status flip; major bump forced on template/rig/fit changes; linking two published versions into one scene with library overrides (`load_check`); the Phase 3 acceptance script |
| `v2-15-library-web` | The web app (`web/`, Vite + React + TypeScript + TanStack Query + React Three Fiber, Keycloak login with PKCE): character gallery with search and filters, character page with the GLB viewer (orbit, front/side/back, expression, viseme and eye morph sliders), version history, draft spec form generated from the style profile, generate/submit/review/publish/deprecate/duplicate actions by permission; the Phase 3 demonstration record |
| `v2-16-outfit-spec` | Phase 4: `OutfitSpecification`, the procedural garment catalog (`GARM-*@0.1`), the style's clothing conventions (colours, fabrics, clearances, fit thresholds, pose suite), outfit validation and resolution, `/outfits/spec/validate`, `/garments`, the `interpret_outfit` AI job |
| `v2-17-outfit-geometry` | `cartoon_engine.outfit`: garments cut from the reference body of the outfit's fit profile and saved alone in `outfit.blend`; fitting onto any character of the rig profile through skeleton space, rigid pieces on attachment bones, a per-vertex body mask; the fit check (clipping both ways, stretch against the skin, lift, structure) over a pose suite; the `generate_outfit` and `fit_outfit` jobs with thumbnail, pose sheet and try-on GLB |
| `v2-18-outfit-library` | Outfit identity, versions and the compatibility registry (migration 0006, with guards), the outfit library service and `/outfits` API (drafts, generate, fit checks on published character versions, fit approval and revocation, submit gated on a passing fit, review, publish, deprecate, wardrobe per character version), fit-job inputs staged by the worker from the exact files, the publisher generalised to `publish_outfit` (fresh-process re-check of the exact `outfit.blend`) |
| `v2-19-outfit-web` | Web outfit library: outfit gallery with slot/scope/status filters, new-outfit form (one catalog garment per slot, parameter sliders, fabric and colour presets, scope and fit profiles), outfit page with the try-on GLB viewer and pose sheet on the reference body or any checked character, the fit report as a garment-by-pose grid, the compatibility table (fit-check on a published character, approve, revoke), version actions; a wardrobe on the character page that puts any checked outfit on the character in the viewer |

The earlier story-to-package pipeline lives on branches `01-schemas` to `14-build-queue`
and the two GUI/LLM branches after them (PRs #1 to #16). It is not merged into `main`; the
new phases port pieces of it when they need them.

## Layout

```
contracts/        shared Pydantic contracts (IDs, versions, job envelopes); no other deps
blender_engine/   cartoon_engine: runs only inside Blender (or the bpy wheel); entrypoint.py runs one job
workers/          Celery tasks, the supervisor that runs Blender jobs, the AI runner and the publisher
web/              the browser app (Vite, React, TypeScript)
infra/            dev service configuration (SeaweedFS identities, Keycloak realm)
backend/          FastAPI app (app/), Alembic migrations, shared test fixtures (studio_testing), tests
docs/architecture/
```

## Running the stack

```
docker compose -f infra/compose.yaml up -d --build
python scripts/phase1_acceptance.py        # logs in through Keycloak, runs a Blender job, checks the file
python scripts/phase3_acceptance.py        # character library: publish 1.0, refuse overwrites, publish 2.0, load both
```

API docs at http://localhost:8000/api/docs, Keycloak at http://localhost:8080 (admin/admin).
The web app runs from source with Node 22: `cd web && npm ci && npm run dev`, then
http://localhost:5173 (it proxies `/api` to the API).
Dev users `admin`, `creator`, `director`, `reviewer`, each with password `dev-<name>`. The
worker image installs Blender as the `bpy` 5.2.3 wheel by default; build with
`--build-arg BLENDER_SOURCE=binary --build-arg BLENDER_SHA256=<sha>` for the official
binary (needed for GPU rendering later).

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
