"""Phase 1 acceptance check (SRS section 30), against a running stack.

    docker compose -f infra/compose.yaml up -d
    python scripts/phase1_acceptance.py

Standard library only. Logs in through Keycloak with the dev users, submits a Blender job
through the API, waits for the worker, then fetches the asset metadata and the file itself
and checks its SHA-256. Exits non-zero on the first failed criterion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "http://localhost:8000/api/v1"
KEYCLOAK = "http://localhost:8080/realms/studio/protocol/openid-connect/token"


def call(method: str, url: str, token: str | None = None, body: dict | None = None, form: dict | None = None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    if form is not None:
        data, headers["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)


def login(user: str) -> str:
    status, body = call("POST", KEYCLOAK, form={"grant_type": "password", "client_id": "studio-dev-cli",
                                                "username": user, "password": f"dev-{user}", "scope": "openid"})
    if status != 200:
        raise SystemExit(f"login {user}: {status} {body}")
    return body["access_token"]


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        sys.exit(1)


def main() -> int:
    global API, KEYCLOAK
    p = argparse.ArgumentParser()
    p.add_argument("--api", default=API)
    p.add_argument("--token-url", default=KEYCLOAK)
    a = p.parse_args()
    API, KEYCLOAK = a.api.rstrip("/"), a.token_url

    status, body = call("GET", f"{API}/health")
    check("FastAPI starts successfully", status == 200 and body == {"status": "ok"}, f"GET /health -> {status}")
    status, body = call("GET", f"{API}/ready")
    check("database, storage and broker are reachable", status == 200, json.dumps(body.get("checks")))
    check("database migrations work (the API reads the migrated schema)", body["checks"]["database"] == "ok")

    status, body = call("GET", f"{API}/jobs")
    check("requests without a token are refused", status == 401, f"{status} {body.get('code')}")
    creator = login("creator")
    status, me = call("GET", f"{API}/me", creator)
    check("Keycloak login works and roles reach the API", status == 200 and me["roles"] == ["creator"],
          f"{me.get('name')} {me.get('roles')}")
    status, body = call("POST", f"{API}/jobs", login("reviewer"), {"type": "blender_smoke"})
    check("a reviewer cannot submit jobs", status == 403, body.get("required", ""))

    status, job = call("POST", f"{API}/jobs", creator, {"type": "blender_smoke", "params": {"object_name": "acceptance_cube", "size_m": 2}})
    check("job accepted", status == 202, f"job {job.get('id')}")
    started = time.monotonic()
    while job["status"] not in ("succeeded", "failed", "timed_out", "cancelled") and time.monotonic() - started < 180:
        time.sleep(1)
        _, job = call("GET", f"{API}/jobs/{job['id']}", creator)
    check("a background job can execute Blender", job["status"] == "succeeded",
          f"{job['status']} on {job.get('worker_host')} with Blender {job.get('blender_version')} in "
          f"{time.monotonic() - started:.1f} s; result {json.dumps(job.get('result', {}).get('data'))}")

    status, version = call("GET", f"{API}/assets/versions/{job['result']['asset_version_id']}", creator)
    check("the API can retrieve the resulting asset metadata", status == 200 and len(version["files"]) == 1,
          f"{version['display_code']} {version['status']}")
    f = version["files"][0]
    print(f"      file {f['storage_key']}  {f['size_bytes']} bytes  sha256 {f['sha256']}  {f['mime_type']}")
    status, link = call("GET", f"{API}/assets/files/{f['id']}/download", creator)
    with urllib.request.urlopen(link["url"], timeout=30) as r:
        data = r.read()
    digest = hashlib.sha256(data).hexdigest()
    check("Blender saved the file to object storage, intact", digest == f["sha256"] and data[:7] == b"BLENDER",
          f"downloaded {len(data)} bytes through a presigned URL, sha256 matches, header {data[:12]!r}")
    status, log = call("GET", f"{API}/jobs/{job['id']}/log", creator)
    check("the Blender log was captured", status == 200)
    print("\nAll Phase 1 acceptance criteria passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
