"""Camera movement over a scene (SRS 15 "camera movement"): the staged camera is keyed at the
scene's first and last frame.

* ``push_in`` moves it forward along its line of view by a fifth of the distance to the cast
  (at most 0.8 m), so it ends closer; ``pull_out`` starts there and ends at the staged spot.
  Both stay on the staged camera's line of view, so a camera kept inside a room stays inside.
* ``pan_left`` / ``pan_right`` keep the camera where it is and turn it about the vertical
  axis by 8 degrees across the scene, starting 4 degrees to the other side.

A camera mark's camera is linked from the environment file and cannot be keyed in the scene,
so a moving shot on a camera mark uses a local copy (``CAM_move``) with the same lens and
placement. The move is stamped on the camera (``studio_camera_move``) for the checks.
"""

from __future__ import annotations

import json
import math

from studio_contracts.scene import ResolvedScene

PUSH_SHARE = 0.2
PUSH_MAX_M = 0.8
PAN_DEG = 8.0


def cast_centre(rs: ResolvedScene) -> tuple[float, float, float]:
    locs = [inst.transform.location for inst in rs.instances]
    n = max(len(locs), 1)
    return (sum(v[0] for v in locs) / n, sum(v[1] for v in locs) / n, 0.9)


def local_camera(scene, cam):
    """``cam`` itself when it can be keyed, else a local copy that replaces it."""
    import bpy

    if cam.library is None and cam.override_library is None:
        return cam
    data = bpy.data.cameras.new("CAM_move")
    data.lens, data.sensor_width = cam.data.lens, cam.data.sensor_width
    data.clip_start, data.clip_end = cam.data.clip_start, cam.data.clip_end
    copy = bpy.data.objects.new("CAM_move", data)
    scene.collection.objects.link(copy)
    copy.matrix_world = cam.matrix_world.copy()
    copy["studio_camera_from"] = cam.name
    scene.camera = copy
    return copy


def animate_camera(scene, cam, rs: ResolvedScene) -> dict | None:
    """Key the scene's camera move; returns what was done (None for a static camera)."""
    from mathutils import Matrix, Vector

    move = rs.camera.movement
    if move == "static":
        return None
    cam = local_camera(scene, cam)
    f0, f1 = rs.frame_start, rs.frame_end
    start = cam.matrix_world.copy()
    loc, rot = start.to_translation(), start.to_quaternion()
    forward = rot @ Vector((0.0, 0.0, -1.0))
    if move in ("push_in", "pull_out"):
        dist = (Vector(cast_centre(rs)) - loc).length
        step = min(PUSH_MAX_M, PUSH_SHARE * dist)
        near = loc + forward * step
        a, b = (loc, near) if move == "push_in" else (near, loc)
        cam.location = a
        cam.keyframe_insert("location", frame=f0)
        cam.location = b
        cam.keyframe_insert("location", frame=f1)
        done = {"movement": move, "from": [round(v, 4) for v in a], "to": [round(v, 4) for v in b],
                "distance_m": round(step, 4)}
    else:
        sign = 1.0 if move == "pan_left" else -1.0
        cam.rotation_mode = "XYZ"
        keys = []
        for frame, deg in ((f0, -sign * PAN_DEG / 2), (f1, sign * PAN_DEG / 2)):
            turned = Matrix.Rotation(math.radians(deg), 4, "Z") @ Matrix.Translation(-loc) @ start
            turned = Matrix.Translation(loc) @ turned
            cam.matrix_world = turned
            cam.keyframe_insert("rotation_euler", frame=frame)
            keys.append([round(math.degrees(v), 3) for v in cam.rotation_euler])
        cam.location = loc
        done = {"movement": move, "rotation_deg": keys, "yaw_deg": PAN_DEG * sign}
    done["camera"] = cam.name
    cam["studio_camera_move"] = json.dumps(done)
    scene.frame_set(f0)
    return done


def camera_track(scene, cam, f0: int, f1: int) -> tuple[list[float], list[float], float]:
    """Where the camera is and how far it turned between two frames (for the reopen check)."""
    scene.frame_set(f0)
    a, ra = cam.matrix_world.to_translation().copy(), cam.matrix_world.to_euler()
    scene.frame_set(f1)
    b, rb = cam.matrix_world.to_translation().copy(), cam.matrix_world.to_euler()
    scene.frame_set(f0)
    return list(a), list(b), math.degrees(rb.z - ra.z)
