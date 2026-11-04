"""Phase 2, the pure side: clip search for the Layout TD, procedural layers as a clip
source, and the transition table the assembler checks."""

from __future__ import annotations

from typing import Any

from studio.schemas import Config, Layout, Manifest
from studio.schemas.continuity import Issue
from studio.schemas.manifest import LibraryClip

# Actions that code animates instead of a clip (tools/procedural.py).
PROCEDURAL: dict[str, list[str]] = {
    "breathe": ["idle", "breathe"],
    "blink": ["blink"],
    "look_at": ["look_at", "look_around"],
}

# Pairs that need an intermediate clip between them (from -> to -> what to insert).
TRANSITIONS: dict[tuple[str, str], str] = {
    ("sit", "walk"): "stand_up",
    ("sit", "run"): "stand_up",
    ("lie", "walk"): "stand_up",
    ("walk", "sit"): "sit_down",
    ("run", "sit"): "sit_down",
    ("kneel", "run"): "stand_up",
}
POSTURES = ("sit", "lie", "kneel", "crouch", "walk", "run", "jog", "sneak")
KINDS = ("locomotion", "gesture", "idle", "emote", "action", "transition")


def score_clip(clip: LibraryClip, wanted: list[str]) -> float:
    """Tag overlap, with a bonus for the kind tag and a penalty for contradicting speed."""
    tags = set(clip.tags)
    want = set(wanted)
    if not want:
        return 0.0
    overlap = len(tags & want) / len(want)
    bonus = 0.2 if any(t in tags for t in want if t in KINDS) else 0.0
    speeds = {"slow", "normal", "fast"}
    penalty = 0.3 if (want & speeds) and (tags & speeds) and not (want & tags & speeds) else 0.0
    detail = want - set(KINDS)
    if detail and not (tags & detail):
        penalty += 0.5  # the right kind of clip doing the wrong thing is still a gap
    return round(max(0.0, overlap + bonus - penalty), 3)


def find_clip(manifest: Manifest, rig_type: str, wanted: list[str], config: Config | None = None, min_score: float = 0.34) -> dict[str, Any] | None:
    """The best clip of the rig type for the wanted tags, or a procedural layer when the
    tags describe one and ``flags.clip_sources`` allows it. None means a clip gap."""
    sources = list(config.flags.clip_sources) if config else ["baked", "procedural"]
    want = [t for t in wanted if t]
    if "procedural" in sources:
        for layer, tags in PROCEDURAL.items():
            if want and set(want) <= set(tags) | {"idle"} and any(t in tags for t in want):
                return {"source": "procedural", "layer": layer, "score": 1.0}
    if "baked" not in sources:
        return None
    best: tuple[float, LibraryClip] | None = None
    for clip in manifest.clips:
        if clip.rig_type != rig_type:
            continue
        s = score_clip(clip, want)
        if best is None or s > best[0] or (s == best[0] and clip.id < best[1].id):
            best = (s, clip)
    if best is None or best[0] < min_score:
        return None
    return {"source": "baked", "clip_id": best[1].id, "score": best[0], "loop": best[1].loop, "frames": best[1].frames}


def coverage(manifest: Manifest, rig_type: str, wanted_sets: list[list[str]], config: Config | None = None) -> dict[str, Any]:
    """How many of the Director's action_tags sets a clip or layer exists for."""
    hits, gaps = 0, []
    for wanted in wanted_sets:
        if find_clip(manifest, rig_type, wanted, config) is not None:
            hits += 1
        else:
            gaps.append(wanted)
    return {"total": len(wanted_sets), "covered": hits, "gaps": gaps}


def _posture(clip: LibraryClip) -> str | None:
    for tag in clip.tags:
        if tag in POSTURES:
            return tag
    return None


def check_transitions(layout: Layout, manifest: Manifest) -> list[Issue]:
    """Warnings for adjacent clips on one character that need an intermediate clip, and
    for blends shorter than four frames between different postures."""
    clips = manifest.clip_by_id()
    issues: list[Issue] = []
    n = 0
    for shot in layout.shots:
        for lc in shot.characters:
            uses = sorted(lc.clips, key=lambda u: u.frame_start)
            for a, b in zip(uses, uses[1:]):
                ca, cb = clips.get(a.clip_id), clips.get(b.clip_id)
                if ca is None or cb is None:
                    continue
                pa, pb = _posture(ca), _posture(cb)
                needed = TRANSITIONS.get((pa, pb)) if pa and pb else None
                if needed:
                    n += 1
                    issues.append(
                        Issue(
                            id=f"iss_{n:03d}", severity="warning", category="clip_mismatch", where=shot.shot_id,
                            entity_ids=[lc.character_id, a.clip_id, b.clip_id],
                            message=f"{shot.shot_id}: {lc.character_id} goes from {a.clip_id} ({pa}) to {b.clip_id} ({pb}) without a {needed} clip",
                            owner_agent="layout_td", suggested_fix=f"Insert a clip tagged '{needed}' between them",
                        )
                    )
                elif pa and pb and pa != pb and max(a.blend_frames, b.blend_frames) < 4:
                    n += 1
                    issues.append(
                        Issue(
                            id=f"iss_{n:03d}", severity="warning", category="clip_mismatch", where=shot.shot_id,
                            entity_ids=[lc.character_id, a.clip_id, b.clip_id],
                            message=f"{shot.shot_id}: {a.clip_id} to {b.clip_id} blends in {max(a.blend_frames, b.blend_frames)} frames; posture changes need 4 or more",
                            owner_agent="layout_td", suggested_fix="Raise blend_frames on one of the two clips",
                        )
                    )
    return issues
