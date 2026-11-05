"""Episode export with FFmpeg (SRS 18.1 steps 7 to 10): PNG sequences -> H.264 video with the
shots' transitions, the dialogue, music and effects mixed to AAC, muxed into an MP4, and the
export metadata read back with ffprobe.

Pure file work on a workspace the media worker has filled from the render job's outputs
(``shots/<SH-id>/frame_NNNN.png``, ``audio/<AUDIO-id>.wav``); ``plan`` is
``app.assets.renders.export_plan``. Transitions: ``cut`` joins, ``fade`` fades the shot in
from black, ``dissolve`` cross-fades it over the end of the previous shot (the shot starts
earlier on the timeline by that much, as the episode version's ``start_s`` says).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

AUDIO_RATE = 48000


class ExportFailed(RuntimeError):
    pass


def _run(cmd: list[str], what: str, timeout: float = 1800) -> str:
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if run.returncode != 0:
        raise ExportFailed(f"{what}: {run.stderr.strip()[-1500:]}")
    return run.stdout


def video_graph(shots: list[dict[str, Any]], fps: int) -> tuple[str, float, list[dict[str, Any]]]:
    """The filtergraph joining the shot inputs ``[0:v]..[n:v]`` into ``[v]``; returns it, the
    total length, and where each shot actually starts (frames decide, not seconds)."""
    parts, timeline = [], []
    length = 0.0
    cur = None
    for i, sh in enumerate(shots):
        dur = sh["frame_count"] / fps
        tr = sh.get("transition") or {"kind": "cut", "duration_s": 0}
        d = float(tr.get("duration_s") or 0)
        label = f"s{i}"
        chain = f"[{i}:v]setpts=PTS-STARTPTS,fps={fps},format=yuv420p,settb=AVTB"
        if tr["kind"] == "fade" and d > 0:
            chain += f",fade=t=in:st=0:d={min(d, dur):.3f}"
        parts.append(f"{chain}[{label}]")
        if cur is None:
            cur, start = label, 0.0
        elif tr["kind"] == "dissolve" and d > 0:
            d = min(d, dur, length)
            start = length - d
            parts.append(f"[{cur}][{label}]xfade=transition=fade:duration={d:.3f}:offset={start:.3f}[j{i}]")
            cur = f"j{i}"
        else:
            start = length
            parts.append(f"[{cur}][{label}]concat=n=2:v=1:a=0[j{i}]")
            cur = f"j{i}"
        timeline.append({"shot_id": sh["shot_id"], "start_s": round(start, 4), "duration_s": round(dur, 4),
                         "transition": tr["kind"]})
        length = start + dur
    parts.append(f"[{cur}]null[v]")
    return ";".join(parts), round(length, 4), timeline


def audio_graph(cues: list[dict[str, Any]], first_input: int, length: float) -> str:
    """Each cue delayed to its start, trimmed, faded and scaled, mixed into ``[a]`` of
    exactly ``length`` seconds (silence where nothing plays)."""
    parts, labels = [], []
    for k, c in enumerate(cues):
        idx = first_input + k
        f = [f"aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo"]
        end = c.get("max_s")
        if end is not None and end > 0:
            f.append(f"atrim=0:{end:.3f}")
        if c.get("fade_in_s"):
            f.append(f"afade=t=in:st=0:d={c['fade_in_s']:.3f}")
        if c.get("fade_out_s"):
            play = min(end or c["duration_s"], c["duration_s"], max(0.0, length - c["start_s"]))
            f.append(f"afade=t=out:st={max(0.0, play - c['fade_out_s']):.3f}:d={c['fade_out_s']:.3f}")
        f.append(f"volume={c.get('volume', 1.0):.3f}")
        ms = int(round(c["start_s"] * 1000))
        f.append(f"adelay={ms}|{ms}")
        parts.append(f"[{idx}:a]{','.join(f)}[c{k}]")
        labels.append(f"[c{k}]")
    base = f"anullsrc=r={AUDIO_RATE}:cl=stereo,atrim=0:{length:.3f}[bed]"
    parts.append(base)
    parts.append(f"[bed]{''.join(labels)}amix=inputs={len(labels) + 1}:duration=first:dropout_transition=0:normalize=0,"
                 f"atrim=0:{length:.3f},asetpts=N/SR/TB[a]")
    return ";".join(parts)


def export(plan: dict[str, Any], ws: Path, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> dict[str, Any]:
    fps = plan["fps"]
    shots = []
    for sh in plan["shots"]:
        d = ws / "shots" / sh["shot_id"]
        frames = sorted(d.glob("frame_*.png"))
        if not frames:
            raise ExportFailed(f"{sh['shot_id']} has no frames in the workspace")
        shots.append({**sh, "dir": d, "first": int(frames[0].stem.split("_")[1]), "frame_count": len(frames)})
    vgraph, length, timeline = video_graph(shots, fps)
    cues = []
    for c in plan["audio"]:
        clip = plan["clips"][c["audio"]]
        if c["start_s"] >= length:
            continue
        cues.append({**c, "duration_s": clip["duration_s"], "path": ws / "audio" / f"{c['audio']}.wav"})
    agraph = audio_graph(cues, len(shots), length)
    out = ws / "out" / "episode.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for sh in shots:
        cmd += ["-framerate", str(fps), "-start_number", str(sh["first"]), "-i", str(sh["dir"] / "frame_%04d.png")]
    for c in cues:
        cmd += ["-i", str(c["path"])]
    cmd += ["-filter_complex", f"{vgraph};{agraph}", "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(plan["crf"]), "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", f"{plan['audio_bitrate_k']}k", "-ar", str(AUDIO_RATE), "-ac", "2",
            "-movflags", "+faststart", "-map_metadata", "-1", "-metadata", f"title={plan['title']}",
            "-metadata", f"comment={plan['render_job']}", "-t", f"{length:.3f}", str(out)]
    _run(cmd, "ffmpeg could not export the episode")
    probe = json.loads(_run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-count_frames",
                             "-of", "json", str(out)], "ffprobe could not read the episode", timeout=600))
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    a = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    meta = {"duration_s": round(float(probe["format"]["duration"]), 3), "expected_duration_s": length,
            "size_bytes": int(probe["format"]["size"]), "format": probe["format"]["format_name"],
            "video": {"codec": v["codec_name"], "profile": v.get("profile"), "width": v["width"], "height": v["height"],
                      "pix_fmt": v["pix_fmt"], "fps": round(num / den, 3), "frames": int(v.get("nb_read_frames") or 0)},
            "audio": {"codec": a["codec_name"], "sample_rate": int(a["sample_rate"]), "channels": a["channels"],
                      "bit_rate": int(a.get("bit_rate") or 0)} if a else None,
            "timeline": timeline, "cues": [{k: c[k] for k in ("audio", "role", "start_s", "volume")} for c in cues]}
    return {"video": out, "meta": meta}
