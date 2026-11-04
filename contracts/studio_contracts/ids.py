"""Identifiers shared by every module.

Primary keys are UUIDv7 (time ordered). Humans see display codes such as ``CHAR-001``,
allocated once per asset kind and never reused. Versions are ``major.minor``.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from enum import StrEnum
from functools import total_ordering
from typing import Annotated, Any

from pydantic import GetCoreSchemaHandler, StringConstraints
from pydantic_core import core_schema


def uuid7() -> uuid.UUID:
    """RFC 9562 UUID version 7: 48-bit Unix milliseconds, then random bits.

    Python 3.14 adds ``uuid.uuid7``; until the whole stack is on it, this is the one place
    that makes them.
    """
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76                       # version
    value |= ((rand >> 62) & 0xFFF) << 64    # rand_a, 12 bits
    value |= 0b10 << 62                      # variant
    value |= rand & ((1 << 62) - 1)          # rand_b, 62 bits
    return uuid.UUID(int=value)


class AssetKind(StrEnum):
    character = "character"
    outfit = "outfit"
    accessory = "accessory"
    material = "material"
    animation = "animation"
    environment = "environment"
    prop = "prop"
    style_profile = "style_profile"
    rig_profile = "rig_profile"
    template = "template"
    test_asset = "test_asset"  # Phase 1 smoke jobs only


CODE_PREFIX: dict[AssetKind, str] = {
    AssetKind.character: "CHAR",
    AssetKind.outfit: "OUTFIT",
    AssetKind.accessory: "ACC",
    AssetKind.material: "MAT",
    AssetKind.animation: "ANIM",
    AssetKind.environment: "ENV",
    AssetKind.prop: "PROP",
    AssetKind.style_profile: "STYLE",
    AssetKind.rig_profile: "RIG",
    AssetKind.template: "TPL",
    AssetKind.test_asset: "TEST",
}

CODE_RE = re.compile(r"^(?P<prefix>[A-Z]+)-(?P<number>\d{3,})$")
DisplayCode = Annotated[str, StringConstraints(pattern=CODE_RE.pattern)]


def display_code(kind: AssetKind, number: int) -> str:
    """``display_code(AssetKind.character, 1) == "CHAR-001"``"""
    if number < 1:
        raise ValueError("display code numbers start at 1")
    return f"{CODE_PREFIX[kind]}-{number:03d}"


def parse_display_code(code: str) -> tuple[AssetKind, int]:
    m = CODE_RE.match(code)
    if not m:
        raise ValueError(f"not a display code: {code!r}")
    for kind, prefix in CODE_PREFIX.items():
        if prefix == m["prefix"]:
            return kind, int(m["number"])
    raise ValueError(f"unknown display code prefix: {m['prefix']!r}")


@total_ordering
class VersionNumber:
    """``major.minor``, both non-negative integers, compared numerically (1.10 > 1.9)."""

    __slots__ = ("major", "minor")
    _RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)$")

    def __init__(self, major: int, minor: int):
        if major < 0 or minor < 0:
            raise ValueError("version parts must be non-negative")
        if (major, minor) == (0, 0):
            raise ValueError("0.0 is not a version")
        self.major = major
        self.minor = minor

    @classmethod
    def parse(cls, text: str) -> "VersionNumber":
        m = cls._RE.match(text)
        if not m:
            raise ValueError(f"not a major.minor version: {text!r}")
        return cls(int(m[1]), int(m[2]))

    def bump(self, part: str) -> "VersionNumber":
        if part == "major":
            return VersionNumber(self.major + 1, 0)
        if part == "minor":
            return VersionNumber(self.major, self.minor + 1)
        raise ValueError("bump part is 'major' or 'minor'")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"

    def __repr__(self) -> str:
        return f"VersionNumber({self.major}, {self.minor})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, VersionNumber) and (self.major, self.minor) == (other.major, other.minor)

    def __lt__(self, other: "VersionNumber") -> bool:
        return (self.major, self.minor) < (other.major, other.minor)

    def __hash__(self) -> int:
        return hash((self.major, self.minor))

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        def validate(value: Any) -> "VersionNumber":
            if isinstance(value, VersionNumber):
                return value
            if isinstance(value, str):
                return cls.parse(value)
            raise ValueError("version must be a 'major.minor' string")

        return core_schema.no_info_plain_validator_function(
            validate, serialization=core_schema.plain_serializer_function_ser_schema(str)
        )


FIRST_VERSION = VersionNumber(1, 0)
