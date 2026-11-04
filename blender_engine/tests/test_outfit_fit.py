"""Outfits in Blender: the two jobs as the worker runs them, attaching to a linked
character, and the fit check catching what it should."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

bpy = pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")

import cartoon_engine
from cartoon_engine.character.build import build_geometry
from cartoon_engine.character.library import link_character
from cartoon_engine.character.rig import build_rig
from cartoon_engine.character.skeleton import build_skeleton
from cartoon_engine.character.validate import find_parts
from cartoon_engine.core import blend_io
from cartoon_engine.jobs.generate_character import _stamp
from cartoon_engine.outfit.build import build_outfit
from cartoon_engine.outfit.check import fit_check
from cartoon_engine.outfit.fit import (
    MASK_PREFIX,
    attach_outfit,
    detach_outfit,
    hidden_mask,
)
from studio_contracts.character import (
    CharacterSpecification,
    resolve_spec,
)
from studio_contracts.ids import uuid7
from studio_contracts.outfit import FAMILY_CARTOON_CLOTHING as CONV
from studio_contracts.outfit import (
    OutfitSpecification,
    get_garment,
    reference_character,
    resolve_outfit,
)

ENTRY = Path(cartoon_engine.__file__).with_name("entrypoint.py")
FAST = {"resolution": 96, "samples": 1, "pose_tile": 64}
ALI = {"name": "Ali", "body": {"age_appearance": "child", "height_m": 1.31, "proportions": {"leg_length": 0.1,
                                                                                            "belly": -0.2}},
       "hair": {"style": "spiky"}}


def piece(key, **kw):
    g = get_garment(key)
    return {"slot": g.slot, "garment": {"id": g.id, "version": g.version}, **kw}


OUTFITS = {
    "school": [piece("tshirt", material={"color": "royal_blue", "trim_color": "white"}), piece("shorts"),
               piece("sneakers", material={"trim_color": "white"}), piece("cap"), piece("backpack")],
    "winter": [piece("sweater", params={"ease": 0.3}, material={"trim_color": "cream"}), piece("trousers"),
               piece("boots")],
    "smart": [piece("longsleeve", material={"color": "red", "trim_color": "white"}),
              piece("trousers", params={"ease": 0.1}, material={"fabric": "cotton", "color": "black"}),
              piece("sneakers", material={"color": "white", "trim_color": "black"})],
}


def spec(name, pieces=None):
    return OutfitSpecification(name=name, fit_profiles=["child_average"], pieces=pieces or OUTFITS[name])


def run_job(ws: Path, job_type: str, params: dict, inputs=()):
    job = {"job_id": str(uuid7()), "type": job_type, "attempt": 1, "params": params,
           "inputs": list(inputs), "contracts_version": "0.1.0"}
    (ws / "job.json").write_text(json.dumps(job))
    proc = subprocess.run([sys.executable, str(ENTRY), str(ws / "job.json")], capture_output=True, text=True, check=False,
                          timeout=600, cwd=ws)
    return proc, json.loads((ws / "result.json").read_text())


def save_outfit(path: Path, name: str, pieces=None) -> Path:
    ro = resolve_outfit(spec(name, pieces))
    blend_io.reset_empty()
    ref = build_geometry(resolve_spec(reference_character("child_average")), "CHAR_REF")
    coll, _ = build_outfit(ro, ref, ro.digest())
    bpy.data.libraries.write(str(path), {coll}, fake_user=True)
    return path


def save_character(dir_: Path, spec_doc: dict) -> tuple[Path, Path]:
    rc = resolve_spec(CharacterSpecification.model_validate(spec_doc))
    blend_io.reset_empty()
    ch = build_geometry(rc, collection_name=f"CHAR_{rc.name}")
    build_rig(ch)
    for target in (ch.rig, ch.collection):
        _stamp(target, rc, rc.digest())
    dir_.mkdir(parents=True, exist_ok=True)
    blend_io.save(dir_ / "character.blend")
    (dir_ / "character.json").write_text(json.dumps({"specification": spec_doc, "resolved": rc.model_dump(mode="json")}))
    return dir_ / "character.blend", dir_ / "character.json"


def sha(p: Path) -> str:
    import hashlib

    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("outfits")
    out = {n: save_outfit(d / f"{n}.blend", n) for n in OUTFITS}
    out["ali"] = save_character(d / "ali", ALI)
    return out


def test_generate_outfit_job(tmp_path):
    proc, result = run_job(tmp_path, "generate_outfit", {"spec": spec("school").model_dump(mode="json"),
                                                         "previews": FAST})
    assert proc.returncode == 0 and result["status"] == "succeeded", proc.stdout[-3000:] + str(result.get("error"))
    roles = {o["path"]: o["role"] for o in result["outputs"]}
    assert roles == {"outfit.blend": "blend", "outfit.json": "spec", "fit_report.json": "report",
                     "thumbnail.png": "thumbnail", "poses.png": "preview", "preview.glb": "glb"}
    data = result["data"]
    assert data["fit"]["ok"], data["report"]["failed"]
    report = json.loads((tmp_path / "out" / "fit_report.json").read_text())
    assert report["file"]["armatures"] == [] and report["file"]["collections"] == ["OUTFIT_school"]
    assert report["outfit_blend_sha256"] == sha(tmp_path / "out" / "outfit.blend") == data["blend_sha256"]
    assert set(report["poses"]["upper_tshirt"]) == set(CONV.poses)
    assert report["fit"]["body_vertices_hidden"] > 0
    assert {"upper_tshirt", "headwear_cap", "accessory_backpack"} <= set(report["glb"]["meshes"])
    names = {c["name"] for c in report["checks"]}
    assert {"no_clipping:upper_tshirt", "deformation:lower_shorts", "rigid_pieces_on_attachment_bones"} <= names


def test_fit_outfit_job_on_a_character(tmp_path, files):
    char_blend, char_json = files["ali"]
    (tmp_path / "inputs").mkdir()
    inputs = []
    for src, name in ((char_blend, "character.blend"), (char_json, "character.json"), (files["winter"], "outfit.blend")):
        dst = tmp_path / "inputs" / name
        dst.write_bytes(src.read_bytes())
        inputs.append({"path": f"inputs/{name}", "sha256": sha(dst)})
    params = {"outfit_version_id": str(uuid7()), "character_version_id": str(uuid7()), "previews": FAST}
    proc, result = run_job(tmp_path, "fit_outfit", params, inputs)
    assert proc.returncode == 0 and result["status"] == "succeeded", proc.stdout[-3000:] + str(result.get("error"))
    data = result["data"]
    assert data["ok"] and data["fit_profile_declared"], data["report"]["failed"]
    wearer = data["report"]["wearer"]
    assert wearer["name"] == "Ali" and wearer["rig_mismatch_m"] < 1e-3
    assert wearer["character_blend_sha256"] == sha(char_blend)

    # a description that does not match the rig is refused
    other = json.loads(char_json.read_text())
    other["resolved"]["height_m"] = 1.45
    (tmp_path / "inputs" / "character.json").write_text(json.dumps(other))
    inputs[1]["sha256"] = sha(tmp_path / "inputs" / "character.json")
    proc, result = run_job(tmp_path, "fit_outfit", params, inputs)
    assert result["status"] == "failed" and "does not describe this rig" in result["error"]


def test_publish_outfit_rechecks_the_exact_file(tmp_path, files):
    (tmp_path / "inputs").mkdir()
    dst = tmp_path / "inputs" / "outfit.blend"
    dst.write_bytes(files["smart"].read_bytes())
    inputs = [{"path": "inputs/outfit.blend", "sha256": sha(dst)}]
    params = {"version_id": str(uuid7()), "spec": spec("smart").model_dump(mode="json")}
    proc, result = run_job(tmp_path, "publish_outfit", params, inputs)
    assert result["status"] == "succeeded", proc.stdout[-3000:] + str(result.get("error"))
    report = result["data"]["report"]
    assert report["ok"], report["failed"]
    assert report["outfit_blend_sha256"] == sha(dst) and report["purpose"] == "publish"
    assert result["data"]["stamps"]["studio_outfit_digest"] == resolve_outfit(spec("smart")).digest()
    # the same file under another spec is refused: it was not built from it
    params["spec"] = spec("winter").model_dump(mode="json")
    _, result = run_job(tmp_path, "publish_outfit", params, inputs)
    assert result["status"] == "succeeded" and not result["data"]["report"]["ok"]
    assert "built_from_spec" in result["data"]["report"]["failed"]


def test_three_outfits_attach_independently_to_a_linked_character(files):
    char_blend, _ = files["ali"]
    rc = resolve_spec(CharacterSpecification.model_validate(ALI))
    blend_io.reset_empty()
    override = link_character(char_blend)
    parts = find_parts(override)
    assert parts.body.data.library is not None  # geometry stays in the character file
    modifiers_before = [m.name for m in parts.body.modifiers]
    groups_before = len(parts.body.vertex_groups)
    for name in OUTFITS:
        fitted = attach_outfit(files[name], parts, build_skeleton(rc))
        assert all(g.parent == parts.rig for g in fitted.garments)
        assert hidden_mask(parts).sum() == fitted.hidden.sum() > 0
        report, _ = fit_check(parts, fitted.garments, CONV.fit, CONV.poses)
        assert report.ok, (name, report.failed())
        detach_outfit(fitted, parts)
        assert [m.name for m in parts.body.modifiers] == modifiers_before
        assert not any(o.name.startswith("OUTFIT_") for o in bpy.data.collections)
    assert len(parts.body.vertex_groups) == groups_before


def test_fit_check_catches_clipping_and_overstretch(files, tmp_path):
    char_blend, _ = files["ali"]
    rc = resolve_spec(CharacterSpecification.model_validate(ALI))
    blend_io.open_file(char_blend)
    parts = find_parts()
    fitted = attach_outfit(files["school"], parts, build_skeleton(rc))
    shirt = next(g for g in fitted.garments if g.name.startswith("upper_"))
    co = np.empty(len(shirt.data.vertices) * 3)
    shirt.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    centre = np.array([0.0, 0.0, co[:, 2].mean()])
    shrunk = centre + (co - centre) * np.array([0.8, 0.8, 1.0])  # squeeze the shirt into the body
    shirt.data.vertices.foreach_set("co", shrunk.ravel())
    shirt.data.update()
    report, _ = fit_check(parts, fitted.garments, CONV.fit, ("rest", "twist"))
    assert "no_clipping:upper_tshirt" in report.failed()

    loose = save_outfit(tmp_path / "loose.blend", "loose", [piece("sweater", params={"ease": 1.0}), piece("trousers")])
    blend_io.open_file(char_blend)
    parts = find_parts()
    fitted = attach_outfit(loose, parts, build_skeleton(rc))
    report, per_pose = fit_check(parts, fitted.garments, CONV.fit, ("rest", "arms_up"))
    assert "deformation:upper_sweater" in report.failed()
    e = per_pose["upper_sweater"]["arms_up"]
    assert e["stretch_hi"] > e["skin_stretch_hi"] * 1.3
    assert not any(m.name.startswith(MASK_PREFIX) for m in parts.body.modifiers[:1])


def test_wardrobe_check_tool(files):
    from cartoon_engine.tools.wardrobe_check import check

    char_blend, char_json = files["ali"]
    doc = check(char_blend, char_json, [files[n] for n in OUTFITS])
    assert doc["ok"], doc
    assert doc["character"]["linked_mesh_data"] and doc["character"]["rig_mismatch_m"] < 1e-3
    assert [r["outfit"] for r in doc["outfits"]] == list(OUTFITS)
    assert all(r["character_unchanged_after_detach"] and r["hidden_body_vertices"] > 0 for r in doc["outfits"])
