from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.readiness import run_checks

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness: the process is up")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness: every dependency answers")
def ready() -> JSONResponse:
    checks = run_checks()
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "unavailable", "checks": checks}, status_code=200 if ok else 503)
