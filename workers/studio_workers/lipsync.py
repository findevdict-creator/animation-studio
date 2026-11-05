"""Dialogue audio for lip sync (SRS 30 Phase 6, architecture R10): normalise a recording and
find its mouth cues. Runs in the media worker; no Blender.

**Normalise.** ffmpeg decodes whatever was uploaded (WAV, MP3, OGG, FLAC) to 16-bit mono
PCM WAV at 16 kHz, the format Rhubarb reads, with silence padding left alone so cue times
match the original. Longer than ``MAX_AUDIO_SECONDS`` is refused.

**Analyse.** Two analysers, chosen by what the worker has:

* ``rhubarb``: Rhubarb Lip Sync (``STUDIO_RHUBARB_PATH``), with the transcript as its dialog
  file when given. Its Preston Blair shapes are mapped by ``cues_from_rhubarb``.
* ``phonetic`` (built in, used when no Rhubarb binary is configured): finds the voiced
  stretches of the recording from its loudness, then lays the transcript's visemes
  (spelling rules, below) across them in order, longer for vowels; when the transcript has
  as many phrases (split at punctuation) as the recording has pauses, each phrase goes in
  its own stretch. Without a transcript it
  opens the mouth by loudness. It knows nothing about phonemes; the timing comes from the
  audio and the shapes from the spelling. It is a fallback, recorded as such on the clip.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
from studio_contracts.audio import (  # noqa: F401
    MAX_AUDIO_SECONDS,
    LipSync,
    MouthCue,
    cues_from_rhubarb,
    viseme_at,
)

RATE = 16_000
WINDOW_S = 0.01
MIN_VOICED_S = 0.06
MERGE_GAP_S = 0.12
MIN_CUE_S = 0.04
PHONETIC = "studio-phonetic-1.0"


class AudioRefused(ValueError):
    """Permanent: the recording cannot be used (unreadable, too long, silent)."""


def normalise(src: Path, dst: Path, ffmpeg: str = "ffmpeg") -> float:
    """Decode ``src`` to 16 kHz 16-bit mono WAV at ``dst``; returns the duration in seconds."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
           "-map", "0:a:0", "-ac", "1", "-ar", str(RATE), "-sample_fmt", "s16", "-map_metadata", "-1",
           "-fflags", "+bitexact", "-flags:a", "+bitexact", "-f", "wav", str(dst)]
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if run.returncode != 0 or not dst.exists():
        raise AudioRefused(f"ffmpeg could not read the recording: {run.stderr.strip()[:500]}")
    with wave.open(str(dst)) as w:
        duration = w.getnframes() / float(w.getframerate())
    if duration <= 0:
        raise AudioRefused("the recording is empty")
    if duration > MAX_AUDIO_SECONDS:
        raise AudioRefused(f"the recording is {duration:.1f} s; a dialogue clip is at most {MAX_AUDIO_SECONDS:.0f} s")
    return round(duration, 4)


def read_samples(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


def loudness(samples: np.ndarray, rate: int = RATE) -> np.ndarray:
    """RMS per ``WINDOW_S`` window."""
    n = int(rate * WINDOW_S)
    frames = len(samples) // n
    if frames == 0:
        return np.zeros(0)
    return np.sqrt(np.mean(samples[: frames * n].reshape(frames, n) ** 2, axis=1))


def voiced_segments(rms: np.ndarray) -> list[tuple[float, float]]:
    """(start s, end s) of the stretches loud enough to be speech, short gaps closed."""
    if len(rms) == 0 or float(rms.max()) < 1e-4:
        return []
    floor = float(np.percentile(rms, 10))
    threshold = max(floor * 4.0, float(np.percentile(rms, 95)) * 0.12, 3e-3)
    on = rms > threshold
    segs: list[list[float]] = []
    for i, v in enumerate(on):
        t = i * WINDOW_S
        if v and (not segs or t - segs[-1][1] > MERGE_GAP_S):
            segs.append([t, t + WINDOW_S])
        elif v:
            segs[-1][1] = t + WINDOW_S
    return [(round(a, 3), round(b, 3)) for a, b in segs if b - a >= MIN_VOICED_S]


# spelling -> viseme, longest match first; letters not listed (s, t, d, k, g, n, r, h, ...)
# keep the mouth where the neighbouring sounds put it
RULES = (("th", "TH"), ("wh", "WQ"), ("qu", "WQ"), ("oo", "U"), ("ou", "O"), ("ow", "O"), ("ee", "E"), ("ea", "E"),
         ("ai", "AI"), ("ay", "AI"), ("ph", "FV"), ("m", "MBP"), ("b", "MBP"), ("p", "MBP"), ("f", "FV"), ("v", "FV"),
         ("w", "WQ"), ("l", "L"), ("a", "AI"), ("i", "AI"), ("e", "E"), ("y", "E"), ("o", "O"), ("u", "U"))
VOWELS = {"AI", "E", "O", "U"}


def text_visemes(text: str) -> list[list[str]]:
    """The visemes of each word, in order."""
    words = []
    for word in re.findall(r"[a-z']+", text.lower()):
        word = word.replace("'", "")
        out, i = [], 0
        while i < len(word):
            for spelling, vis in RULES:
                if word.startswith(spelling, i):
                    if not out or out[-1] != vis:
                        out.append(vis)
                    i += len(spelling)
                    break
            else:
                i += 1
        if out:
            words.append(out)
    return words


def _lay(tokens: list[str], segments: list[tuple[float, float]]) -> list[tuple[float, float, str]]:
    """Spread ``tokens`` over the voiced timeline in order, vowels twice as long."""
    weights = np.array([2.0 if t in VOWELS else 1.0 for t in tokens])
    total = sum(b - a for a, b in segments)
    edges = np.concatenate([[0.0], np.cumsum(weights) / weights.sum() * total])

    def at(x: float) -> float:  # position on the voiced timeline -> seconds
        for a, b in segments:
            if x <= b - a + 1e-9:
                return a + x
            x -= b - a
        return segments[-1][1]
    out = []
    for tok, x0, x1 in zip(tokens, edges[:-1], edges[1:], strict=True):
        # a token that straddles a pause is cut at it: the mouth rests during the gap
        for a, b in segments:
            lo, hi = max(at(x0), a), min(at(x1), b)
            if hi - lo > 1e-6:
                out.append((lo, hi, tok))
    return out


def _by_loudness(rms: np.ndarray, segments: list[tuple[float, float]]) -> list[tuple[float, float, str]]:
    """No transcript: open wide on loud windows, half open on quieter ones, in 80 ms beats."""
    out = []
    peak = float(rms.max()) or 1.0
    for a, b in segments:
        t = a
        while t < b - 1e-6:
            e = min(t + 0.08, b)
            i0, i1 = int(t / WINDOW_S), max(int(e / WINDOW_S), int(t / WINDOW_S) + 1)
            level = float(rms[i0:i1].mean()) / peak
            out.append((t, e, "AI" if level > 0.5 else "E" if level > 0.25 else "MBP"))
            t = e
    return out


def phonetic_cues(wav: Path, dialog_text: str | None) -> list[MouthCue]:
    rms = loudness(read_samples(wav))
    segments = voiced_segments(rms)
    if not segments:
        raise AudioRefused("no speech found in the recording (it is silent or too quiet)")
    phrases = [[v for w in text_visemes(p) for v in w] for p in re.split(r"[.!?,;:]+", dialog_text or "")]
    phrases = [p for p in phrases if p]
    if not phrases:
        spans = _by_loudness(rms, segments)
    elif len(phrases) == len(segments):
        # one pause per punctuation mark: each phrase goes in its own voiced stretch
        spans = [x for p, seg in zip(phrases, segments, strict=True) for x in _lay(p, [seg])]
    else:
        spans = _lay([v for p in phrases for v in p], segments)
    duration = len(rms) * WINDOW_S
    cues: list[list] = []
    t = 0.0
    for a, b, vis in spans:
        if a - t > 1e-6:
            cues.append([t, a, "rest"])
        if cues and cues[-1][2] == vis and abs(cues[-1][1] - a) < 1e-6:
            cues[-1][1] = b
        else:
            cues.append([a, b, vis])
        t = b
    if duration - t > 1e-6:
        cues.append([t, duration, "rest"])
    # fold cues too short to read into their neighbour
    merged: list[list] = []
    for c in cues:
        if merged and (c[1] - c[0] < MIN_CUE_S or merged[-1][2] == c[2]):
            merged[-1][1] = c[1]
        else:
            merged.append(c)
    return [MouthCue(start_s=round(a, 3), end_s=round(b, 3), viseme=v, shape=v) for a, b, v in merged]


def rhubarb_cues(wav: Path, dialog_text: str | None, binary: str, workdir: Path) -> tuple[list[MouthCue], str]:
    """Run Rhubarb on ``wav``; returns (cues, analyser name)."""
    out = workdir / "rhubarb.json"
    cmd = [binary, "--quiet", "-f", "json", "-o", str(out), "--recognizer", "pocketSphinx", "--machineReadable"]
    if dialog_text:
        dialog = workdir / "dialog.txt"
        dialog.write_text(dialog_text)
        cmd += ["-d", str(dialog)]
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    run = subprocess.run([*cmd, str(wav)], capture_output=True, text=True, timeout=600)
    if run.returncode != 0 or not out.exists():
        raise RuntimeError(f"rhubarb failed ({run.returncode}): {run.stderr.strip()[:500]}")
    name = "rhubarb-" + (version.split()[-1] if version else "unknown")
    return cues_from_rhubarb(json.loads(out.read_text())), name


def analyse(wav: Path, duration_s: float, dialog_text: str | None, workdir: Path,
            rhubarb: str | None = None) -> LipSync:
    """Mouth cues for a normalised recording, with Rhubarb when ``rhubarb`` names a binary."""
    text = (dialog_text or "").strip() or None
    if rhubarb and shutil.which(rhubarb):
        cues, name = rhubarb_cues(wav, text, rhubarb, workdir)
        return LipSync(analyser=name, recognizer="pocketSphinx", dialog_text=text, duration_s=duration_s,
                       cues=tuple(cues))
    return LipSync(analyser=PHONETIC, recognizer="phonetic", dialog_text=text, duration_s=duration_s,
                   cues=tuple(phonetic_cues(wav, text)))
