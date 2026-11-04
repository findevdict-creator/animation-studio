"""The generate_character outcome on an existing draft, without Blender: the worker hands it
the uploaded rows and the job's result data, as the supervisor does after a real build."""

import uuid

from app.models.characters import CharacterVersion
from studio_workers.outcomes import OUTCOMES

SPEC = {"body": {"age_appearance": "child", "height_m": 1.28}}


def _uploaded(job_id, sha="e" * 64):
    return [{"role": role, "storage_key": f"jobs/{job_id}/attempt-1/out/{name}", "sha256": sha, "size_bytes": 1,
             "mime_type": "application/octet-stream"}
            for role, name in (("blend", "character.blend"), ("spec", "character.json"),
                               ("report", "validation.json"), ("thumbnail", "thumbnail.png"), ("glb", "preview.glb"))]


def test_outcome_attaches_to_the_named_draft(api, db):
    c = api.post("/api/v1/characters", json={"name": f"Out {uuid.uuid4().hex[:6]}", "spec": SPEC}).json()
    vid = c["versions"][0]["id"]
    job = api.post(f"/api/v1/characters/{c['id']}/drafts/{vid}/generate").json()["job"]
    with db.begin() as s:
        digest = s.get(CharacterVersion, uuid.UUID(vid)).spec_digest
        data = {"resolved_digest": digest, "height_m": 1.28, "report": {"ok": True, "blend_sha256": "e" * 64}}
        recorded = OUTCOMES["generate_character"](s, uuid.UUID(job["id"]), _uploaded(job["id"]), data, "5.2.3")
    assert recorded["attached"] and recorded["asset_version_id"] == vid and recorded["character_id"] == c["id"]
    v = api.get(f"/api/v1/characters/{c['id']}/versions/{vid}").json()
    assert v["ready_for_review"] == [] and v["blender_version"] == "5.2.3" and len(v["files"]) == 5

    assert api.delete(f"/api/v1/characters/{c['id']}/drafts/{vid}").status_code == 204
    with db.begin() as s:  # a build for a discarded draft is kept on the job only
        recorded = OUTCOMES["generate_character"](s, uuid.UUID(job["id"]), _uploaded(job["id"]), data, "5.2.3")
    assert recorded == {"attached": False, "reason": "the draft was discarded"}
