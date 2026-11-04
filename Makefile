# Developer entry points. Python 3.13; a venv is assumed to be active.
PY ?= python

.PHONY: install test test-backend migrate api

install:
	$(PY) -m pip install -e contracts -e "backend[dev]"

test: test-backend

test-backend:
	cd backend && $(PY) -m pytest -q

migrate:
	cd backend && alembic upgrade head

api:
	cd backend && uvicorn app.main:app --reload
