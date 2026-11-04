# Phase 1 spikes

Three questions from the architecture had to be answered on real software before Phase 2
builds on them. All three ran on **Blender 5.2.3 LTS** (the `bpy` wheel from PyPI, Python
3.13) on 2026-11-04. The two Blender spikes are kept as regression tests
(`blender_engine/tests/test_spikes.py`), so a Blender upgrade that breaks them fails CI.

| Spike | Question | Result |
| --- | --- | --- |
| (a) Linking and overrides | Can a scene link a published character and outfit, rebind the outfit to the character's rig, mask the body, pose and animate, save with relative paths and reopen elsewhere, without touching the sources? | **Yes**, with two rules (below) |
| (b) Headless Rigify | Can Rigify generate a rig in background mode from a metarig whose bones were moved? | **Yes** |
| (c) Conditional writes | Does the local S3 server refuse a second write to a write-once key? | **SeaweedFS 4.50: yes. Garage 2.1: no** |

## (a) Linking and library overrides

Script: `blender_engine/spikes/link_override.py`. It runs `build` in one process and
`verify` in a fresh process. The test copies the whole workspace to a different directory
and deletes the original before verifying.

What `build` does:

1. Writes `assets/character/CHAR-001/1.0/character.blend`: a collection `CHAR` holding an
   armature `rig` (`root`, `DEF-spine`, `DEF-upper_arm.L`) and a skinned `body` mesh that has
   a `mask_torso` vertex group.
2. Writes `assets/outfit/OUTFIT-002/1.0/outfit.blend`: a collection `OUTFIT` holding a
   skinned `shirt` whose Armature modifier targets a placeholder armature `RIG_TARGET`.
3. In `scene/scene.blend`, links both collections and runs
   `override_hierarchy_create(..., do_fully_editable=True)` on each one.
4. Sets the shirt override's Armature modifier to the character override's rig, adds an
   inverted Mask modifier on the body, sets a static pose, assigns a scene-local Action, and
   saves.

Measured in `verify`, after the move:

| Check | Value |
| --- | --- |
| library paths | `//../assets/character/CHAR-001/1.0/character.blend`, `//../assets/outfit/OUTFIT-002/1.0/outfit.blend` |
| absolute workspace path anywhere in `scene.blend` | not present |
| shirt's Armature modifier target | the character's overridden rig |
| shirt vertices moved by the rig's pose | 36 |
| body vertices hidden by the mask | 16 |
| static pose (quaternion, 45°) | kept |
| scene-local Action, evaluated at frame 25 | kept, spine at 10.0° |
| mesh data | still linked (not copied into the scene) |
| SHA-256 of both source files after save and after reopen | unchanged |

Two findings that become engine rules:

- **Do not change `rotation_mode` on an override.** In the first run the pose was set by
  switching a bone to `XYZ` and setting `rotation_euler`. After reopening, the rotation read
  0°. The override recorded both properties, but on reload the mode was applied after the
  value, and the mode change converted the value away. With the bone's published rotation
  mode (quaternion) the pose survives. The rig profile fixes rotation modes in the published
  character, and the scene code never changes them.
- **New custom properties on an override are dropped on save**, even after
  `property_overridable_library_set`. Scene and instance metadata therefore lives in the
  scene manifest (decision D5), never on Blender objects. Modifiers, pointers, poses and
  Actions are kept.

Saving computes the override operations by itself. A run with `--no-operations-update`
gives the same result, so the engine's explicit `operations_update()` call is there for
inspection only.

## (b) Rigify in background mode

Script: `blender_engine/spikes/rigify_headless.py`. It enables the bundled Rigify add-on,
adds the stock human metarig, moves the leg and shoulder bones as the character generator
will, then runs `bpy.ops.pose.rigify_generate()`.

| Check | Value |
| --- | --- |
| generation time | about 7 s on this machine (6.87 s and 7.01 s in two runs) |
| bones / deform bones | 706 / 160, all deform bones prefixed `DEF-` |
| deform bones the profile needs (spine, arm chain, leg chain, fingers, thumb) | all present |
| controls (`root`, `torso`, `hand_ik.L`, `upper_arm_fk.L`, `foot_ik.R`, `thigh_fk.R`, `head`, `neck`) | all present |
| `IK_FK` switch property on the arm | present |
| finger controls | 30 |
| reopens with scripts disabled | yes, same bone count |

Finding: **do not pin `active_object` with `context.temp_override`** around the generate
operator. Rigify makes the new rig active and asserts that `context.active_object` follows
it, so a pinned override raises `AssertionError`. Setting `view_layer.objects.active`
before the call works.

The stock human metarig includes a face rig, which accounts for most of the 160 deform
bones. The `cartoon_humanoid_v1` profile will drop it in favour of facial shape keys
(architecture 8.2). That is Phase 2 work.

## (c) Conditional writes on the S3 server

MinIO stopped publishing community Docker images in 2025, so the dev stack needed another
S3 server.

| Server | `PutObject` with `If-None-Match: *` on an existing key | `CompleteMultipartUpload` with `If-None-Match: *` | Per-bucket identity permissions |
| --- | --- | --- | --- |
| Garage 2.1.0 | accepted (overwrote) | accepted (overwrote) | per key and bucket, not per prefix |
| SeaweedFS 4.50 | refused, `PreconditionFailed` | refused, `PreconditionFailed` | yes (`Read:bucket`, `Write:bucket`) |

SeaweedFS is the dev server. The library and work buckets are split so that write-once
needs only per-bucket credentials (architecture 10.1). `backend/tests/test_storage.py` runs
the conditional-write and access-refusal checks against a live server on every test run.
AWS S3 documents the same `If-None-Match` behaviour. The production store still has to be
checked when it is chosen.
