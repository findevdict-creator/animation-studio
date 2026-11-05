"""Render jobs (SRS 18, Phase 8).

A render job renders a **locked** episode version: one ``render_scene`` job per shot on the
render queue (a Blender process each, never in the API), each opening the shot's stored
``scene.blend`` with its pinned files staged from the registry. When every shot's frames are
in, the last shot's outcome submits the ``export_episode`` job (media queue: FFmpeg), which
joins the PNG sequences with the transitions, mixes the dialogue, music and effects and
writes the MP4 and its metadata.

The render job records what it rendered: the episode version, the settings in force, the
asset versions of the closure, the Blender version, times, status, error and the outputs.
Its status follows its jobs: ``queued`` -> ``rendering`` -> ``exporting`` -> ``completed``,
or ``failed`` (a job failed; retry re-runs the failed jobs only) or ``cancelled``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from studio_contracts.episode import EpisodeManifest, RenderSettings
from studio_contracts.ids import uuid7
from studio_contracts.jobs import TERMINAL_STATUSES, JobStatus
from studio_contracts.scene import ResolvedScene

from app.assets import episodes as ep_svc
from app.assets import scenes as scene_svc
from app.assets.episodes import _code
from app.core.errors import ApiError
from app.jobs import service
from app.models.audio import AudioClip
from app.models.episodes import EpisodeVersion, RenderJob, RenderJobShot, RenderOutput
from app.models.jobs import Job
from app.models.scenes import SceneVersion

DONE = {"completed", "cancelled"}


def now() -> datetime:
    return datetime.now(UTC)


def get(session: Session, ref: str, *, lock: bool = False) -> RenderJob:
    try:
        rid = uuid.UUID(ref)
        q = select(RenderJob).where(RenderJob.id == rid)
    except ValueError:
        q = select(RenderJob).where(RenderJob.code == ref)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    rj = session.scalar(q)
    if rj is None:
        raise ApiError(404, "render_job_not_found", ref)
    return rj


def settings_for(v: EpisodeVersion, overrides: dict[str, Any]) -> RenderSettings:
    m = EpisodeManifest.model_validate(v.manifest)
    try:
        s = RenderSettings.model_validate({**m.render.model_dump(mode="json"), **overrides})
    except Exception as exc:  # pydantic ValidationError
        raise ApiError(422, "invalid_render_settings", str(exc)[:1000]) from None
    if s.fps != m.render.fps:
        raise ApiError(422, "fps_mismatch", f"the episode's scenes are {m.render.fps} fps; a render cannot change that")
    if s.width % 2 or s.height % 2:
        raise ApiError(422, "odd_size", "H.264 needs an even width and height")
    return s


def create(session: Session, v: EpisodeVersion, overrides: dict[str, Any],
           requested_by: uuid.UUID | None) -> tuple[RenderJob, list[Job]]:
    """The render job and its shot jobs, queued (the caller commits, then dispatches)."""
    if v.status != "locked":
        raise ApiError(409, "not_locked", "only a locked episode version can be rendered; lock it first")
    s = settings_for(v, overrides)
    shots = ep_svc.shot_versions(session, v)
    for _, sv in shots:
        if sv.build_status != "built" or scene_svc.scene_blend(sv) is None:
            raise ApiError(409, "not_built", f"scene version {sv.id} has no stored scene.blend")
        scene_svc.check_pins_still_usable(session, sv)
    rj = RenderJob(id=uuid7(), code=_code(session, "render", "RENDER"), episode_id=v.episode_id,
                   episode_version_id=v.id, status="queued", settings=s.model_dump(mode="json"),
                   asset_versions=[a.display for a in ep_svc.closure(session, v)], requested_by=requested_by)
    session.add(rj)
    session.flush()
    jobs = []
    for pos, (shot, sv) in enumerate(shots):
        params = {"render_job_id": str(rj.id), "scene_version_id": str(sv.id), "resolved": sv.resolved,
                  "scene_blend_sha256": sv.scene_blend_sha256, "settings": s.model_dump(mode="json")}
        job, _ = service.submit(session, "render_scene", params, requested_by=requested_by)
        session.add(RenderJobShot(id=uuid7(), render_job_id=rj.id, position=pos, shot_id=shot.shot_id,
                                  scene_version_id=sv.id, job_id=job.id))
        jobs.append(job)
    session.flush()
    session.refresh(rj)
    return rj, jobs


def jobs_of(session: Session, rj: RenderJob) -> list[Job]:
    ids = [s.job_id for s in rj.shots] + ([rj.export_job_id] if rj.export_job_id else [])
    rows = {j.id: j for j in session.scalars(select(Job).where(Job.id.in_(ids)))}
    return [rows[i] for i in ids]


def derive(session: Session, rj: RenderJob) -> str:
    """The status the jobs say the render job is in."""
    jobs = jobs_of(session, rj)
    statuses = [JobStatus(j.status) for j in jobs]
    if rj.cancel_requested:
        return "cancelled" if all(s in TERMINAL_STATUSES for s in statuses) else rj.status
    if any(s in (JobStatus.failed, JobStatus.timed_out, JobStatus.cancelled) for s in statuses):
        return "failed"
    if rj.export_job_id:
        export = jobs[-1]
        return "completed" if export.status == JobStatus.succeeded and rj.output_location else "exporting"
    if any(s != JobStatus.queued for s in statuses):
        return "rendering"
    return "queued"


def refresh(session: Session, rj: RenderJob) -> RenderJob:
    if rj.status in DONE:
        return rj
    st = derive(session, rj)
    if st != rj.status:
        bad = next((j for j in jobs_of(session, rj) if j.status in ("failed", "timed_out", "cancelled")), None) \
            if st == "failed" else None
        rj.status = st  # one UPDATE: a failed render no longer changes until it is retried
        if st != "queued" and rj.started_at is None:
            rj.started_at = now()
        if st in ("completed", "cancelled", "failed"):
            rj.finished_at = now()
        if bad is not None:
            rj.error = {"job_id": str(bad.id), "type": bad.type, **(bad.error or {})}
        session.flush()
    return rj


def progress(session: Session, rj: RenderJob) -> dict[str, Any]:
    """Overall progress: rendering is 90 %, weighted by each shot's frames; export the rest."""
    jobs = jobs_of(session, rj)
    shots = jobs[:len(rj.shots)]
    weights = [max(1, ResolvedScene.model_validate(session.get(SceneVersion, s.scene_version_id).resolved).frame_end)
               for s in rj.shots]
    done = sum(w * (1.0 if j.status == JobStatus.succeeded else (j.progress or 0.0))
               for w, j in zip(weights, shots, strict=True))
    value = 0.9 * done / max(sum(weights), 1)
    if rj.export_job_id:
        ex = jobs[-1]
        value += 0.1 * (1.0 if ex.status == JobStatus.succeeded else (ex.progress or 0.0))
    return {"value": round(min(1.0, value), 4), "frames": sum(weights),
            "shots_done": sum(1 for j in shots if j.status == JobStatus.succeeded), "shots": len(shots)}


def cancel(session: Session, rj: RenderJob) -> RenderJob:
    if rj.status in DONE:
        raise ApiError(409, "render_not_cancellable", f"the render is {rj.status}")
    rj.cancel_requested = True
    for j in jobs_of(session, rj):
        if j.status not in TERMINAL_STATUSES and j.status != JobStatus.cancel_requested:
            service.request_cancel(session, j.id)
    session.flush()
    return refresh(session, rj)


def retry(session: Session, rj: RenderJob) -> list[Job]:
    """Run the failed (or cancelled) jobs again; finished shots keep their frames."""
    refresh(session, rj)
    if rj.status != "failed":
        raise ApiError(409, "render_not_retryable", f"the render is {rj.status}; only a failed render is retried")
    again = [service.retry(session, j.id) for j in jobs_of(session, rj)
             if j.status in ("failed", "timed_out", "cancelled")]
    rj.error, rj.finished_at = None, None
    rj.status = "exporting" if rj.export_job_id else "rendering"
    session.flush()
    return again


# ---------------------------------------------------------------- outcomes (worker side)
def record_shot(session: Session, job_id: uuid.UUID, uploaded: list[dict[str, Any]], data: dict[str, Any],
                blender_version: str | None) -> dict[str, Any]:
    """A shot's frames onto the render job; the last shot in submits the export job (its id
    is returned under ``_dispatch`` for the worker to send once this commits)."""
    shot = session.scalar(select(RenderJobShot).where(RenderJobShot.job_id == job_id).with_for_update())
    if shot is None:
        return {"recorded": False, "reason": "no render job has this shot"}
    rj = get(session, str(shot.render_job_id), lock=True)
    frames = sorted((u for u in uploaded if u["role"] == "frame"), key=lambda u: u["path"])
    report = next((u for u in uploaded if u["role"] == "report"), None)
    if not frames:
        return {"recorded": False, "reason": "the job declared no frames"}
    prefix = frames[0]["storage_key"].rsplit("/", 1)[0] + "/"
    shot.frames = len(frames)
    session.add(RenderOutput(id=uuid7(), render_job_id=rj.id, kind="frames", scene_version_id=shot.scene_version_id,
                             shot_id=shot.shot_id, job_id=job_id, storage_key=prefix, frame_count=len(frames),
                             size_bytes=sum(f["size_bytes"] for f in frames),
                             meta={"frames": [{"path": f["path"], "storage_key": f["storage_key"], "sha256": f["sha256"]}
                                              for f in frames],
                                   "seconds_per_frame": data.get("seconds_per_frame"),
                                   "in_force": data.get("in_force"), "frame_start": data.get("frame_start"),
                                   "frame_end": data.get("frame_end")}))
    if report:
        session.add(RenderOutput(id=uuid7(), render_job_id=rj.id, kind="report", shot_id=shot.shot_id,
                                 scene_version_id=shot.scene_version_id, job_id=job_id,
                                 storage_key=report["storage_key"], sha256=report["sha256"],
                                 size_bytes=report["size_bytes"], meta={}))
    if blender_version and rj.blender_version is None:
        rj.blender_version = blender_version
    session.flush()
    out: dict[str, Any] = {"recorded": True, "render_job": rj.code, "shot": shot.shot_id, "frames": len(frames)}
    others = [s for s in rj.shots if s.id != shot.id]
    if not rj.cancel_requested and rj.export_job_id is None and all(s.frames for s in others):
        job, _ = service.submit(session, "export_episode", {"render_job_id": str(rj.id)},
                                requested_by=rj.requested_by)
        rj.export_job_id = job.id
        rj.status = "exporting"
        out["_dispatch"] = [[str(job.id), job.queue]]
        out["export_job_id"] = str(job.id)
    session.flush()
    return out


def export_plan(session: Session, rj: RenderJob) -> dict[str, Any]:
    """Everything the export needs, as plain data: each shot's frames (storage keys with
    SHA-256), where it starts on the timeline and how it comes in, and every audio clip with
    when it plays."""
    v = session.get(EpisodeVersion, rj.episode_version_id)
    m = EpisodeManifest.model_validate(v.manifest)
    outs = {o.shot_id: o for o in rj.outputs if o.kind == "frames"}
    s = RenderSettings.model_validate(rj.settings)
    shots, dialogue = [], []
    for es, sv in ep_svc.shot_versions(session, v):
        o = outs.get(es.shot_id)
        if o is None:
            raise ValueError(f"{es.shot_id} has no frames")
        shots.append({"shot_id": es.shot_id, "start_s": es.start_s, "duration_s": es.duration_s,
                      "transition": es.transition, "frames": o.meta["frames"]})
        rs = ResolvedScene.model_validate(sv.resolved)
        for inst in rs.instances:
            for d in inst.dialogue:
                if d.audio is None:
                    continue
                at = es.start_s + (d.start_frame - rs.frame_start) / rs.fps
                dialogue.append({"audio": d.audio.code, "start_s": round(at, 4), "shot_id": es.shot_id,
                                 "max_s": round(es.start_s + es.duration_s - at, 4), "volume": m.dialogue_volume,
                                 "fade_in_s": 0.0, "fade_out_s": 0.0})
    clips = {}
    for _, clip in ep_svc.audio_of(session, v):
        clips[clip.code] = {"storage_key": clip.wav_storage_key, "sha256": clip.wav_sha256,
                            "duration_s": clip.duration_s, "kind": clip.kind}
    cues = [{**c.model_dump(mode="json"), "role": role} for role, lst in (("music", m.music), ("sfx", m.sfx))
            for c in lst]
    for d in dialogue:
        d["role"] = "dialogue"
    end = max(sh["start_s"] + sh["duration_s"] for sh in shots)
    return {"render_job": rj.code, "episode_version_id": str(v.id), "title": m.title, "fps": s.fps,
            "width": s.width, "height": s.height, "crf": s.crf, "audio_bitrate_k": s.audio_bitrate_k,
            "duration_s": round(end, 4), "shots": shots, "audio": [*dialogue, *cues], "clips": clips,
            "settings": rj.settings, "asset_versions": rj.asset_versions, "blender_version": rj.blender_version}


def record_export(session: Session, job_id: uuid.UUID, rj_id: uuid.UUID, video: dict[str, Any],
                  meta: dict[str, Any], extra: list[dict[str, Any]]) -> dict[str, Any]:
    rj = get(session, str(rj_id), lock=True)
    if rj.export_job_id != job_id:
        return {"recorded": False, "reason": "not this render's export"}
    session.add(RenderOutput(id=uuid7(), render_job_id=rj.id, kind="video", job_id=job_id,
                             storage_key=video["storage_key"], sha256=video["sha256"], size_bytes=video["size_bytes"],
                             meta=meta))
    for e in extra:
        session.add(RenderOutput(id=uuid7(), render_job_id=rj.id, kind=e["kind"], job_id=job_id,
                                 storage_key=e["storage_key"], sha256=e["sha256"], size_bytes=e["size_bytes"], meta={}))
    rj.output_location = video["storage_key"]
    session.flush()
    return {"recorded": True, "render_job": rj.code, "video": video["storage_key"]}


def clip_by_code(session: Session, code: str) -> AudioClip | None:
    return session.scalar(select(AudioClip).where(AudioClip.code == code))
