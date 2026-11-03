"""Agent 1, Story Analyst: story_bible.json"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from studio.ids import BeatId, CharacterId, LocationId
from studio.schemas.common import Role, StudioModel, TimeOfDay, VersionedFile

StoryFunction = Literal[
    "setup",
    "inciting_incident",
    "rising_action",
    "climax",
    "falling_action",
    "resolution",
]
DialogueSource = Literal["verbatim", "adapted", "added"]


class CastEntry(StudioModel):
    id: CharacterId
    name: str
    aliases: list[str] = Field(default_factory=list)
    role: Role


class LocationEntry(StudioModel):
    id: LocationId
    name: str


class DialogueLine(StudioModel):
    speaker_id: CharacterId
    text: str = Field(min_length=1)
    source: DialogueSource = "verbatim"


class Beat(StudioModel):
    id: BeatId
    order: int = Field(ge=1)
    summary: str
    source_text: str = ""
    story_function: StoryFunction
    location_id: LocationId
    time_of_day: TimeOfDay
    character_ids: list[CharacterId] = Field(default_factory=list)
    emotional_tone: str = ""
    dialogue: list[DialogueLine] = Field(default_factory=list)


class Setting(StudioModel):
    era: str = ""
    world: str = ""


class StoryBible(VersionedFile):
    title: str
    logline: str
    synopsis: str
    genre: list[str] = Field(default_factory=list)
    tone: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    setting: Setting = Field(default_factory=Setting)
    cast_index: list[CastEntry]
    location_index: list[LocationEntry]
    beats: list[Beat] = Field(min_length=1)

    @model_validator(mode="after")
    def _internal_refs(self) -> "StoryBible":
        chars = {c.id for c in self.cast_index}
        locs = {l.id for l in self.location_index}
        if len(chars) != len(self.cast_index):
            raise ValueError("duplicate character ids in cast_index")
        if len(locs) != len(self.location_index):
            raise ValueError("duplicate location ids in location_index")
        orders = [b.order for b in self.beats]
        if orders != sorted(orders) or len(set(orders)) != len(orders):
            raise ValueError("beats must have unique, ascending order")
        for beat in self.beats:
            if beat.location_id not in locs:
                raise ValueError(f"{beat.id}: unknown location {beat.location_id}")
            for cid in beat.character_ids:
                if cid not in chars:
                    raise ValueError(f"{beat.id}: unknown character {cid}")
            for line in beat.dialogue:
                if line.speaker_id not in chars:
                    raise ValueError(f"{beat.id}: unknown speaker {line.speaker_id}")
        return self

    def character_ids(self) -> set[str]:
        return {c.id for c in self.cast_index}

    def location_ids(self) -> set[str]:
        return {l.id for l in self.location_index}

    def beat_ids(self) -> set[str]:
        return {b.id for b in self.beats}
