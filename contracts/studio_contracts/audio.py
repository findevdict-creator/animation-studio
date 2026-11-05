"""Dialogue audio and lip sync (SRS 30 Phase 6 "audio-based lip synchronisation",
architecture R10).

A dialogue clip (``AUDIO-001``) is an uploaded recording, normalised by the media worker to
16-bit mono WAV, plus the mouth cues found in it. Cues are timed in seconds and use the
rig profile's viseme names, so the face track keys them directly. The analyser's own shapes
are kept next to them for audit.

Rhubarb Lip Sync gives the Preston Blair shapes A to H and X. They are mapped onto the SRS
visemes as decided in R10: Rhubarb does not tell TH or W/Q apart, so ``vis_TH`` and
``vis_WQ`` stay for manual keys.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AUDIO_CODE_RE = r"^AUDIO-\d{3,}$"
MAX_AUDIO_SECONDS = 60.0
AUDIO_MIME = ("audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg", "audio/ogg", "audio/flac")

#: Preston Blair shape -> SRS viseme (R10)
RHUBARB_TO_VISEME: dict[str, str] = {"A": "MBP", "B": "E", "C": "E", "D": "AI", "E": "O", "F": "U", "G": "FV",
                                     "H": "L", "X": "rest"}


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MouthCue(_Frozen):
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    viseme: str  # SRS viseme name: AI, E, O, U, FV, L, MBP, WQ, TH, rest
    shape: str  # the analyser's own shape (Rhubarb A..H, X)


class LipSync(_Frozen):
    """What the lipsync job found in one recording."""

    analyser: str  # e.g. rhubarb-1.14.0 (pocketSphinx)
    recognizer: Literal["pocketSphinx", "phonetic"]
    #: the transcript given to the analyser, if any (it improves the phonemes it finds)
    dialog_text: str | None = None
    duration_s: float = Field(gt=0, le=MAX_AUDIO_SECONDS)
    cues: tuple[MouthCue, ...]


def cues_from_rhubarb(doc: dict) -> list[MouthCue]:
    """Rhubarb's ``-f json`` output to cues; unknown shapes are an error, not a guess."""
    out = []
    for c in doc.get("mouthCues", []):
        shape = str(c["value"])
        if shape not in RHUBARB_TO_VISEME:
            raise ValueError(f"unknown Rhubarb mouth shape {shape!r}")
        out.append(MouthCue(start_s=float(c["start"]), end_s=float(c["end"]), viseme=RHUBARB_TO_VISEME[shape],
                            shape=shape))
    return out


def viseme_at(cues: tuple[MouthCue, ...] | list[MouthCue], t: float) -> str:
    """The viseme showing at time ``t`` (``rest`` outside every cue)."""
    for c in cues:
        if c.start_s <= t < c.end_s:
            return c.viseme
    return "rest"
