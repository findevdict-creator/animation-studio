"""Accessories in Blender: the two jobs as the worker runs them, and every catalog item put on
a linked character (wearing an outfit) at each of its attachment points."""

import json
from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")

import importlib.util
import sys

_spec = importlib.util.spec_from_file_location("outfit_helpers", Path(__file__).with_name("test_outfit_fit.py"))
_h = sys.modules.setdefault("outfit_helpers", importlib.util.module_from_spec(_spec))
_spec.loader.exec_module(_h)
ALI, run_job, save_character, save_outfit, sha = _h.ALI, _h.run_job, _h.save_character, _h.save_outfit, _h.sha

from cartoon_engine.accessory.attach import attach_accessory
from cartoon_engine.accessory.build import build_accessory
from cartoon_engine.accessory.check import attach_check
from cartoon_engine.character.library import link_character
from cartoon_engine.character.skeleton import build_skeleton
from cartoon_engine.character.validate import find_parts
from cartoon_engine.core import blend_io
from cartoon_engine.jobs.accessory_common import reference_wearer
from cartoon_engine.outfit.fit import attach_outfit
from studio_contracts.accessory import (
    ACCESSORIES,
    AccessorySpecification,
    get_accessory_conventions,
    resolve_accessory,
)
from studio_contracts.character import CharacterSpecification, resolve_spec

CONV = get_accessory_conventions("family_cartoon@1.0")
ACC_FAST = {"resolution": 96, "samples": 1}


def acc_spec(key: str, **kw) -> AccessorySpecification:
    item = next(a for a in ACCESSORIES if a.key == key)
    return AccessorySpecification(name=item.label, item={"id": item.id, "version": item.version}, **kw)


def save_accessory(path: Path, spec: AccessorySpecification) -> Path:
    ra = resolve_accessory(spec)
    blend_io.reset_empty()
    _, ref, parts = reference_wearer(ra, rigged=False)
    coll, _, _, _ = build_accessory(ra, ref.skeleton, parts, ra.digest())
    bpy.data.libraries.write(str(path), {coll}, fake_user=True)
    return path


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("accessories")
    out = {a.key: save_accessory(d / f"{a.key}.blend", acc_spec(a.key)) for a in ACCESSORIES}
    out["ali"] = save_character(d / "ali", ALI)
    out["winter"] = save_outfit(d / "winter.blend", "winter")
    return out


def test_generate_accessory_job(tmp_path):
    spec = acc_spec("glasses", material={"color": "red", "accent_color": "#DDEEFF"})
    proc, result = run_job(tmp_path, "generate_accessory", {"spec": spec.model_dump(mode="json"), "previews": ACC_FAST})
    assert proc.returncode == 0 and result["status"] == "succeeded", proc.stdout[-3000:] + str(result.get("error"))
    roles = {o["path"]: o["role"] for o in result["outputs"]}
    assert roles == {"accessory.blend": "blend", "accessory.json": "spec", "attach_report.json": "report",
                     "thumbnail.png": "thumbnail", "on_reference.png": "preview", "preview.glb": "glb"}
    data = result["data"]
    assert data["check"]["ok"], data["report"]["failed"]
    report = json.loads((tmp_path / "out" / "attach_report.json").read_text())
    assert report["file"]["armatures"] == [] and report["file"]["objects"] == ["ACC_glasses"]
    assert report["accessory_blend_sha256"] == sha(tmp_path / "out" / "accessory.blend")
    assert set(report["poses"]["ACC_glasses"]) == set(CONV.poses)
    assert report["placement"]["bone"] == "DEF-spine.006" and report["placement"]["library"]
    assert "ACC_glasses" in report["glb"]["meshes"]


def test_publish_accessory_rechecks_the_exact_file(tmp_path, files):
    (tmp_path / "inputs").mkdir()
    dst = tmp_path / "inputs" / "accessory.blend"
    dst.write_bytes(files["watch"].read_bytes())
    inputs = [{"path": "inputs/accessory.blend", "sha256": sha(dst)}]
    params = {"version_id": "00000000-0000-7000-8000-000000000001", "spec": acc_spec("watch").model_dump(mode="json")}
    proc, result = run_job(tmp_path, "publish_accessory", params, inputs)
    assert result["status"] == "succeeded", proc.stdout[-3000:] + str(result.get("error"))
    report = result["data"]["report"]
    assert report["ok"], report["failed"]
    assert result["data"]["stamps"]["studio_accessory_digest"] == resolve_accessory(acc_spec("watch")).digest()
    params["spec"] = acc_spec("watch", params={"face_size": 0.9}).model_dump(mode="json")
    _, result = run_job(tmp_path, "publish_accessory", params, inputs)
    assert result["status"] == "succeeded" and "built_from_spec" in result["data"]["report"]["failed"]


@pytest.mark.parametrize("key", [a.key for a in ACCESSORIES])
def test_every_item_on_a_linked_character_at_every_point(files, key):
    rc = resolve_spec(CharacterSpecification.model_validate(ALI))
    item = next(a for a in ACCESSORIES if a.key == key)
    blend_io.reset_empty()
    parts = find_parts(link_character(files["ali"][0]))
    sk = build_skeleton(rc)
    fitted = attach_outfit(files["winter"], parts, sk)
    atts = [attach_accessory(files[key], parts, sk, point=p, garments=fitted.garments) for p in item.attach_points]
    for a in atts:
        assert a.obj.data.library is not None  # the mesh stays in the accessory file
        assert a.obj.parent == parts.rig and a.obj.parent_bone == a.bone
    if len(atts) == 2:
        assert atts[0].info["mirrored"] != atts[1].info["mirrored"]
    report, _ = attach_check(parts, atts, CONV.thresholds, CONV.poses)
    assert report.ok, report.failed()


def test_an_item_inside_the_head_fails_the_check(files):
    rc = resolve_spec(CharacterSpecification.model_validate(ALI))
    blend_io.reset_empty()
    parts = find_parts(link_character(files["ali"][0]))
    att = attach_accessory(files["glasses"], parts, build_skeleton(rc))
    att.obj.matrix_basis.translation.y += 0.06  # pushed back into the face
    report, per_pose = attach_check(parts, [att], CONV.thresholds, ("rest", "head_turn"))
    assert not report.ok and "clear_of_body:ACC_glasses" in report.failed()
    assert per_pose["ACC_glasses"]["rest"]["inside_share"] > CONV.thresholds.max_inside_share
