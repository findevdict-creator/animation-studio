"""generate_character through the real supervisor: a fresh Blender process (bpy 5.2 wheel)
builds, rigs, saves, reopens and validates a character, renders previews and exports a GLB;
the worker uploads every file with its SHA-256 and records a CHAR draft."""

import json

import pytest

from app.config import get_settings
from app.core.storage import sha256_file
from app.jobs import service
from app.assets import characters
from app.models.assets import AssetVersion
from app.models.characters import Character, CharacterVersion
from app.models.jobs import Job
from studio_contracts.jobs import JobStatus
from studio_workers.supervisor import Supervisor

SPEC = {
    "name": "Ali",
    "body": {"age_appearance": "child", "height_m": 1.28, "proportions": {"leg_length": 0.1}},
    "face": {"eye_size": 0.7, "eye_color": "brown", "sliders": {"cheek_fullness": 0.3}},
    "hair": {"style": "short", "color": "black"},
    "materials": {"skin": {"preset": "warm_medium"}},
}
SMALL = {"resolution": 128, "samples": 4, "turntable_frames": 4, "sheet_tile": 96}


def test_invalid_spec_is_refused_at_submit(api_as):
    r = api_as("creator").post("/api/v1/jobs", json={"type": "generate_character",
                                                      "params": {"spec": {**SPEC, "hair": {"style": "mohawk"}}}})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "invalid_job_params" and "hair.style" in body["errors"][0]["msg"]
    r = api_as("reviewer").post("/api/v1/jobs", json={"type": "generate_character", "params": {"spec": SPEC}})
    assert r.status_code == 403


def test_generate_character_end_to_end(db, storage_as, dispatcher, tmp_path):
    pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")
    settings = get_settings().model_copy(update={"work_root": str(tmp_path / "work"), "blender_mode": "wheel",
                                                 "job_timeouts_s": {"generate_character": 600}})
    with db.begin() as s:
        job, _ = service.submit(s, "generate_character", {"spec": SPEC, "previews": SMALL})
        job_id = job.id
    work = storage_as("worker", "work")
    outcome = Supervisor(settings, work_storage=work, dispatcher=dispatcher).run(job_id)
    with db() as s:
        job = s.get(Job, job_id)
        assert outcome.status == JobStatus.succeeded, (job.error, job.log_key)
        data = job.result["data"]
        assert data["validation"]["ok"] and data["fit_profile"] == "child_average" and data["height_m"] == 1.28
        assert data["glb"]["head_morph_targets"] == 43 and data["turntable_frames"] == 4
        version = s.get(AssetVersion, job.result["asset_version_id"])
        assert version.kind == "character" and version.display_code.startswith("CHAR-") and version.status == "draft"
        # without a draft the job creates the character identity and its first draft
        character = s.get(Character, job.result["character_id"])
        draft = s.get(CharacterVersion, version.id)
        assert job.result["attached"] and character.code == version.display_code and character.name == "Ali"
        assert draft.built_digest == draft.spec_digest == data["resolved_digest"]
        assert draft.validation_report["ok"] and draft.validation_report["blend_sha256"] == data["blend_sha256"]
        assert characters.readiness(draft) == []
        roles = sorted(f.role for f in version.files)
        assert roles == ["blend", "glb", "preview", "preview", "report", "spec", "thumbnail", "turntable"]
        files = {f.storage_key.rsplit("/", 1)[-1]: f for f in version.files}
        assert files["character.blend"].sha256 == data["blend_sha256"]

    for name, f in files.items():  # every object is in storage with the recorded checksum
        local = tmp_path / name
        work.get_file(f.storage_key, local)
        assert sha256_file(local) == f.sha256
    report = json.loads((tmp_path / "validation.json").read_text())
    assert report["ok"] and report["blender_version"].startswith("5.2")
    spec = json.loads((tmp_path / "character.json").read_text())
    assert spec["specification"]["name"] == "Ali" and spec["resolved_digest"] == data["resolved_digest"]
    assert (tmp_path / "turntable.mp4").read_bytes()[4:8] == b"ftyp"
    assert (tmp_path / "preview.glb").read_bytes()[:4] == b"glTF"
