from fastapi.testclient import TestClient

from app.main import create_app


def client():
    return TestClient(create_app())


def test_health():
    r = client().get("/api/v1/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_ready_checks_database_and_storage(migrated, s3_buckets):
    r = client().get("/api/v1/ready")
    assert r.status_code == 200, r.text
    assert r.json()["checks"] == {"database": "ok", "storage": "ok"}


def test_unknown_route_is_problem_json():
    r = client().get("/api/v1/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "not_found"


def test_openapi_document():
    r = client().get("/api/v1/openapi.json")
    assert r.status_code == 200
    assert "/api/v1/health" in r.json()["paths"]
