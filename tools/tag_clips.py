"""Clip tagging from names and motion (asset build phase 2).

Pure functions: ``tags_from_name`` reads the source file name against a fixed vocabulary,
``tags_from_motion`` adds speed and direction from the root motion, and ``merge_tags``
keeps the result inside the vocabulary the Director and the Layout TD share. The optional
LLM pass (``studio.agents.clip_tagger``) looks at a contact sheet and proposes tags from
the same vocabulary; it never invents new words.
"""

from __future__ import annotations

import math
import re

VOCABULARY: dict[str, list[str]] = {
    "kind": ["locomotion", "gesture", "idle", "emote", "action", "transition"],
    "speed": ["slow", "normal", "fast"],
    "direction": ["forward", "backward", "left", "right", "turn", "stationary"],
    "detail": [
        "walk", "run", "jog", "sneak", "sit", "stand", "stand_up", "sit_down", "wave", "point", "nod", "shake_head",
        "reach", "pick_up", "put_down", "throw", "jump", "crouch", "kneel", "lie", "look_around", "breathe",
        "talk", "laugh", "cry", "cheer", "shrug", "bow", "hug", "push", "pull", "climb", "fall", "dance",
        "cautious", "happy", "sad", "angry", "tired", "scared", "surprised",
    ],
}
ALL_TAGS = {t for group in VOCABULARY.values() for t in group}

NAME_RULES: list[tuple[str, list[str]]] = [
    (r"\bwalk", ["locomotion", "walk"]),
    (r"\b(run|sprint)", ["locomotion", "run", "fast"]),
    (r"\bjog", ["locomotion", "jog"]),
    (r"\b(sneak|creep|tiptoe)", ["locomotion", "sneak", "slow", "cautious"]),
    (r"\bcrouch", ["crouch"]),
    (r"\bidle|\bbreath", ["idle", "breathe"]),
    (r"\bstand_?up|\bget_?up|\brise", ["transition", "stand_up"]),
    (r"\bsit_?down", ["transition", "sit_down"]),
    (r"\bsit\b(?![ _]?down)|\bsitting", ["idle", "sit"]),
    (r"\bwave|\bwaving", ["gesture", "wave"]),
    (r"\bpoint", ["gesture", "point"]),
    (r"\bnod", ["gesture", "nod"]),
    (r"\bshake", ["gesture", "shake_head"]),
    (r"\breach|\bgrab", ["action", "reach"]),
    (r"\bpick", ["action", "pick_up"]),
    (r"\bput_?down|\bplace", ["action", "put_down"]),
    (r"\bthrow", ["action", "throw"]),
    (r"\bjump|\bhop", ["action", "jump"]),
    (r"\bkneel", ["kneel"]),
    (r"\blook", ["idle", "look_around"]),
    (r"\btalk|\bspeak", ["gesture", "talk"]),
    (r"\blaugh", ["emote", "laugh", "happy"]),
    (r"\bcry|\bsob", ["emote", "cry", "sad"]),
    (r"\bcheer|\bcelebrat", ["emote", "cheer", "happy"]),
    (r"\bshrug", ["gesture", "shrug"]),
    (r"\bbow", ["gesture", "bow"]),
    (r"\bhug", ["action", "hug"]),
    (r"\bpush", ["action", "push"]),
    (r"\bpull", ["action", "pull"]),
    (r"\bclimb", ["action", "climb"]),
    (r"\bfall|\btrip", ["action", "fall"]),
    (r"\bdanc", ["action", "dance"]),
    (r"\bcautious|\bwary|\bnervous", ["cautious"]),
    (r"\bhappy|\bjoy", ["happy"]),
    (r"\bsad", ["sad"]),
    (r"\bangry|\bmad", ["angry"]),
    (r"\btired|\bexhaust", ["tired"]),
    (r"\bscared|\bafraid|\bfear", ["scared"]),
    (r"\bsurpris|\bstartl", ["surprised"]),
    (r"\bturn", ["turn"]),
    (r"\bback(ward)?s?\b", ["backward"]),
    (r"\bleft\b", ["left"]),
    (r"\bright\b", ["right"]),
    (r"\bslow", ["slow"]),
    (r"\bfast|\bquick", ["fast"]),
]


def tags_from_name(name: str) -> list[str]:
    text = re.sub(r"[^a-z0-9]+", "_", name.lower())
    text = " " + text.replace("_", " ") + " "
    tags: list[str] = []
    for pattern, found in NAME_RULES:
        if re.search(pattern.replace(r"\b", r"(?<![a-z])"), text.replace(" ", "_")) or re.search(pattern, text):
            tags += found
    return tags


def tags_from_motion(root_motion_m: tuple[float, float, float], frames: int, fps: int = 24, loop: bool = False) -> list[str]:
    """Speed and direction from how far the root travels over the clip."""
    dx, dy, _ = root_motion_m
    dist = math.hypot(dx, dy)
    seconds = max(frames, 1) / fps
    speed = dist / seconds
    tags: list[str] = []
    if dist < 0.05:
        return ["stationary"]
    tags.append("locomotion")
    if speed < 0.8:
        tags.append("slow")
    elif speed < 1.8:
        tags.append("normal")
    else:
        tags.append("fast")
    angle = math.degrees(math.atan2(dx, dy))  # 0 = +Y forward
    if -45 <= angle <= 45:
        tags.append("forward")
    elif angle > 135 or angle < -135:
        tags.append("backward")
    elif angle > 0:
        tags.append("right")
    else:
        tags.append("left")
    return tags


def merge_tags(*groups: list[str]) -> list[str]:
    """Union in first-seen order, vocabulary only, one speed and one kind of 'walk/run'."""
    seen: list[str] = []
    for group in groups:
        for tag in group:
            if tag in ALL_TAGS and tag not in seen:
                seen.append(tag)
    speeds = [t for t in seen if t in VOCABULARY["speed"]]
    for extra in speeds[1:]:
        seen.remove(extra)
    if "stationary" in seen and "locomotion" in seen:
        seen.remove("stationary")
    return seen


def clean_tags(tags: list[str]) -> list[str]:
    return merge_tags(tags)
