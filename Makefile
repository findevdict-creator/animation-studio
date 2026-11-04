# Developer entry points. Python 3.13; a venv is assumed to be active.
PY ?= python

.PHONY: install install-bpy test migrate api worker

install:
	$(PY) -m pip install -e contracts -e "backend[dev]" -e blender_engine -e workers

# Blender as a Python module, for engine and worker tests outside a Blender install.
install-bpy:
	$(PY) -m pip install bpy==5.2.3

test:
	$(PY) -m pytest -q backend/tests blender_engine/tests workers/tests

migrate:
	cd backend && alembic upgrade head

api:
	cd backend && uvicorn app.main:app --reload

worker:
	celery -A studio_workers.tasks worker -Q blender --concurrency 1
