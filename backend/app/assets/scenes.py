"""Scene service: manifests, their resolution against the registry, and the build and reopen
jobs' results (SRS 16.2, 16.3, Phase 5).

A scene version is created from a ``SceneManifest`` in one step: the pure checks
(``validate_manifest``), then the registry checks, then the pins are frozen as a
``ResolvedScene``. The registry checks, all reported at once:

* every pinned character, outfit and accessory version exists and is published. A
  deprecated version is allowed only where the version this one is based on already pinned
  exactly it (an existing scene keeps working; a new pin of a deprecated version is refused);
* the outfit on each character has an **approved** fit on that exact character version. A
  fit that is only *verified* needs a director's override (``asset.publish``), which is
  recorded on the version and in the audit log; a failed, revoked or missing fit is refused;
* each accessory is for the character's rig and style profiles, declares (or does not
  restrict) the character's body-fit profile, goes on the chosen attachment point, does not
  conflict with a slot the outfit fills (a beanie on an outfit with headwear), and no two
  accessories share an attachment point;
* the expressions exist on the character's rig profile;
* (Phase 7) the environment and every prop are published versions, and where things stand
  is checked against the environment version's layout (``check_layout``): marks and camera
  marks exist, characters start on free floor inside the set, props stand inside it, on the
  floor clear of solid dressing and of each other, or on a top. Instances placed at a mark
  get the mark's spot and facing in their resolved transform.

Nothing here changes a library asset. Scene state lives in ``scene_versions`` and in the
scene's own ``scene.blend``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from studio_contracts.accessory import get_accessory_def
from studio_contracts.animation import AnimationCompat, compatibility_issues
from studio_contracts.character import Issue
from studio_contracts.ids import uuid7
from studio_contracts.rig_profile import BUILTIN_RIG_PROFILES
from studio_contracts.scene import (
    Pin,
    PropFacts,
    ResolvedAccessoryUse,
    ResolvedClipUse,
    ResolvedDialogueLine,
    ResolvedInstance,
    ResolvedPin,
    ResolvedPropUse,
    ResolvedScene,
    SceneManifest,
    check_layout,
    is_library_clip,
    pin_path,
    prop_heights,
    resolve_clip,
    start_transform,
    validate_manifest,
)

from app.core.errors import ApiError
from app.models.accessories import AccessoryVersion
from app.models.animations import AnimationVersion
from app.models.assets import AssetVersion
from app.models.characters import CharacterVersion
from app.models.outfits import OutfitCompatibility, OutfitVersion
from app.models.scenes import (
    Scene,
    SceneInstance,
    SceneInstanceAccessory,
    SceneInstanceAnimation,
    SceneVersion,
)
from app.models.sets import EnvironmentVersion, PropVersion

USABLE = ("published", "deprecated")
PIN_FILES = {"character": ("character.blend", "character.json"), "outfit": ("outfit.blend",),
             "accessory": ("accessory.blend",), "animation": ("animation.blend",), "prop": ("prop.blend",),
             "environment": ("environment.blend",)}
MODELS = {"character": CharacterVersion, "outfit": OutfitVersion, "accessory": AccessoryVersion,
          "animation": AnimationVersion, "prop": PropVersion, "environment": EnvironmentVersion}


def now() -> datetime:
    return datetime.now(UTC)


def allocate_code(session: Session) -> str:
    """SCENE-001, SCENE-002, … from the same never-reused counter table as asset codes."""
    number = session.execute(text(
        "INSERT INTO asset_code_counters (kind, next_number) VALUES ('scene', 2) "
        "ON CONFLICT (kind) DO UPDATE SET next_number = asset_code_counters.next_number + 1 "
        "RETURNING next_number - 1")).scalar_one()
    return f"SCENE-{number:03d}"


# ---------------------------------------------------------------- resolution
@dataclass
class Resolution:
    manifest: SceneManifest
    issues: list[Issue] = field(default_factory=list)
    resolved: ResolvedScene | None = None
    #: per instance: (character version, outfit version, compatibility row, [(accessory version, point)],
    #: [(animation version, position in the instance's animations, start frame)])
    rows: list[tuple[Any, Any, Any, list[tuple[Any, str]], list[tuple[Any, int, int]]]] = field(default_factory=list)
    overrides: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.resolved is not None and not self.errors

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


def parse_manifest(raw: dict[str, Any]) -> SceneManifest:
    try:
        return SceneManifest.model_validate(raw)
    except ValidationError as exc:
        issues = [{"path": ".".join(str(p) for p in e["loc"]) or "$", "code": e["type"], "message": e["msg"]}
                  for e in exc.errors(include_url=False, include_context=False)]
        raise ApiError(422, "invalid_manifest", "the scene manifest is not valid", issues=issues) from None


def find_version(session: Session, kind: str, pin: Pin):
    major, minor = (int(x) for x in pin.version.split("."))
    model = MODELS[kind]
    return session.scalar(select(model).join(AssetVersion, AssetVersion.id == model.id)
                          .where(AssetVersion.kind == kind, AssetVersion.display_code == pin.code,
                                 AssetVersion.major == major, AssetVersion.minor == minor))


def _files(version) -> dict[str, Any]:
    return {f.storage_key.rsplit("/", 1)[-1]: f for f in version.entry.files}


def _pin(session: Session, kind: str, pin: Pin, path: str, issues: list[Issue], previous: set[uuid.UUID],
         name_of) -> tuple[Any, ResolvedPin | None]:
    v = find_version(session, kind, pin)
    if v is None:
        issues.append(Issue(path=path, code="not_found", message=f"no {kind} version {pin.ref}"))
        return None, None
    if v.status not in USABLE:
        issues.append(Issue(path=path, code="not_published", message=f"{pin.ref} is {v.status}; scenes pin "
                                                                      "published versions only"))
        return v, None
    if v.status == "deprecated" and v.id not in previous:
        issues.append(Issue(path=path, code="deprecated",
                            message=f"{pin.ref} is deprecated; only a scene that already pinned it may keep it"))
        return v, None
    files = _files(v)
    missing = [n for n in PIN_FILES[kind] if n not in files]
    if missing:
        issues.append(Issue(path=path, code="missing_files", message=f"{pin.ref} has no {', '.join(missing)}"))
        return v, None
    if v.status == "deprecated":
        issues.append(Issue(path=path, code="deprecated_kept", severity="warning",
                            message=f"{pin.ref} is deprecated; kept because the previous version pinned it"))
    return v, ResolvedPin(kind=kind, code=pin.code, version=pin.version, version_id=str(v.id), status=v.status,
                          name=name_of(v), path=pin_path(kind, pin.code, pin.version),
                          files={n: files[n].sha256 for n in PIN_FILES[kind]})


def previous_pins(base: SceneVersion | None) -> set[uuid.UUID]:
    if base is None:
        return set()
    rs = ResolvedScene.model_validate(base.resolved)
    return {uuid.UUID(p.version_id) for p in rs.pins()}


def resolve(session: Session, manifest: SceneManifest, *, base: SceneVersion | None = None,
            allow_verified_fits: bool = False) -> Resolution:
    """Every check at once; ``resolved`` is set only when there is no error."""
    res = Resolution(manifest)
    issues = res.issues
    issues += validate_manifest(manifest)
    previous = previous_pins(base)
    env_pin, layout, lighting, props = _resolve_set(session, manifest, issues, previous)
    instances = []
    for i, inst in enumerate(manifest.instances):
        p = f"instances[{i}]"
        cv, cpin = _pin(session, "character", inst.character, f"{p}.character", issues, previous,
                        lambda v: v.character.name)
        if cpin is None:
            continue
        rig = BUILTIN_RIG_PROFILES.get(cv.rig_profile_ref or "")
        if rig is None:
            issues.append(Issue(path=f"{p}.character", code="unknown_rig",
                                message=f"{inst.character.ref} has rig profile {cv.rig_profile_ref!r}, not a built-in one"))
            continue
        for j, e in enumerate(inst.expressions):
            if e.expression != "neutral" and e.expression not in rig.expressions:
                issues.append(Issue(path=f"{p}.expressions[{j}].expression", code="unknown_expression",
                                    message=f"{inst.character.ref}'s rig {rig.ref} has no expression {e.expression!r}"))

        ov = opin = compat = None
        compat_doc = None
        if inst.outfit is not None:
            ov, opin = _pin(session, "outfit", inst.outfit, f"{p}.outfit", issues, previous, lambda v: v.outfit.name)
            if opin is not None:
                compat = session.scalar(select(OutfitCompatibility).where(
                    OutfitCompatibility.outfit_version_id == ov.id, OutfitCompatibility.character_version_id == cv.id))
                st = compat.status if compat else None
                compat_doc = {"status": st, "compatibility_id": str(compat.id) if compat else None,
                              "fit_profile": compat.fit_profile if compat else None,
                              "checked_at": compat.checked_at.isoformat() if compat and compat.checked_at else None,
                              "override": False}
                if st == "verified" and allow_verified_fits:
                    compat_doc["override"] = True
                    res.overrides.append({"instance": inst.key, "outfit": inst.outfit.ref, "character": f"{cpin.code}@{cpin.version}",
                                          "compatibility_id": str(compat.id)})
                    issues.append(Issue(path=f"{p}.outfit", code="fit_not_approved", severity="warning",
                                        message=f"the fit of {inst.outfit.ref} on {inst.character.ref} is verified, "
                                                "not approved; accepted by a director's override"))
                elif st != "approved":
                    hint = {"verified": "it is verified but not approved; a reviewer can approve it, or a director "
                                        "can override for this scene",
                            None: "no fit check has been run for this pair"}.get(st, f"its fit check is {st}")
                    issues.append(Issue(path=f"{p}.outfit", code="fit_not_approved",
                                        message=f"{inst.outfit.ref} is not approved on {inst.character.ref}: {hint}"))

        accs: list[ResolvedAccessoryUse] = []
        acc_rows: list[tuple[Any, str]] = []
        points: dict[str, int] = {}
        for j, use in enumerate(inst.accessories):
            ap = f"{p}.accessories[{j}]"
            av, apin = _pin(session, "accessory", use.accessory, f"{ap}.accessory", issues, previous,
                            lambda v: v.accessory.name)
            if apin is None:
                continue
            ref = use.accessory.ref
            point = use.attach_point or av.attach_point
            bad = []
            if av.rig_profile_ref != cv.rig_profile_ref:
                who = inst.character.ref
                bad.append(("rig_mismatch", f"{ref} is for rig {av.rig_profile_ref}, {who} has {cv.rig_profile_ref}"))
            if av.style_profile_ref != cv.style_profile_ref:
                who = inst.character.ref
                bad.append(("style_mismatch",
                            f"{ref} is for style {av.style_profile_ref}, {who} has {cv.style_profile_ref}"))
            if av.fit_profiles and cv.fit_profile not in av.fit_profiles:
                bad.append(("fit_profile", f"{ref} is declared for {', '.join(av.fit_profiles)}, not {cv.fit_profile}"))
            if point not in (av.attach_points or []):
                bad.append(("attach_point", f"{ref} goes on {', '.join(av.attach_points)}, not {point}"))
            elif point not in rig.attachment_points:
                bad.append(("attach_point", f"rig {rig.ref} has no attachment point {point}"))
            if ov is not None:
                clash = sorted(set(av.conflicts_with_slots or []) & set(ov.slots or []))
                if clash:
                    bad.append(("slot_conflict", f"{ref} cannot be worn with {inst.outfit.ref}: both fill {', '.join(clash)}"))
            if point in points:
                bad.append(("point_taken", f"accessories[{points[point]}] is already on {point}"))
            points.setdefault(point, j)
            for code, msg in bad:
                issues.append(Issue(path=ap, code=code, message=msg))
            if bad:
                continue
            d = get_accessory_def(av.item_ref)
            accs.append(ResolvedAccessoryUse(pin=apin, attach_point=point, attach_bone=rig.attachment_points[point],
                                             item=d.ref, placement=d.placement))
            acc_rows.append((av, point))
        clips_l, anim_rows = _resolve_animations(session, inst, cv, rig, p, issues, previous, manifest)
        clips = tuple(clips_l)
        instances.append(ResolvedInstance(
            key=inst.key, character=cpin, rig_profile=rig.ref, fit_profile=cv.fit_profile,
            outfit=opin, outfit_compatibility=compat_doc, accessories=tuple(accs),
            transform=start_transform(inst, layout),
            expressions=tuple(inst.expressions), blink=inst.blink, animations=clips,
            dialogue=_resolve_dialogue(session, inst, p, issues, manifest), state=inst.state))
        res.rows.append((cv, ov, compat, acc_rows, anim_rows))
    if not res.errors:
        res.resolved = ResolvedScene(manifest_digest=manifest.digest(), name=manifest.name, fps=manifest.fps,
                                     frame_start=manifest.frame_start, frame_end=manifest.frame_end,
                                     camera=manifest.camera, instances=tuple(instances), environment=env_pin,
                                     layout=layout, props=tuple(props), lighting=lighting)
    return res


def _resolve_set(session: Session, manifest: SceneManifest, issues: list[Issue], previous: set[uuid.UUID]):
    """The environment and props: pins, their placement against the layout, and the
    resolved prop uses (standing height from the top they are on)."""
    env_pin = layout = lighting = None
    if manifest.environment is not None:
        ev, env_pin = _pin(session, "environment", manifest.environment, "environment", issues, previous,
                           lambda v: v.owner.name)
        if env_pin is not None:
            layout = ev.layout
            lighting = ev.lighting
    if manifest.lighting is not None:
        lighting = manifest.lighting
    facts: dict[str, PropFacts] = {}
    pins: dict[int, tuple[Any, ResolvedPin]] = {}
    for i, use in enumerate(manifest.props):
        pv, ppin = _pin(session, "prop", use.prop, f"props[{i}].prop", issues, previous, lambda v: v.owner.name)
        if ppin is None:
            continue
        pins[i] = (pv, ppin)
        facts[use.prop.ref] = PropFacts(size=tuple(pv.size), solid=pv.solid, rests_on=pv.rests_on, top_z=pv.top_z)
    if manifest.environment is None or env_pin is not None:
        issues += check_layout(manifest, layout, facts)
    heights = prop_heights(manifest, layout, facts)
    out: list[ResolvedPropUse] = []

    for i, use in enumerate(manifest.props):
        if i not in pins:
            continue
        pv, ppin = pins[i]
        out.append(ResolvedPropUse(key=use.key, pin=ppin, item=pv.item_ref, size=tuple(pv.size), at=use.at,
                                   z=heights[use.key], rotation_deg=use.rotation_deg, on=use.on))
    return env_pin, layout, lighting, out


def _resolve_animations(session: Session, inst, cv, rig, p: str, issues: list[Issue], previous: set[uuid.UUID],
                        manifest: SceneManifest) -> tuple[list[ResolvedClipUse], list[tuple[Any, int, int]]]:
    """Library animations are pinned like any asset and checked against this character's rig
    profile and fit profile with the compatibility record their build measured; stand-in
    catalog clips resolve from the catalog."""
    clips: list[ResolvedClipUse] = []
    rows: list[tuple[Any, int, int]] = []
    for j, a in enumerate(inst.animations):
        ap = f"{p}.animations[{j}]"
        if not is_library_clip(a.clip):
            try:
                clips.append(resolve_clip(a))
            except KeyError:
                pass  # reported by validate_manifest
            continue
        code, ver = a.clip.split("@")
        av, apin = _pin(session, "animation", Pin(code=code, version=ver), f"{ap}.clip", issues, previous,
                        lambda v: v.animation.name)
        if apin is None:
            continue
        if av.compat is None:
            issues.append(Issue(path=f"{ap}.clip", code="no_compatibility", message=f"{a.clip} has no compatibility record"))
            continue
        compat = AnimationCompat.model_validate(av.compat)
        problems = compatibility_issues(compat, rig.ref, cv.fit_profile)
        for pr in problems:
            issues.append(Issue(path=f"{ap}.clip", code="incompatible",
                                message=f"{a.clip} cannot play on {inst.character.ref}: {pr}"))
        if problems:
            continue
        length = compat.frames * a.repeat / a.speed * manifest.fps / 24
        if a.start_frame + length > manifest.frame_end + 1:
            issues.append(Issue(path=ap, code="runs_past_end", severity="warning",
                                message=f"{a.clip} plays to frame {a.start_frame + length:.0f}, after the scene ends "
                                        f"at {manifest.frame_end}; it is cut there"))
        clips.append(ResolvedClipUse(clip=a.clip, key=code.lower().replace("-", "_"), start_frame=a.start_frame,
                                     repeat=a.repeat, speed=a.speed, influence=a.influence, frames=compat.frames,
                                     loops=compat.loops, source="library", pin=apin,
                                     compat=compat.model_dump(mode="json"), travel=a.travel and compat.travels))
        rows.append((av, j, a.start_frame))
    return clips, rows


def _resolve_dialogue(session: Session, inst, p: str, issues: list[Issue],
                      manifest: SceneManifest) -> tuple[ResolvedDialogueLine, ...]:
    """A line with a recording freezes the clip's WAV hash and mouth cues; only a ready clip
    (analysed without error) can be used."""
    from app.assets import audio as audio_svc
    from app.models.audio import AudioClip

    out = []
    for j, d in enumerate(inst.dialogue):
        audio = None
        if d.audio is not None:
            dp = f"{p}.dialogue[{j}].audio"
            clip = session.scalar(select(AudioClip).where(AudioClip.code == d.audio))
            if clip is None:
                issues.append(Issue(path=dp, code="not_found", message=f"no dialogue clip {d.audio}"))
            elif clip.status != "ready":
                issues.append(Issue(path=dp, code="not_ready", message=f"{d.audio} is {clip.status}"
                                    + (f": {clip.error}" if clip.error else "")))
            else:
                audio = audio_svc.resolved(clip)
                frames = audio.duration_s * manifest.fps
                if d.start_frame + frames > d.end_frame + 1:
                    issues.append(Issue(path=f"{p}.dialogue[{j}]", code="audio_longer_than_line", severity="warning",
                                        message=f"{d.audio} lasts {audio.duration_s:.2f} s ({frames:.0f} frames); the "
                                                f"line ends at frame {d.end_frame}, the mouth keeps moving to the end "
                                                f"of the recording"))
                if d.start_frame + frames > manifest.frame_end + 1:
                    issues.append(Issue(path=f"{p}.dialogue[{j}]", code="runs_past_end", severity="warning",
                                        message=f"{d.audio} plays past the end of the scene; it is cut there"))
        out.append(ResolvedDialogueLine(start_frame=d.start_frame, end_frame=d.end_frame, text=d.text, audio=audio))
    return tuple(out)


def _refuse(res: Resolution) -> None:
    if not res.ok:
        raise ApiError(422, "invalid_manifest", "the scene manifest cannot be resolved",
                       issues=[i.model_dump() for i in res.issues])


# ---------------------------------------------------------------- identity and versions
def get_scene(session: Session, scene_id: uuid.UUID, *, lock: bool = False) -> Scene:
    q = select(Scene).where(Scene.id == scene_id)
    if lock:
        q = q.with_for_update(of=Scene)
    s = session.scalar(q)
    if s is None:
        raise ApiError(404, "scene_not_found", str(scene_id))
    return s


def get_version(session: Session, scene_id: uuid.UUID, version_id: uuid.UUID, *, lock: bool = False) -> SceneVersion:
    q = select(SceneVersion).where(SceneVersion.id == version_id, SceneVersion.scene_id == scene_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    v = session.scalar(q)
    if v is None:
        raise ApiError(404, "scene_version_not_found", str(version_id))
    return v


def create_scene(session: Session, *, name: str, description: str, tags: list[str], manifest: dict[str, Any],
                 notes: str, allow_verified_fits: bool, created_by: uuid.UUID | None) -> tuple[Scene, SceneVersion]:
    m = parse_manifest({**manifest, "name": manifest.get("name") or name})
    res = resolve(session, m, allow_verified_fits=allow_verified_fits)
    _refuse(res)
    scene = Scene(id=uuid7(), code=allocate_code(session), name=name, description=description,
                  tags=sorted(set(tags)), created_by=created_by)
    session.add(scene)
    session.flush()
    return scene, _insert_version(session, scene, res, notes=notes, created_by=created_by, base=None)


def new_version(session: Session, scene: Scene, *, manifest: dict[str, Any], based_on: SceneVersion | None,
                notes: str, allow_verified_fits: bool, created_by: uuid.UUID | None) -> SceneVersion:
    m = parse_manifest({**manifest, "name": manifest.get("name") or scene.name})
    res = resolve(session, m, base=based_on, allow_verified_fits=allow_verified_fits)
    _refuse(res)
    return _insert_version(session, scene, res, notes=notes, created_by=created_by, base=based_on)


def _insert_version(session: Session, scene: Scene, res: Resolution, *, notes: str, created_by: uuid.UUID | None,
                    base: SceneVersion | None) -> SceneVersion:
    number = 1 + max((v.number for v in scene.versions), default=0)
    v = SceneVersion(id=uuid7(), scene_id=scene.id, number=number, based_on_version_id=base.id if base else None,
                     manifest=res.manifest.model_dump(mode="json"), manifest_digest=res.manifest.digest(),
                     resolved=res.resolved.model_dump(mode="json"), overrides=res.overrides, notes=notes,
                     created_by=created_by, build_status="none", verify_status="none", build_files=[],
                     verify_files=[])
    session.add(v)
    session.flush()
    for pos, (inst, (cv, ov, compat, accs, anims)) in enumerate(zip(res.resolved.instances, res.rows, strict=True)):
        row = SceneInstance(id=uuid7(), scene_version_id=v.id, position=pos, key=inst.key, character_version_id=cv.id,
                            outfit_version_id=ov.id if ov else None,
                            outfit_compatibility_id=compat.id if compat else None,
                            compatibility_status=compat.status if compat else None,
                            clips=[c.clip for c in inst.animations])
        session.add(row)
        session.flush()
        for j, (av, point) in enumerate(accs):
            session.add(SceneInstanceAccessory(id=uuid7(), instance_id=row.id, position=j,
                                               accessory_version_id=av.id, attach_point=point))
        for anv, j, start in anims:
            session.add(SceneInstanceAnimation(id=uuid7(), instance_id=row.id, position=j,
                                               animation_version_id=anv.id, start_frame=start))
    session.flush()
    session.refresh(scene)
    return v


# ---------------------------------------------------------------- jobs
def check_pins_still_usable(session: Session, v: SceneVersion) -> None:
    """A pinned version cannot change or vanish (guards), but its files must still be there."""
    rs = ResolvedScene.model_validate(v.resolved)
    for p in rs.pins():
        ver = session.get(MODELS[p.kind], uuid.UUID(p.version_id))
        files = _files(ver) if ver else {}
        if ver is None or any(n not in files or files[n].sha256 != sha for n, sha in p.files.items()):
            raise ApiError(409, "pin_changed", f"{p.code}@{p.version}'s files are not the pinned ones")




def _job_dead(session: Session, job_id: uuid.UUID | None) -> bool:
    from studio_contracts.jobs import TERMINAL_STATUSES

    from app.models.jobs import Job

    # still queued on the version after the job ended: it failed, or ended without a result
    job = session.get(Job, job_id) if job_id else None
    return job is None or job.status in TERMINAL_STATUSES


def build_status(session: Session, v: SceneVersion) -> str:
    """``queued`` whose job has ended reads as ``failed``: it can be queued again."""
    if v.build_status == "queued" and _job_dead(session, v.assemble_job_id):
        return "failed"
    return v.build_status


def verify_status(session: Session, v: SceneVersion) -> str:
    if v.verify_status == "queued" and _job_dead(session, v.verify_job_id):
        return "failed"
    return v.verify_status


def begin_assemble(session: Session, v: SceneVersion, job_id: uuid.UUID) -> None:
    if build_status(session, v) not in ("none", "failed"):
        raise ApiError(409, "already_built" if v.build_status == "built" else "build_in_progress",
                       f"this version's build is {v.build_status}; a built scene never changes, make a new version",
                       build_status=v.build_status)
    v.build_status = "queued"
    v.assemble_job_id = job_id
    session.flush()


def begin_verify(session: Session, v: SceneVersion, job_id: uuid.UUID) -> None:
    if v.build_status != "built":
        raise ApiError(409, "not_built", "reopen needs a built scene.blend", build_status=v.build_status)
    if verify_status(session, v) == "queued":
        raise ApiError(409, "verify_in_progress", "a reopen check is already queued")
    v.verify_status = "queued"
    v.verify_job_id = job_id
    session.flush()


def scene_blend(v: SceneVersion) -> dict[str, Any] | None:
    return next((f for f in v.build_files or [] if f["role"] == "blend"), None)


def _keep(uploaded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"name": u["storage_key"].rsplit("/", 1)[-1],
             **{k: u[k] for k in ("role", "storage_key", "sha256", "size_bytes", "mime_type")}} for u in uploaded]


def record_build(session: Session, job_id: uuid.UUID, version_id: uuid.UUID, uploaded: list[dict[str, Any]],
                 data: dict[str, Any], blender_version: str | None) -> dict[str, Any]:
    v = session.get(SceneVersion, version_id, with_for_update=True, populate_existing=True)
    if v is None or v.assemble_job_id != job_id or v.build_status != "queued":
        return {"recorded": False, "reason": "the build was superseded"}
    report = data.get("report") or {}
    files = _keep(uploaded)
    blend = next((f for f in files if f["role"] == "blend"), None)
    good = bool(blend) and blend["sha256"] == data.get("scene_blend_sha256") and \
        report.get("manifest_digest") == v.manifest_digest
    v.build_status = "built" if good else "failed"
    v.scene_blend_sha256 = blend["sha256"] if blend else None
    v.build_report = report
    v.build_files = files
    v.blender_version = blender_version
    v.built_at = now()
    session.flush()
    return {"recorded": True, "scene_version_id": str(v.id), "build_status": v.build_status,
            "accessory_checks_ok": bool(data.get("ok"))}


def record_verify(session: Session, job_id: uuid.UUID, version_id: uuid.UUID, uploaded: list[dict[str, Any]],
                  data: dict[str, Any]) -> dict[str, Any]:
    v = session.get(SceneVersion, version_id, with_for_update=True, populate_existing=True)
    if v is None or v.verify_job_id != job_id or v.verify_status != "queued":
        return {"recorded": False, "reason": "the reopen check was superseded"}
    report = data.get("report") or {}
    v.verify_status = "verified" if data.get("ok") and report.get("scene_blend_sha256") == v.scene_blend_sha256 \
        else "failed"
    v.verify_report = report
    v.verify_files = _keep(uploaded)
    v.verified_at = now()
    session.flush()
    return {"recorded": True, "scene_version_id": str(v.id), "verify_status": v.verify_status}
