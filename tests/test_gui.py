"""The GUI service and its HTTP API, driven with the stub agents (no API key, no bpy)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from studio.agents.stubs import FIXTURE_DIR
from studio.gui import GuiError, StudioService, make_server

REPO = Path(__file__).resolve().parent.parent
STORY = (FIXTURE_DIR / "story.txt").read_text()


def _body(name: str, **approval) -> dict:
    return {
        "name": name,
        "agents": "stub",
        "story_text": STORY,
        "config": {
            "target_duration_sec": 30,
            "library_manifest_path": str(FIXTURE_DIR / "manifest.json"),
            "human_approval": approval or {"mode": "none"},
        },
    }


@pytest.fixture
def service(tmp_path):
    svc = StudioService(runs_dir=tmp_path / "runs", library=REPO / "library", agents="stub")
    yield svc
    svc.close()


def test_run_without_gates_finishes(service):
    service.create_run(_body("straight"))
    assert service.wait("straight") == "done"
    detail = service.run_detail("straight")
    assert detail["status"] == "done" and detail["package_status"] == "ready"
    assert all(s["state"] == "done" for s in detail["steps"])
    assert "production_package.json" in detail["files"]
    assert any(f.startswith("exports/") for f in detail["files"])
    assert any("stored as v1" in line for line in detail["log"])
    assert service.list_runs()[0]["title"] == "The Lantern Fox"


def test_gates_revise_edit_approve(service):
    service.create_run(_body("gated", mode="selected", agents=["story_analyst", "director"]))
    assert service.wait("gated") == "awaiting_approval"
    detail = service.run_detail("gated")
    assert detail["awaiting"] == "story_analyst"
    assert detail["review"]["title"] == "The Lantern Fox"

    with pytest.raises(GuiError):  # revise needs notes
        service.answer_gate("gated", "story_analyst", {"decision": "revise"})
    with pytest.raises(GuiError):  # no gate open for this agent
        service.answer_gate("gated", "director", {"decision": "approve"})

    service.answer_gate("gated", "story_analyst", {"decision": "revise", "notes": "older Mia"})
    assert service.wait("gated") == "awaiting_approval"

    edited = dict(detail["review"], logline="Edited in the GUI.")
    service.answer_gate("gated", "story_analyst", {"decision": "edit", "edited_payload": json.dumps(edited)})
    assert service.wait("gated") == "awaiting_approval"
    assert service.run_detail("gated")["awaiting"] == "director"
    assert service.output("gated", "story_analyst")["logline"] == "Edited in the GUI."

    service.answer_gate("gated", "director", {"decision": "approve"})
    assert service.wait("gated") == "done"
    records = service.run_detail("gated")["approvals"]
    gui = [(r["agent"], r["decision"]) for r in records if r["decided_by"] == "gui"]
    assert gui == [("story_analyst", "revise"), ("story_analyst", "edit"), ("director", "approve")]


def test_no_auto_resume_waits_for_resume(tmp_path):
    svc = StudioService(runs_dir=tmp_path / "runs", library=REPO / "library", agents="stub", auto_resume=False)
    try:
        svc.create_run(_body("manual", mode="selected", agents=["story_analyst"]))
        svc.wait("manual")
        assert svc.answer_gate("manual", "story_analyst", {"decision": "approve"})["resumed"] is False
        assert svc.run_detail("manual")["status"] == "awaiting_approval"
        svc.start("manual")
        assert svc.wait("manual") == "done"
    finally:
        svc.close()


def test_failed_run_reports_issues(service):
    body = _body("short")
    body["config"]["target_duration_sec"] = 300  # the fixture is 30 s: the scene builder check fails
    body["config"]["flags"] = {"agent_retries": 0}
    service.create_run(body)
    assert service.wait("short") == "failed"
    detail = service.run_detail("short")
    assert detail["status"] == "failed"
    assert detail["last_error"]["issues"]
    assert any(s["state"] == "failed" for s in detail["steps"])


def test_create_run_validation(service):
    with pytest.raises(GuiError, match="run names"):
        service.create_run(dict(_body("x"), name="../evil"))
    with pytest.raises(GuiError, match="not allowed"):
        service.create_run(dict(_body("x"), agents="other"))
    bad = _body("x")
    bad["config"]["flags"] = {"render_backend": "blender", "layout_td": False}
    with pytest.raises(GuiError) as exc:
        service.create_run(bad)
    assert exc.value.status == 422
    service.create_run(_body("dup"))
    service.wait("dup")
    with pytest.raises(GuiError) as exc:
        service.create_run(_body("dup"))
    assert exc.value.status == 409


def test_queue_and_library(service):
    service.create_run(_body("q"))
    service.wait("q")
    q = service.queue("q")
    assert q["ready"] and q["metrics"]["opened"] == 0
    saved = service.queue("q", save=True)
    assert saved["saved"]
    assert (service.runs_dir / "q" / "assets" / "metrics.json").exists()
    lib = service.library_manifest()
    assert lib["found"] and lib["manifest"]["characters"]


# ------------------------------------------------------------------ HTTP
@pytest.fixture
def http(service):
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json"} if body is not None else {}
        hdrs.update(headers or {})
        req = urllib.request.Request(base + path, data=data, headers=hdrs, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req) as res:
                raw = res.read()
                return res.status, (json.loads(raw) if "json" in res.headers.get("Content-Type", "") else raw.decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    yield call
    server.shutdown()
    server.server_close()


def test_http_flow(http, service):
    status, page = http("/")
    assert status == 200 and "Animation Studio" in page
    assert http("/app.js")[0] == 200
    assert http("/api/info")[1]["default_agents"] == "stub"

    status, run = http("/api/runs", _body("web", mode="selected", agents=["story_analyst"]))
    assert status == 201
    service.wait("web")
    assert http("/api/runs/web")[1]["awaiting"] == "story_analyst"
    assert http("/api/runs/web/gates/story_analyst", {"decision": "approve"})[1]["resumed"]
    service.wait("web")
    assert http("/api/runs")[1][0]["status"] == "done"

    assert http("/api/runs/web/outputs/director")[1]["schema_version"]
    assert http("/api/runs/web/outputs/director?version=1")[0] == 200
    assert http("/api/runs/web/outputs/nobody")[0] == 404
    status, text = http("/api/runs/web/files/exports/screenplay.md")
    assert status == 200 and "Lantern" in text
    assert http("/api/runs/web/queue")[1]["ready"]
    assert http("/api/library")[1]["found"]


def test_http_guards(http, service):
    service.create_run(_body("g"))
    service.wait("g")
    assert http("/api/runs/g/files/..%2f..%2f..%2fetc%2fpasswd")[0] == 403
    assert http("/api/runs/..%2fx")[0] == 400
    assert http("/api/runs", headers={"Host": "evil.example"})[0] == 403
    # A cross-site form post cannot set application/json, so it is refused.
    status, body = http("/api/runs/g/resume", {}, headers={"Content-Type": "text/plain"})
    assert status == 415
