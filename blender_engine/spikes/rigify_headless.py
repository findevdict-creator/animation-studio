"""Spike (b), architecture 8.1 and risk R5: can Rigify generate a rig in background mode,
from a metarig whose bones were moved (as the character generator will do after shaping
the body), and does the result have the deform bones, IK/FK switches and finger controls
the rig standard needs?

    python blender_engine/spikes/rigify_headless.py <workdir>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import bpy  # noqa: E402  (first, so the bundled add-ons resolve)

CHECKS: dict[str, object] = {}


def main(work: Path) -> None:
    import addon_utils

    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable("rigify", default_set=True, persistent=False)
    CHECKS["rigify_enabled"] = "rigify" in {m.__name__ for m in addon_utils.modules() if addon_utils.check(m.__name__)[1]}

    # The stock human metarig, as the template will carry it.
    bpy.ops.object.armature_human_metarig_add()
    meta = bpy.context.active_object
    CHECKS["metarig_bones"] = len(meta.data.bones)

    # Move landmarks the way the generator will: longer legs, wider shoulders.
    bpy.ops.object.mode_set(mode="EDIT")
    eb = meta.data.edit_bones
    for name in ("thigh.L", "thigh.R", "shin.L", "shin.R"):
        eb[name].tail.z -= 0.05
    for name in ("shoulder.L", "shoulder.R"):
        eb[name].tail.x *= 1.15
    bpy.ops.object.mode_set(mode="OBJECT")

    t = time.monotonic()
    # No temp_override of active_object: the generator makes the new rig active itself and
    # asserts that context.active_object follows, which a pinned override would break.
    bpy.context.view_layer.objects.active = meta
    meta.select_set(True)
    bpy.ops.pose.rigify_generate()
    CHECKS["generate_seconds"] = round(time.monotonic() - t, 2)

    rig = next((o for o in bpy.data.objects if o.type == "ARMATURE" and o is not meta), None)
    CHECKS["rig_generated"] = rig is not None
    if rig is None:
        return
    names = [b.name for b in rig.data.bones]
    deform = [b.name for b in rig.data.bones if b.use_deform]
    CHECKS["rig_name"] = rig.name
    CHECKS["bones_total"] = len(names)
    CHECKS["deform_bones"] = len(deform)
    CHECKS["deform_bones_all_DEF_prefixed"] = all(n.startswith("DEF-") for n in deform)
    required = ["DEF-spine", "DEF-upper_arm.L", "DEF-forearm.L", "DEF-hand.L", "DEF-thigh.L", "DEF-shin.L",
                "DEF-foot.L", "DEF-f_index.01.L", "DEF-thumb.01.R"]
    CHECKS["missing_required_deform"] = [n for n in required if n not in names]
    CHECKS["required_deform_present"] = not CHECKS["missing_required_deform"]
    controls = ["root", "torso", "hand_ik.L", "upper_arm_fk.L", "foot_ik.R", "thigh_fk.R", "head", "neck"]
    CHECKS["missing_controls"] = [n for n in controls if n not in names]
    CHECKS["ik_fk_and_body_controls_present"] = not CHECKS["missing_controls"]
    props = rig.pose.bones["upper_arm_parent.L"].keys() if "upper_arm_parent.L" in rig.pose.bones else []
    CHECKS["ik_fk_switch_property"] = "IK_FK" in props
    CHECKS["finger_controls"] = sum(1 for n in names if n.startswith(("f_index", "f_middle", "thumb")) and not n.startswith("DEF-"))
    # moved landmark survived generation
    CHECKS["finding_shin_tail_z_after_move"] = round(rig.data.bones["DEF-shin.L.001"].tail_local.z, 4) if "DEF-shin.L.001" in names else None

    out = work / "rigify_rig.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    bpy.ops.wm.open_mainfile(filepath=str(out), load_ui=False, use_scripts=False)
    again = bpy.data.objects.get(CHECKS["rig_name"])
    CHECKS["reopens_without_scripts"] = again is not None and len(again.data.bones) == CHECKS["bones_total"]
    CHECKS["blender_version"] = bpy.app.version_string


if __name__ == "__main__":
    work = Path(sys.argv[-1]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    main(work)
    print("SPIKE_RESULT " + json.dumps(CHECKS))
    sys.exit(1 if any(v is False for v in CHECKS.values()) else 0)
