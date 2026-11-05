"""Speech synthesis behind a provider interface (SRS 17: provider-independent, voices kept).

A provider turns text and a ``VoiceProfile`` into a WAV file and names itself with its
version, which the clip records next to the exact voice settings, so the same line can be
explained (and spoken again the same way) later. The registry maps ``VoiceProfile.provider``
to an implementation; nothing else in the studio names a provider.

``espeak-ng`` is the built-in provider: offline, deterministic, many languages, and clearly
synthetic. A cloud voice (a hosted TTS API) plugs in as another ``VoiceProvider`` with its
key in the worker's settings; none is configured in this deployment.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from studio_contracts.episode import VoiceProfile


class SpeechRefused(ValueError):
    """The text or voice cannot be spoken (permanent)."""


class VoiceProvider(Protocol):
    name: str

    def version(self) -> str: ...

    def synthesize(self, text: str, voice: VoiceProfile, out: Path) -> Path: ...


class EspeakNG:
    name = "espeak-ng"

    def __init__(self, binary: str = "espeak-ng"):
        self.binary = binary

    def _bin(self) -> str:
        path = shutil.which(self.binary)
        if path is None:
            raise RuntimeError(f"{self.binary!r} is not installed on this worker")
        return path

    def version(self) -> str:
        out = subprocess.run([self._bin(), "--version"], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"text-to-speech:\s*([\d.]+)", out)
        return f"espeak-ng-{m.group(1) if m else 'unknown'}"

    def voices(self) -> set[str]:
        out = subprocess.run([self._bin(), "--voices"], capture_output=True, text=True, timeout=10).stdout
        return {line.split()[1] for line in out.splitlines()[1:] if len(line.split()) > 1}

    def synthesize(self, text: str, voice: VoiceProfile, out: Path) -> Path:
        text = " ".join(text.split())
        if not text:
            raise SpeechRefused("there is no text to speak")
        if voice.voice not in self.voices():
            raise SpeechRefused(f"espeak-ng has no voice {voice.voice!r}")
        name = voice.voice + (f"+{voice.variant}" if voice.variant else "")
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self._bin(), "-v", name, "-s", str(voice.rate_wpm), "-p", str(voice.pitch), "-w", str(out), "--", text]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if run.returncode != 0 or not out.exists() or out.stat().st_size <= 44:
            raise SpeechRefused(f"espeak-ng could not speak the line: {run.stderr.strip()[:300]}")
        return out


PROVIDERS: dict[str, type] = {"espeak-ng": EspeakNG}


def provider_for(voice: VoiceProfile, binaries: dict[str, str] | None = None) -> VoiceProvider:
    cls = PROVIDERS.get(voice.provider)
    if cls is None:
        raise SpeechRefused(f"no speech provider {voice.provider!r} on this worker (available: {sorted(PROVIDERS)})")
    binary = (binaries or {}).get(voice.provider)
    return cls(binary) if binary else cls()
