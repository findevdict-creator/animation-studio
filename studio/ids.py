"""ID formats shared by every pipeline file.

IDs are minted once and never renamed. Each kind has a fixed prefix so a
dangling reference can be reported with its kind.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import StringConstraints

SLUG = r"[a-z0-9]+(?:_[a-z0-9]+)*"

PATTERNS: dict[str, str] = {
    "character": rf"^char_{SLUG}$",
    "location": rf"^loc_{SLUG}$",
    "beat": r"^beat_\d{3}$",
    "outfit": rf"^out_{SLUG}$",
    "prop": rf"^prop_{SLUG}$",
    "anchor": rf"^anc_{SLUG}$",
    "scene": r"^sc_\d{2}$",
    "shot": r"^sh_\d{2}_\d{2}$",
    "line": r"^ln_\d{2}_\d{2}_\d{2}$",
    "build_request": rf"^br_(?:char|env|prop|clip|garm)_{SLUG}$",
    "library": rf"^lib_(?:char|prop|env)_{SLUG}$",
    "clip": rf"^clip_{SLUG}$",
    "issue": r"^iss_\d{3}$",
}

COMPILED = {kind: re.compile(p) for kind, p in PATTERNS.items()}


def _constr(kind: str):
    return Annotated[str, StringConstraints(pattern=PATTERNS[kind])]


CharacterId = _constr("character")
LocationId = _constr("location")
BeatId = _constr("beat")
OutfitId = _constr("outfit")
PropId = _constr("prop")
AnchorId = _constr("anchor")
SceneId = _constr("scene")
ShotId = _constr("shot")
LineId = _constr("line")
BuildRequestId = _constr("build_request")
LibraryId = _constr("library")
ClipId = _constr("clip")
IssueId = _constr("issue")

HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]


def kind_of(identifier: str) -> str | None:
    """Return the ID kind for an identifier, or None if it matches no pattern."""
    for kind, rx in COMPILED.items():
        if rx.match(identifier):
            return kind
    return None


def scene_number(scene_id: str) -> int:
    return int(scene_id.split("_")[1])


def shot_scene(shot_id: str) -> str:
    """sh_01_03 -> sc_01"""
    return f"sc_{shot_id.split('_')[1]}"


def line_shot(line_id: str) -> str:
    """ln_01_03_02 -> sh_01_03"""
    parts = line_id.split("_")
    return f"sh_{parts[1]}_{parts[2]}"
