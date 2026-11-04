"""Clip Tagger: a small vision pass that reads a clip's contact sheet and proposes tags from
the fixed vocabulary (asset build phase 2, auto-tagging). Optional: the name and motion
heuristics in tools/tag_clips.py run first; this fills in what a file name cannot say
(emotion, gesture detail). Output is filtered against the vocabulary, so the model can
never introduce a tag the Director and the Layout TD do not know."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, ValidationError

from studio.agents.base import ClaudeAgent
from studio.llm import ImagePart, LLMConfigError, LLMError
from studio.schemas.common import StudioModel
from tools.tag_clips import ALL_TAGS, VOCABULARY, merge_tags

SYSTEM_PROMPT = """You tag animation clips for an animation studio's library. You see a contact sheet: frames
of one clip in order, left to right, top to bottom, of a simple rigged figure. Describe the
motion with tags from the vocabulary only. Choose one kind tag, one speed tag when the
figure travels, a direction tag when it travels or turns, and up to four detail tags for
what the body does and the mood it reads as. Prefer fewer, surer tags. Return only the
structured output."""


class ClipTags(StudioModel):
    tags: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0, le=1)
    notes: str = ""


class ClipTagger(ClaudeAgent):
    name = "clip_tagger"
    output_model = ClipTags
    system_prompt = SYSTEM_PROMPT
    max_tokens = 1000

    def tag(self, contact_sheet: Path, clip_name: str, known_tags: list[str] | None = None) -> ClipTags:
        vocab = "\n".join(f"- {group}: {', '.join(tags)}" for group, tags in VOCABULARY.items())
        text = (
            f"Clip: {clip_name}\n\nVocabulary:\n{vocab}\n\n"
            + (f"Tags already known from the file name and root motion: {', '.join(known_tags)}\n\n" if known_tags else "")
            + "Tag this clip."
        )
        try:
            result = self.complete(text, ClipTags, images=[ImagePart(Path(contact_sheet).read_bytes())], effort="low")
        except LLMConfigError:
            raise
        except (LLMError, ValidationError) as exc:  # no vision on this provider, or JSON beyond repair
            return ClipTags(tags=known_tags or ["idle"], confidence=0.0, notes=f"no answer from the tagger: {exc}")
        if result.stop != "ok" or result.parsed is None:
            return ClipTags(tags=known_tags or ["idle"], confidence=0.0, notes="no answer from the tagger")
        answer = result.parsed
        clean = merge_tags(known_tags or [], [t for t in answer.tags if t in ALL_TAGS])
        return ClipTags(tags=clean or ["idle"], confidence=answer.confidence, notes=answer.notes)
