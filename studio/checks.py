"""Hard cross-reference checks the coordinator runs in code after every agent.

Each function takes the validated upstream models and returns a list of
Issues. An error means the agent is re-asked with the issue list; a warning
never blocks. The checks mirror the numbered list in the design doc:

1. schema (done by Pydantic before these run)
2. every referenced ID exists upstream
3. every library_id / clip_id exists in the manifest or carries a build request
4. rig, shape key and outfit slot compatibility
5. every beat belongs to exactly one scene, in story order
6. every non-background character appears in a scene
7. shot frame ranges contiguous; scene durations within tolerance of target
8. clips do not overlap beyond blend; paths inside environment bounds and camera frustum
9. dialogue speakers present; verbatim story lines appear in some shot
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from studio.ids import shot_scene
from studio.schemas.characters import Characters
from studio.schemas.config import Config
from studio.schemas.continuity import Issue
from studio.schemas.direction import Direction
from studio.schemas.layout import Layout, ShotLayout
from studio.schemas.manifest import Manifest
from studio.schemas.story_bible import StoryBible
from studio.schemas.world import World


class IssueList:
    """Collects issues with sequential ids."""

    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def add(
        self,
        severity: str,
        category: str,
        owner: str,
        message: str,
        where: str = "",
        entity_ids: Iterable[str] = (),
        fix: str = "",
    ) -> None:
        self.issues.append(
            Issue(
                id=f"iss_{len(self.issues) + 1:03d}",
                severity=severity,  # type: ignore[arg-type]
                category=category,  # type: ignore[arg-type]
                where=where,
                entity_ids=list(entity_ids),
                message=message,
                owner_agent=owner,  # type: ignore[arg-type]
                suggested_fix=fix,
            )
        )

    def error(self, category: str, owner: str, message: str, **kw) -> None:
        self.add("error", category, owner, message, **kw)

    def warning(self, category: str, owner: str, message: str, **kw) -> None:
        self.add("warning", category, owner, message, **kw)


def _library_ref(
    out: IssueList,
    manifest: Manifest,
    config: Config,
    owner: str,
    where: str,
    library_id: str | None,
    build_request_id: str | None,
    declared_requests: set[str],
    status: str,
) -> None:
    """Check 3: a library id must exist, or a build request must be declared."""
    mode = config.flags.library_mode
    if mode == "freeform":
        return
    if status == "library":
        if library_id not in manifest.all_library_ids():
            out.error(
                "missing_asset",
                owner,
                f"{where}: library_id {library_id} is not in the manifest",
                where=where,
                entity_ids=[library_id or ""],
                fix="Pick an id from the manifest or declare a build request",
            )
    elif status == "build_request":
        if mode == "strict":
            out.error(
                "missing_asset",
                owner,
                f"{where}: build requests are not allowed in library_mode 'strict'",
                where=where,
                entity_ids=[build_request_id or ""],
            )
        elif build_request_id not in declared_requests:
            out.error(
                "missing_reference",
                owner,
                f"{where}: build_request_id {build_request_id} is not declared",
                where=where,
                entity_ids=[build_request_id or ""],
            )


# ---------------------------------------------------------------- characters


def check_characters(
    bible: StoryBible, characters: Characters, manifest: Manifest, config: Config
) -> list[Issue]:
    out = IssueList()
    owner = "character_designer"
    cast = {c.id: c for c in bible.cast_index}
    declared = {b.id for b in characters.build_requests}
    seen: set[str] = set()

    for ch in characters.characters:
        seen.add(ch.id)
        if ch.id not in cast:
            out.error(
                "missing_reference", owner, f"{ch.id} is not in the story bible cast_index",
                where=ch.id, entity_ids=[ch.id],
            )
        for rel in ch.relationships:
            if rel.character_id not in cast:
                out.error(
                    "missing_reference", owner,
                    f"{ch.id}: relationship to unknown character {rel.character_id}",
                    where=ch.id, entity_ids=[rel.character_id],
                )
        a = ch.asset
        _library_ref(out, manifest, config, owner, ch.id, a.library_id, a.build_request_id, declared, a.status)
        if a.status == "parametric":
            base = manifest.base_by_id().get(a.base_id or "")
            if base is None:
                out.error(
                    "missing_asset", owner, f"{ch.id}: base {a.base_id} is not in the manifest",
                    where=ch.id, entity_ids=[a.base_id or ""],
                )
            else:
                _rig_compat(out, owner, ch.id, base.rig_type, base.shape_keys, base.outfit_slots, ch, a.rig_type)
                for slider in a.proportions:
                    if slider not in base.proportion_sliders:
                        out.error(
                            "clip_mismatch", owner, f"{ch.id}: base has no proportion slider {slider}",
                            where=ch.id, entity_ids=[a.base_id or ""],
                        )
        elif a.status == "library":
            lib = manifest.character_by_id().get(a.library_id or "")
            if lib is not None:
                _rig_compat(out, owner, ch.id, lib.rig_type, lib.shape_keys, lib.outfit_slots, ch, a.rig_type)

    for cid, entry in cast.items():
        if cid not in seen and entry.role != "background":
            out.error(
                "missing_reference", owner, f"{cid} ({entry.name}) has no character sheet",
                where=cid, entity_ids=[cid],
            )
    return out.issues


def _rig_compat(out, owner, where, rig_type, shape_keys, outfit_slots, ch, declared_rig) -> None:
    """Check 4: expressions use only the rig's shape keys, outfits only its slots."""
    if declared_rig != rig_type:
        out.error(
            "clip_mismatch", owner, f"{where}: rig_type {declared_rig} does not match asset rig {rig_type}",
            where=where,
        )
    for name, weights in ch.expressions.items():
        for key in weights:
            if key not in shape_keys:
                out.error(
                    "clip_mismatch", owner, f"{where}: expression {name} uses unknown shape key {key}",
                    where=where, entity_ids=[key],
                )
    for outfit in ch.outfits:
        for slot in outfit.slots:
            if slot not in outfit_slots:
                out.error(
                    "outfit", owner, f"{where}: outfit {outfit.id} uses unknown slot {slot}",
                    where=outfit.id, entity_ids=[outfit.id],
                )


# --------------------------------------------------------------------- world


def check_world(
    bible: StoryBible, characters: Characters, world: World, manifest: Manifest, config: Config
) -> list[Issue]:
    out = IssueList()
    owner = "scene_builder"
    declared = {b.id for b in world.build_requests}
    chars = characters.by_id()
    outfit_owner = characters.outfit_owner()
    bible_locs = bible.location_ids()
    world_locs = world.location_by_id()

    for loc in world.locations:
        if loc.id not in bible_locs:
            out.warning(
                "location", owner, f"{loc.id} is not in the story bible location_index (added by scene builder)",
                where=loc.id, entity_ids=[loc.id],
            )
        a = loc.asset
        _library_ref(out, manifest, config, owner, loc.id, a.library_id, a.build_request_id, declared, a.status)
        if a.status == "library":
            env = manifest.environment_by_id().get(a.library_id or "")
            if env is not None:
                for anc in loc.anchors:
                    if anc.library_anchor and anc.library_anchor not in env.anchors:
                        out.error(
                            "missing_asset", owner,
                            f"{loc.id}: anchor {anc.id} refers to unknown library anchor {anc.library_anchor}",
                            where=anc.id, entity_ids=[anc.id],
                        )
                if a.time_variant and env.time_variants and a.time_variant not in env.time_variants:
                    out.error(
                        "missing_asset", owner, f"{loc.id}: {a.library_id} has no time variant {a.time_variant}",
                        where=loc.id,
                    )
        elif a.status == "composed":
            kits = {k.id: k for k in manifest.kits}
            kit = kits.get(a.kit_id or "")
            if kit is None:
                out.error("missing_asset", owner, f"{loc.id}: kit {a.kit_id} is not in the manifest", where=loc.id)
            else:
                piece_ids = {p.id for p in kit.pieces}
                for p in a.pieces:
                    if p.piece_id not in piece_ids:
                        out.error(
                            "missing_asset", owner, f"{loc.id}: kit piece {p.piece_id} is not in {kit.id}",
                            where=loc.id, entity_ids=[p.piece_id],
                        )
            gens = {g.id for g in manifest.generators}
            for g in ([a.terrain] if a.terrain else []) + list(a.scatter):
                if g.generator_id not in gens:
                    out.error(
                        "missing_asset", owner, f"{loc.id}: generator {g.generator_id} is not in the manifest",
                        where=loc.id, entity_ids=[g.generator_id],
                    )
            if a.lighting_preset and manifest.lighting_presets and a.lighting_preset not in manifest.lighting_presets:
                out.error("missing_asset", owner, f"{loc.id}: unknown lighting preset {a.lighting_preset}", where=loc.id)

    for loc_id in bible_locs:
        if loc_id not in world_locs:
            out.error(
                "location", owner, f"story location {loc_id} has no world entry",
                where=loc_id, entity_ids=[loc_id],
            )

    for prop in world.props:
        if prop.owner_id and prop.owner_id not in chars:
            out.error(
                "missing_reference", owner, f"{prop.id}: owner {prop.owner_id} is not a character",
                where=prop.id, entity_ids=[prop.owner_id],
            )
        _library_ref(
            out, manifest, config, owner, prop.id, prop.asset.library_id,
            prop.asset.build_request_id, declared, prop.asset.status,
        )

    # Check 5: every beat in exactly one scene, in story order.
    beat_order = {b.id: b.order for b in bible.beats}
    placed: dict[str, str] = {}
    last_order = 0
    for sc in world.scenes:
        for bid in sc.beat_ids:
            if bid not in beat_order:
                out.error("missing_reference", owner, f"{sc.id}: unknown beat {bid}", where=sc.id, entity_ids=[bid])
                continue
            if bid in placed:
                out.error(
                    "beat_coverage", owner, f"{bid} appears in both {placed[bid]} and {sc.id}",
                    where=sc.id, entity_ids=[bid],
                )
            placed[bid] = sc.id
            if beat_order[bid] < last_order:
                out.error(
                    "beat_coverage", owner, f"{sc.id}: beat {bid} is out of story order",
                    where=sc.id, entity_ids=[bid],
                )
            last_order = max(last_order, beat_order[bid])
        for member in sc.cast:
            if member.character_id not in chars:
                out.error(
                    "missing_reference", owner, f"{sc.id}: cast member {member.character_id} has no sheet",
                    where=sc.id, entity_ids=[member.character_id],
                )
            elif outfit_owner.get(member.outfit_id) != member.character_id:
                out.error(
                    "outfit", owner,
                    f"{sc.id}: outfit {member.outfit_id} does not belong to {member.character_id}",
                    where=sc.id, entity_ids=[member.outfit_id],
                )
        # Beat characters should be in the scene cast.
        for bid in sc.beat_ids:
            beat = next((b for b in bible.beats if b.id == bid), None)
            if beat is None:
                continue
            missing = set(beat.character_ids) - sc.cast_ids()
            for cid in sorted(missing):
                out.warning(
                    "character_logic", owner, f"{sc.id}: {cid} is in beat {bid} but not in the scene cast",
                    where=sc.id, entity_ids=[cid],
                )
    for bid in beat_order:
        if bid not in placed:
            out.error("beat_coverage", owner, f"{bid} is not in any scene", where=bid, entity_ids=[bid])

    # Check 6: non-background characters appear somewhere.
    in_scenes = {c for sc in world.scenes for c in sc.cast_ids()}
    for entry in bible.cast_index:
        if entry.role != "background" and entry.id not in in_scenes:
            out.error(
                "unused_entity", owner, f"{entry.id} ({entry.role}) appears in no scene",
                where=entry.id, entity_ids=[entry.id],
            )

    # Check 7 (scene level): durations within tolerance of the target.
    total = sum(sc.estimated_duration_sec for sc in world.scenes)
    tol = config.flags.duration_tolerance
    if abs(total - config.target_duration_sec) > tol * config.target_duration_sec:
        out.error(
            "duration", owner,
            f"scenes sum to {total:.0f}s, target is {config.target_duration_sec:.0f}s (tolerance {tol:.0%})",
            fix="Adjust estimated_duration_sec across scenes",
        )
    return out.issues


# ----------------------------------------------------------------- direction


def check_direction(
    bible: StoryBible, characters: Characters, world: World, direction: Direction, config: Config
) -> list[Issue]:
    out = IssueList()
    owner = "director"
    scenes = world.scene_by_id()
    chars = characters.by_id()
    outfit_owner = characters.outfit_owner()
    props = world.prop_by_id()
    locs = world.location_by_id()
    beat_by_id = {b.id: b for b in bible.beats}

    for sd in direction.scenes:
        sc = scenes.get(sd.scene_id)
        if sc is None:
            out.error("missing_reference", owner, f"direction for unknown scene {sd.scene_id}", where=sd.scene_id)
            continue
        anchors = locs[sc.location_id].anchor_ids()
        if abs(sd.duration_sec - sc.estimated_duration_sec) > config.flags.duration_tolerance * max(sc.estimated_duration_sec, 1):
            out.warning(
                "duration", owner,
                f"{sd.scene_id}: directed {sd.duration_sec:.0f}s vs planned {sc.estimated_duration_sec:.0f}s",
                where=sd.scene_id,
            )
        for shot in sd.shots:
            if shot.beat_id not in sc.beat_ids:
                out.error(
                    "beat_coverage", owner, f"{shot.id}: beat {shot.beat_id} is not in {sc.id}",
                    where=shot.id, entity_ids=[shot.beat_id],
                )
            for c in shot.characters:
                if c.character_id not in chars:
                    out.error("missing_reference", owner, f"{shot.id}: unknown character {c.character_id}", where=shot.id)
                    continue
                if c.character_id not in sc.cast_ids():
                    out.error(
                        "character_logic", owner, f"{shot.id}: {c.character_id} is not in the cast of {sc.id}",
                        where=shot.id, entity_ids=[c.character_id],
                    )
                if outfit_owner.get(c.outfit_id) != c.character_id:
                    out.error(
                        "outfit", owner, f"{shot.id}: outfit {c.outfit_id} does not belong to {c.character_id}",
                        where=shot.id, entity_ids=[c.outfit_id],
                    )
                if c.expression not in chars[c.character_id].expressions:
                    out.error(
                        "missing_reference", owner,
                        f"{shot.id}: {c.character_id} has no expression '{c.expression}'",
                        where=shot.id, entity_ids=[c.character_id],
                    )
                for anc in (c.from_anchor_id, c.to_anchor_id):
                    if anc and anc not in anchors:
                        out.error(
                            "missing_reference", owner, f"{shot.id}: anchor {anc} is not in {sc.location_id}",
                            where=shot.id, entity_ids=[anc],
                        )
            for pid in shot.prop_ids:
                if pid not in props:
                    out.error("missing_reference", owner, f"{shot.id}: unknown prop {pid}", where=shot.id, entity_ids=[pid])

    # Every scene directed.
    directed = {sd.scene_id for sd in direction.scenes}
    for sid in scenes:
        if sid not in directed:
            out.error("beat_coverage", owner, f"{sid} has no direction", where=sid, entity_ids=[sid])

    # Check 9: verbatim story lines appear in some shot.
    if config.flags.strict_dialogue:
        shot_texts = [d.text.strip().lower() for s in direction.shots() for d in s.dialogue]
        for beat in bible.beats:
            for line in beat.dialogue:
                if line.source == "verbatim" and line.text.strip().lower() not in shot_texts:
                    out.error(
                        "dialogue", owner,
                        f"verbatim line from {beat.id} is missing: \"{line.text}\"",
                        where=beat.id, entity_ids=[line.speaker_id],
                        fix="Add the line to a shot in that beat, or mark it 'adapted' in the bible",
                    )
    return out.issues


# -------------------------------------------------------------------- layout


def _frustum_contains(cam_pos, look_at, point, focal_mm: float, sensor_mm: float, aspect: float) -> bool:
    """True if point is inside the camera's view pyramid (ignores near/far)."""
    fx = [l - c for l, c in zip(look_at, cam_pos)]
    norm = math.sqrt(sum(v * v for v in fx)) or 1.0
    fwd = [v / norm for v in fx]
    rel = [p - c for p, c in zip(point, cam_pos)]
    depth = sum(a * b for a, b in zip(rel, fwd))
    if depth <= 0:
        return False
    # Build right/up basis.
    up0 = (0.0, 0.0, 1.0)
    right = [
        fwd[1] * up0[2] - fwd[2] * up0[1],
        fwd[2] * up0[0] - fwd[0] * up0[2],
        fwd[0] * up0[1] - fwd[1] * up0[0],
    ]
    rn = math.sqrt(sum(v * v for v in right)) or 1.0
    right = [v / rn for v in right]
    up = [
        right[1] * fwd[2] - right[2] * fwd[1],
        right[2] * fwd[0] - right[0] * fwd[2],
        right[0] * fwd[1] - right[1] * fwd[0],
    ]
    x = sum(a * b for a, b in zip(rel, right))
    y = sum(a * b for a, b in zip(rel, up))
    half_w = depth * (sensor_mm / 2) / focal_mm
    half_h = half_w / aspect
    return abs(x) <= half_w and abs(y) <= half_h


def _aspect(config: Config) -> float:
    w, h = config.resolution
    return w / h


def check_layout(
    characters: Characters, world: World, direction: Direction, layout: Layout, manifest: Manifest, config: Config
) -> list[Issue]:
    out = IssueList()
    owner = "layout_td"
    shots = direction.shot_by_id()
    scenes = world.scene_by_id()
    locs = world.location_by_id()
    chars = characters.by_id()
    clips = manifest.clip_by_id()
    envs = manifest.environment_by_id()
    declared = {b.id for b in layout.build_requests}
    aspect = _aspect(config)

    if layout.fps != config.fps:
        out.error("timeline", owner, f"layout fps {layout.fps} differs from config fps {config.fps}")

    by_scene: dict[str, list[ShotLayout]] = {}
    for sl in layout.shots:
        by_scene.setdefault(sl.scene_id, []).append(sl)
        shot = shots.get(sl.shot_id)
        if shot is None:
            out.error("missing_reference", owner, f"layout for unknown shot {sl.shot_id}", where=sl.shot_id)
            continue
        sc = scenes[sl.scene_id]
        loc = locs[sc.location_id]
        expected = round(shot.duration_sec * config.fps)
        if abs(sl.frames - expected) > 2:
            out.error(
                "duration", owner,
                f"{sl.shot_id}: {sl.frames} frames but the director asked for {shot.duration_sec}s ({expected} frames)",
                where=sl.shot_id,
            )
        # Environment must match the location asset.
        if loc.asset.status == "library" and sl.environment.library_id != loc.asset.library_id:
            out.error(
                "location", owner,
                f"{sl.shot_id}: environment {sl.environment.library_id} but {loc.id} uses {loc.asset.library_id}",
                where=sl.shot_id,
            )
        if config.flags.library_mode != "freeform" and sl.environment.library_id not in envs:
            out.error(
                "missing_asset", owner, f"{sl.shot_id}: environment {sl.environment.library_id} is not in the manifest",
                where=sl.shot_id, entity_ids=[sl.environment.library_id],
            )
        bounds = envs[sl.environment.library_id].bounds_m if sl.environment.library_id in envs else None

        shot_chars = {c.character_id for c in shot.characters}
        layout_chars = {c.character_id for c in sl.characters}
        for cid in shot_chars - layout_chars:
            out.error("missing_reference", owner, f"{sl.shot_id}: {cid} is in the shot but not in the layout", where=sl.shot_id, entity_ids=[cid])
        for cid in layout_chars - shot_chars:
            out.error("character_logic", owner, f"{sl.shot_id}: {cid} is in the layout but not in the shot", where=sl.shot_id, entity_ids=[cid])

        if sl.camera.rig not in manifest.camera_rigs:
            out.error("missing_asset", owner, f"{sl.shot_id}: unknown camera rig {sl.camera.rig}", where=sl.shot_id)

        for lc in sl.characters:
            ch = chars.get(lc.character_id)
            if ch is None:
                continue
            rig = ch.asset.rig_type
            if lc.outfit_id not in ch.outfit_ids():
                out.error("outfit", owner, f"{sl.shot_id}: {lc.character_id} has no outfit {lc.outfit_id}", where=sl.shot_id)
            for e in lc.expression_keys:
                if e.expression not in ch.expressions:
                    out.error(
                        "missing_reference", owner, f"{sl.shot_id}: {lc.character_id} has no expression '{e.expression}'",
                        where=sl.shot_id,
                    )
            # Check 3 and 4 for clips.
            for use in lc.clips:
                clip = clips.get(use.clip_id)
                if clip is None:
                    if config.flags.library_mode != "freeform":
                        out.error(
                            "missing_asset", owner, f"{sl.shot_id}: clip {use.clip_id} is not in the manifest",
                            where=sl.shot_id, entity_ids=[use.clip_id],
                        )
                    continue
                if clip.rig_type != rig:
                    out.error(
                        "clip_mismatch", owner,
                        f"{sl.shot_id}: clip {use.clip_id} is for {clip.rig_type}, {lc.character_id} is {rig}",
                        where=sl.shot_id, entity_ids=[use.clip_id, lc.character_id],
                    )
                length = use.frame_end - use.frame_start + 1
                if not clip.loop and length > math.ceil(clip.frames / use.speed) + 1:
                    out.error(
                        "clip_mismatch", owner,
                        f"{sl.shot_id}: {use.clip_id} is {clip.frames} frames but used for {length}",
                        where=sl.shot_id, entity_ids=[use.clip_id],
                    )
            # Check 8: path inside bounds and inside the frustum at camera keyframes.
            if bounds:
                hx, hy, hz = bounds[0] / 2, bounds[1] / 2, bounds[2]
                for p in lc.path:
                    x, y, z = p.position_m
                    if abs(x) > hx or abs(y) > hy or z < -0.01 or z > hz:
                        out.error(
                            "out_of_frame", owner,
                            f"{sl.shot_id}: {lc.character_id} at frame {p.frame} is outside the environment bounds",
                            where=sl.shot_id, entity_ids=[lc.character_id],
                        )
            head = ch.asset.height_m * 0.9
            for k in sl.camera.keyframes:
                pos = _position_at(lc.path, k.frame)
                point = (pos[0], pos[1], pos[2] + head)
                if not _frustum_contains(k.position_m, k.look_at_m, point, k.focal_mm, sl.camera.sensor_mm, aspect):
                    out.warning(
                        "out_of_frame", owner,
                        f"{sl.shot_id}: {lc.character_id} is outside the camera frame at frame {k.frame}",
                        where=sl.shot_id, entity_ids=[lc.character_id],
                        fix="Move the camera keyframe or the character path",
                    )
            for att in lc.attachments:
                if att.prop_id not in {p.prop_id for p in sl.props}:
                    out.error("missing_reference", owner, f"{sl.shot_id}: attached prop {att.prop_id} is not in the shot props", where=sl.shot_id)
            for gap in sl.clip_gaps:
                if gap.build_request_id and gap.build_request_id not in declared:
                    out.error("missing_reference", owner, f"{sl.shot_id}: clip gap build request not declared", where=sl.shot_id)

        # Dialogue timing matches the director's lines.
        shot_lines = {d.line_id: d for d in shot.dialogue}
        timed = {d.line_id for d in sl.dialogue_timing}
        for lid in shot_lines.keys() - timed:
            out.error("dialogue", owner, f"{sl.shot_id}: line {lid} has no timing", where=sl.shot_id, entity_ids=[lid])
        for d in sl.dialogue_timing:
            if d.line_id not in shot_lines:
                out.error("dialogue", owner, f"{sl.shot_id}: timing for unknown line {d.line_id}", where=sl.shot_id)
            elif shot_lines[d.line_id].speaker_id != d.speaker_id:
                out.error("dialogue", owner, f"{sl.shot_id}: {d.line_id} speaker mismatch", where=sl.shot_id)

    # Check 7: frames contiguous within a scene, every shot laid out.
    laid = {sl.shot_id for sl in layout.shots}
    for sid, shot in shots.items():
        if sid not in laid:
            out.error("missing_reference", owner, f"{sid} has no layout", where=sid, entity_ids=[sid])
    for sid, sls in by_scene.items():
        ordered = sorted(sls, key=lambda s: s.frame_start)
        if ordered[0].frame_start != 1:
            out.error("timeline", owner, f"{sid}: first shot starts at frame {ordered[0].frame_start}, not 1", where=sid)
        for a, b in zip(ordered, ordered[1:]):
            if b.frame_start != a.frame_end + 1:
                out.error(
                    "timeline", owner,
                    f"{sid}: {b.shot_id} starts at {b.frame_start}, {a.shot_id} ends at {a.frame_end}",
                    where=sid, entity_ids=[a.shot_id, b.shot_id],
                )
    return out.issues


def _position_at(path, frame: int):
    """Linear interpolation of a character path at a frame."""
    if frame <= path[0].frame:
        return path[0].position_m
    if frame >= path[-1].frame:
        return path[-1].position_m
    for a, b in zip(path, path[1:]):
        if a.frame <= frame <= b.frame:
            t = (frame - a.frame) / max(b.frame - a.frame, 1)
            return tuple(pa + (pb - pa) * t for pa, pb in zip(a.position_m, b.position_m))
    return path[-1].position_m


def open_build_requests(*files) -> list[str]:
    ids: list[str] = []
    for f in files:
        for br in getattr(f, "build_requests", []) or []:
            if br.status not in ("accepted", "rejected"):
                ids.append(br.id)
    return ids
