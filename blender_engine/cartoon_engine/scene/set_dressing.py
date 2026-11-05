"""The set of a scene (Phase 7): the pinned environment, the pinned props, the camera at a
camera mark, and the lighting.

* The environment's ``ENV_`` collection is linked from its pinned ``environment.blend``
  under a local ``SET`` collection, so its floor, walls, dressing, marks and camera marks
  are library data; its ``LIGHTS_`` rig is linked too, unless the scene overrides the
  lighting, in which case the override preset's rig is built locally with the same code
  (``LIGHTS_override``). The sky is the preset's world.
* Each prop use is an empty ``PROPI_<key>`` instancing the ``PROP_`` collection linked from
  its pinned ``prop.blend``, at its spot, height and turn; one linked collection serves every
  use of the same version.
* Without an environment the scene keeps the Phase 5 stage (``stage_camera`` adds a floor
  and two suns); props then stand on that floor.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from studio_contracts.environment import CHARACTER_RADIUS_M, LIGHTING, contains, overlap
from studio_contracts.scene import ResolvedScene

SET = "SET"
PROP_INSTANCE = "PROPI_"


def _link(path: Path, prefixes: tuple[str, ...]):
    import bpy

    path = Path(path).resolve()
    with bpy.data.libraries.load(str(path), link=True, relative=True) as (src, dst):
        dst.collections = [n for n in src.collections if n.startswith(prefixes)]
    return {next(p for p in prefixes if c.name.startswith(p)): c for c in dst.collections if c is not None}


def build_set(ws: Path, rs: ResolvedScene, scene) -> dict:
    import bpy

    from cartoon_engine.environment import lighting

    report: dict = {"environment": None, "props": [], "lighting": rs.lighting}
    set_coll = bpy.data.collections.new(SET)
    scene.collection.children.link(set_coll)
    set_coll["studio_set"] = True
    if rs.environment is not None:
        path = ws / rs.environment.path / "environment.blend"
        linked = _link(path, ("ENV_",))
        set_coll.children.link(linked["ENV_"])
        own = linked["ENV_"].get("studio_lighting")
        if rs.lighting and rs.lighting != own:
            size = tuple(rs.layout["size"])
            rig = lighting.build_rig(LIGHTING[rs.lighting], size, rs.layout["kind"], "override")
            set_coll.children.link(rig)
            rig["studio_lighting_override"] = True
        else:
            set_coll.children.link(_link(path, ("LIGHTS_",))["LIGHTS_"])
        lighting.apply_world(scene, LIGHTING[rs.lighting or own])
        report["environment"] = {"ref": rs.environment.ref, "collection": linked["ENV_"].name,
                                 "library": linked["ENV_"].library.filepath, "lighting_from_file": own,
                                 "lighting_override": bool(rs.lighting and rs.lighting != own)}
    elif rs.lighting:
        lighting.apply_world(scene, LIGHTING[rs.lighting])
    by_version: dict[str, object] = {}
    for use in rs.props:
        coll = by_version.get(use.pin.version_id)
        if coll is None:
            coll = _link(ws / use.pin.path / "prop.blend", ("PROP_",))["PROP_"]
            by_version[use.pin.version_id] = coll
        e = bpy.data.objects.new(f"{PROP_INSTANCE}{use.key}", None)
        e.instance_type = "COLLECTION"
        e.instance_collection = coll
        e.location = (use.at[0], use.at[1], use.z)
        e.rotation_euler = (0, 0, math.radians(use.rotation_deg))
        e["studio_prop"] = use.key
        e["studio_source"] = f"{use.pin.ref}#{use.pin.files['prop.blend']}"
        set_coll.objects.link(e)
        report["props"].append({"key": use.key, "ref": use.pin.ref, "at": list(use.at), "z": use.z,
                                "on": use.on, "library": coll.library.filepath})
    return report


def set_camera(scene, rs: ResolvedScene):
    """The camera mark's camera, linked from the environment, when the scene names one."""
    import bpy

    if rs.camera.mark is None:
        return None
    cam = next((o for o in bpy.data.objects if o.name == f"CAM_{rs.camera.mark}" and o.library is not None), None)
    if cam is None:
        raise RuntimeError(f"the environment has no camera mark {rs.camera.mark!r}")
    scene.camera = cam
    return cam


def keep_inside(scene, cam, rs: ResolvedScene, lo, hi) -> dict | None:
    """A framed camera that backed out through a side or back wall of a room is brought back
    inside along its line of view (the south wall is the cutaway, it may stand behind it) and
    widened until the cast fits again."""
    import itertools

    import bpy
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector

    if rs.layout is None or rs.layout["kind"] != "room":
        return None
    w, d, h = rs.layout["size"]
    x, y, z = cam.location
    margin = 0.15
    nx, ny, nz = max(-w / 2 + margin, min(w / 2 - margin, x)), min(d / 2 - margin, y), min(h - margin, z)
    if (nx, ny, nz) == (x, y, z):
        return None
    cam.location = (nx, ny, nz)
    centre = Vector(((np.asarray(lo) + np.asarray(hi)) / 2).tolist())
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    corners = [Vector(c) for c in itertools.product(*zip(list(lo), list(hi), strict=True))]
    lens0 = cam.data.lens
    for _ in range(30):
        bpy.context.view_layer.update()
        views = [world_to_camera_view(scene, cam, c) for c in corners]
        if all(0.02 <= v.x <= 0.98 and 0.02 <= v.y <= 0.98 and v.z > 0 for v in views):
            break
        if cam.data.lens <= 14:
            break
        cam.data.lens *= 0.92
    return {"moved_from": [round(x, 3), round(y, 3), round(z, 3)], "to": [round(nx, 3), round(ny, 3), round(nz, 3)],
            "lens_mm": [round(lens0, 1), round(cam.data.lens, 1)]}


# ------------------------------------------------------------------ measured placement
def _instance_world_verts(empty) -> np.ndarray:
    pts = []
    m_e = np.array(empty.matrix_world)
    for o in empty.instance_collection.all_objects:
        if o.type != "MESH":
            continue
        n = len(o.data.vertices)
        co = np.empty(n * 3)
        o.data.vertices.foreach_get("co", co)
        m = m_e @ np.array(o.matrix_world)
        pts.append(co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])
    return np.vstack(pts)


def _local_rect(verts: np.ndarray, at, rot_deg: float):
    a = math.radians(rot_deg)
    c, s = math.cos(-a), math.sin(-a)
    lx = (verts[:, 0] - at[0]) * c - (verts[:, 1] - at[1]) * s
    ly = (verts[:, 0] - at[0]) * s + (verts[:, 1] - at[1]) * c
    cx, cy = (lx.max() + lx.min()) / 2, (ly.max() + ly.min()) / 2
    ca, sa = math.cos(a), math.sin(a)
    return (at[0] + cx * ca - cy * sa, at[1] + cx * sa + cy * ca, float(lx.max() - lx.min()),
            float(ly.max() - ly.min()), rot_deg)


def set_checks(rs: ResolvedScene, scene, ws: Path, root_paths: dict[str, list]) -> list[dict]:
    """The reopened set against the manifest: the environment and props come from their
    pins, props stand where and how high the manifest says, inside the set and clear of
    solid dressing and of each other, the camera is the named mark, the lighting is the
    one in force, and every character's root stays on free floor for the whole scene
    (``root_paths``: instance key -> [(frame, x, y)])."""
    import bpy

    bpy.context.view_layer.update()
    checks: list[dict] = []

    def add(name: str, ok: bool, **detail) -> None:
        checks.append({"name": name, "ok": bool(ok), **detail})

    layout = rs.layout
    if rs.environment is not None:
        env = next((c for c in bpy.data.collections if c.name.startswith("ENV_") and c.library is not None), None)
        lib = bpy.path.abspath(env.library.filepath).replace("\\", "/") if env else None
        add("set:environment_linked_from_pin", env is not None and lib.endswith(
            f"{rs.environment.path}/environment.blend") and not any(
            o.library is None for o in env.all_objects), library=env.library.filepath if env else None)
        set_coll = bpy.data.collections.get(SET)
        lights = [c for c in (set_coll.children if set_coll else []) if c.name.startswith("LIGHTS_")]
        world = scene.world.get("studio_lighting") if scene.world else None
        lit = [c.get("studio_lighting") for c in lights]
        add("set:lighting", world == rs.lighting and lit == [rs.lighting], world=world, rigs=lit,
            expected=rs.lighting)
    if rs.camera.mark is not None:
        add("set:camera_mark", scene.camera is not None and scene.camera.name == f"CAM_{rs.camera.mark}"
            and scene.camera.library is not None, camera=scene.camera.name if scene.camera else None)

    size = layout["size"] if layout else None
    solids = [(s[0], tuple(s[1:6])) for s in (layout or {}).get("solids", [])]
    placed = []
    for use in rs.props:
        e = bpy.data.objects.get(f"{PROP_INSTANCE}{use.key}")
        name = f"prop:{use.key}"
        if e is None or e.instance_collection is None:
            add(f"{name}:instanced", False)
            continue
        lib = bpy.path.abspath(e.instance_collection.library.filepath).replace("\\", "/") \
            if e.instance_collection.library else ""
        add(f"{name}:linked_from_pin", lib.endswith(f"{use.pin.path}/prop.blend")
            and e.get("studio_source") == f"{use.pin.ref}#{use.pin.files['prop.blend']}", library=lib)
        v = _instance_world_verts(e)
        bottom = float(v[:, 2].min())
        add(f"{name}:grounded", abs(bottom - use.z) <= 0.006, bottom_z=round(bottom, 4), expected=use.z, on=use.on)
        rect = _local_rect(v, use.at, use.rotation_deg)
        if use.on is None:
            if size is not None:
                over = float(max(np.abs(v[:, 0]).max() - size[0] / 2, np.abs(v[:, 1]).max() - size[1] / 2))
                add(f"{name}:inside", over <= 0.001, overshoot_m=round(over, 4))
            solid = bool(e.instance_collection.all_objects[0].get("studio_solid", True))
            hits = [n for n, r in solids if overlap(rect, r)]
            hits += [k for k, r, sol in placed if (sol or solid) and overlap(rect, r)]
            add(f"{name}:clear", not hits, overlaps=hits)
            placed.append((use.key, rect, solid))

    walls = size is not None
    for key, path in root_paths.items():
        blocked = []
        for f, x, y in path:
            if walls and (abs(x) > size[0] / 2 - CHARACTER_RADIUS_M + 0.02 or abs(y) > size[1] / 2 - CHARACTER_RADIUS_M
                          + 0.02):
                blocked.append([f, "wall"])
                continue
            for n, r in [*solids, *((k, r) for k, r, sol in placed if sol)]:
                if contains(r, x, y, CHARACTER_RADIUS_M - 0.05):
                    blocked.append([f, n])
                    break
        add(f"path:{key}", not blocked, frames=len(path), blocked=blocked[:10],
            start=[round(path[0][1], 3), round(path[0][2], 3)] if path else None,
            end=[round(path[-1][1], 3), round(path[-1][2], 3)] if path else None)
    return checks


def root_paths(rs: ResolvedScene, scene, step: int = 4) -> dict[str, list]:
    """Each instance's root on the floor, every ``step`` frames and the last."""
    import bpy

    from cartoon_engine.character.validate import find_parts
    from cartoon_engine.scene.assemble import INSTANCE_PREFIX

    rigs = {}
    for inst in rs.instances:
        coll = bpy.data.collections.get(f"{INSTANCE_PREFIX}{inst.key}")
        ov = [c for c in coll.children if c.override_library is not None] if coll else []
        if ov:
            rigs[inst.key] = find_parts(ov[0]).rig
    frames = sorted({*range(rs.frame_start, rs.frame_end + 1, step), rs.frame_end})
    out: dict[str, list] = {k: [] for k in rigs}
    for f in frames:
        scene.frame_set(f)
        for k, rig in rigs.items():
            p = rig.matrix_world @ rig.pose.bones["root"].matrix.translation
            out[k].append((f, float(p.x), float(p.y)))
    scene.frame_set(rs.frame_start)
    return out
