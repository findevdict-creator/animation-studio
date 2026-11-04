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

The earlier story-to-package pipeline lives on branches `01-schemas` to `14-build-queue`
and the two GUI/LLM branches after them (PRs #1 to #16). It is not merged into `main`; the
new phases port pieces of it when they need them.

## Layout

```
contracts/        shared Pydantic contracts (IDs, versions, job envelopes); no other deps
backend/          FastAPI app (app/), Alembic migrations, tests
docs/architecture/
```

## Development

Needs Python 3.13 and PostgreSQL 16 or later.

```
python3.13 -m venv .venv && . .venv/bin/activate
make install
createdb studio && createdb studio_test           # role studio/studio, or set the URLs below
export STUDIO_DATABASE_URL=postgresql+psycopg://studio:studio@localhost:5432/studio
make migrate
make test          # uses STUDIO_TEST_DATABASE_URL (default: .../studio_test)
make api           # http://127.0.0.1:8000/api/docs
```

Settings are environment variables prefixed `STUDIO_` (see `backend/app/config.py`).
