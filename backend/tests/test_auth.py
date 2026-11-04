import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.identity import User
from studio_testing.plugin import make_token


def test_health_is_public_everything_else_needs_a_token(anon):
    assert anon.get("/api/v1/health").status_code == 200
    r = anon.get("/api/v1/jobs")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert r.json()["code"] == "unauthorized"
    assert anon.get("/api/v1/me", headers={"Authorization": "Basic x"}).status_code == 401


@pytest.mark.parametrize(
    "token_kwargs, why",
    [
        ({"expires_in": -120}, "expired"),
        ({"issuer": "http://evil.example/realms/studio"}, "issuer"),
        ({"audience": "some-other-api"}, "audience"),
        ({"key": rsa.generate_private_key(public_exponent=65537, key_size=2048)}, "signature"),
        ({"kid": "unknown"}, "kid"),
    ],
)
def test_bad_tokens_are_rejected(anon, token_kwargs, why):
    r = anon.get("/api/v1/me", headers={"Authorization": f"Bearer {make_token('mallory', **token_kwargs)}"})
    assert r.status_code == 401, why
    assert r.json()["code"] == "invalid_token"


def test_unsigned_token_is_rejected(anon):
    import jwt

    now = int(time.time())
    token = jwt.encode({"sub": "x", "iss": "http://test-idp.local/realms/studio", "aud": "studio-api",
                        "iat": now, "exp": now + 60}, key=None, algorithm="none")
    assert anon.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_first_login_creates_the_user_and_syncs_roles(api_as, db):
    sub = f"new-user-{time.time_ns()}"
    me = api_as("creator", "not-a-studio-role", sub=sub).get("/api/v1/me").json()
    assert me["roles"] == ["creator"]
    assert "job.submit" in me["permissions"] and "asset.publish" not in me["permissions"]
    with db() as s:
        user = s.scalar(select(User).where(User.external_id == sub))
        assert user.email == f"{sub}@studio.test"
        actions = set(s.scalars(select(AuditLog.action).where(AuditLog.actor_id == user.id)))
    assert {"user.created", "user.roles_synced"} <= actions
    # the identity provider removes the role: the next request reflects it
    me = api_as(sub=sub).get("/api/v1/me").json()
    assert me["roles"] == [] and me["permissions"] == []


@pytest.mark.parametrize(
    "role, method, path, allowed",
    [
        ("reviewer", "post", "/api/v1/jobs", False),
        ("reviewer", "get", "/api/v1/jobs", True),
        ("creator", "post", "/api/v1/jobs", True),
        ("director", "post", "/api/v1/jobs", True),
        ("admin", "post", "/api/v1/jobs", True),
    ],
)
def test_permissions_by_role(api_as, role, method, path, allowed):
    client = api_as(role)
    r = client.post(path, json={"type": "blender_smoke"}) if method == "post" else client.get(path)
    if allowed:
        assert r.status_code in (200, 202), r.text
    else:
        assert r.status_code == 403 and r.json()["required"] == "job.submit"


def test_no_roles_means_no_access(api_as):
    r = api_as().get("/api/v1/assets/files/00000000-0000-7000-8000-000000000000")
    assert r.status_code == 403


def test_submitted_jobs_record_who_and_are_audited(api_as, db):
    client = api_as("creator", sub=f"auditor-{time.time_ns()}")
    job = client.post("/api/v1/jobs", json={"type": "blender_smoke"}).json()
    me = client.get("/api/v1/me").json()
    from app.models.jobs import Job

    with db() as s:
        assert str(s.get(Job, job["id"]).requested_by) == me["user_id"]
        logged = s.scalar(select(AuditLog).where(AuditLog.action == "job.submitted",
                                                 AuditLog.target_id == job["id"]))
        assert str(logged.actor_id) == me["user_id"]
