"""Two versions of one character, saved as separate files, linked into one scene with
library overrides: each keeps its own data and passes the full validator (structure and
motion) through its override, as a scene will use them."""

import pytest

bpy = pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")

from cartoon_engine.character.build import build_geometry  # noqa: E402
from cartoon_engine.character.rig import build_rig  # noqa: E402
from cartoon_engine.core import blend_io  # noqa: E402
from cartoon_engine.jobs.generate_character import _stamp  # noqa: E402
from cartoon_engine.tools.load_check import check  # noqa: E402
from studio_contracts.character import CharacterSpecification, resolve_spec  # noqa: E402


def save(path, height, hair):
    rc = resolve_spec(CharacterSpecification.model_validate(
        {"name": "Ali", "body": {"age_appearance": "child", "height_m": height}, "hair": {"style": hair}}))
    blend_io.reset_empty()
    ch = build_geometry(rc, collection_name="CHAR_Ali")
    build_rig(ch)
    for target in (ch.rig, ch.collection):
        _stamp(target, rc, rc.digest())
    blend_io.save(path)
    return rc.digest()


def test_two_versions_in_one_scene(tmp_path):
    a, b = tmp_path / "1.0" / "character.blend", tmp_path / "2.0" / "character.blend"
    a.parent.mkdir()
    b.parent.mkdir()
    digests = save(a, 1.28, "short"), save(b, 1.34, "spiky")
    doc = check([a, b])
    assert doc["ok"], doc
    one, two = doc["characters"]
    assert [one["library"], two["library"]] == [str(a), str(b)]
    assert one["collection"] != two["collection"] and one["rig"] != two["rig"]
    assert one["linked_mesh_data"] and two["linked_mesh_data"]  # geometry stays in the published files
    assert (one["stamps"]["studio_spec_digest"], two["stamps"]["studio_spec_digest"]) == digests
    assert (one["stamps"]["studio_height_m"], two["stamps"]["studio_height_m"]) == (1.28, 1.34)
    assert one["checks"] == two["checks"] == 22
