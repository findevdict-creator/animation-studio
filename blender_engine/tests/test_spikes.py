"""Phase 1 spikes kept as regression checks: they run against whatever Blender the tests
use, so a Blender upgrade that breaks linking, overrides or headless Rigify fails here."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")
SPIKES = Path(__file__).resolve().parents[1] / "spikes"


def run(script: str, *args: str) -> dict:
    proc = subprocess.run([sys.executable, str(SPIKES / script), *args], capture_output=True, text=True, timeout=300)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("SPIKE_RESULT ")), None)
    assert line, proc.stdout[-2000:] + proc.stderr[-2000:]
    result = json.loads(line.removeprefix("SPIKE_RESULT "))
    assert proc.returncode == 0, {k: v for k, v in result.items() if v is False}
    return result


def test_link_override_survives_a_move_to_another_machine(tmp_path):
    built = tmp_path / "worker-a"
    run("link_override.py", str(built), "build")
    moved = tmp_path / "somewhere" / "else" / "worker-b"
    shutil.copytree(built, moved)
    shutil.rmtree(built)  # nothing may still point at the old location
    r = run("link_override.py", str(moved), "verify")
    assert r["library_paths"] == {
        "character.blend": "//../assets/character/CHAR-001/1.0/character.blend",
        "outfit.blend": "//../assets/outfit/OUTFIT-002/1.0/outfit.blend",
    }
    assert r["outfit_bound_to_character_rig"] and r["garment_deforms"] and r["mask_hides_torso"]
    assert r["pose_kept"] and r["action_evaluates"] and r["sources_unchanged_after_reopen"]


def test_rigify_generates_headless(tmp_path):
    r = run("rigify_headless.py", str(tmp_path))
    assert r["required_deform_present"] and r["ik_fk_and_body_controls_present"] and r["ik_fk_switch_property"]
    assert r["deform_bones_all_DEF_prefixed"] and r["reopens_without_scripts"]
