"""Merge agent outputs into production_package.json."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from studio.checks import open_build_requests
from studio.schemas import (
    ApprovalRecord,
    Characters,
    Config,
    ContinuityReport,
    Direction,
    Layout,
    Manifest,
    ProductionPackage,
    StoryBible,
    World,
)
from studio.schemas.package import (
    PackageInput,
    PackageScene,
    PackageShot,
    PackageStats,
    PackageStory,
)


def build_package(
    project_id: str,
    config: Config,
    manifest: Manifest,
    bible: StoryBible,
    characters: Characters,
    world: World,
    direction: Direction,
    layout: Layout | None,
    report: ContinuityReport | None,
    approvals: list[ApprovalRecord],
    needs_review: bool = False,
) -> ProductionPackage:
    layouts = layout.shot_by_id() if layout else {}
    direction_scenes = {d.scene_id: d for d in direction.scenes}

    scenes: list[PackageScene] = []
    cursor = 0  # global frame cursor for scene ranges
    total_frames = 0
    for sc in world.scenes:
        d = direction_scenes[sc.id]
        shots = [PackageShot(direction=s, layout=layouts.get(s.id)) for s in d.shots]
        frame_start = frame_end = None
        if layout:
            local = [layouts[s.id] for s in d.shots if s.id in layouts]
            if local:
                length = max(l.frame_end for l in local)
                frame_start = cursor + 1
                frame_end = cursor + length
                cursor += length
                total_frames += length
        scenes.append(
            PackageScene(
                id=sc.id,
                order=sc.order,
                title=sc.title,
                beat_ids=sc.beat_ids,
                location_id=sc.location_id,
                time_of_day=sc.time_of_day,
                weather=sc.weather,
                cast=sc.cast,
                prop_ids=sc.prop_ids,
                summary=sc.summary,
                mood=sc.mood,
                frame_start=frame_start,
                frame_end=frame_end,
                shots=shots,
            )
        )
    if not layout:
        total_frames = round(sum(d.duration_sec for d in direction.scenes) * config.fps)

    build_requests = list(characters.build_requests) + list(world.build_requests)
    if layout:
        build_requests += list(layout.build_requests)
    open_ids = open_build_requests(characters, world, layout) if layout else open_build_requests(characters, world)

    if open_ids:
        status = "blocked_on_assets"
    elif needs_review or (report is not None and report.status == "fail"):
        status = "needs_review"
    else:
        status = "ready"

    all_shots = direction.shots()
    stats = PackageStats(
        characters=len(characters.characters),
        locations=len(world.locations),
        props=len(world.props),
        scenes=len(world.scenes),
        shots=len(all_shots),
        dialogue_lines=sum(len(s.dialogue) for s in all_shots),
        total_frames=total_frames,
        open_build_requests=len(open_ids),
    )
    return ProductionPackage(
        project_id=project_id,
        status=status,  # type: ignore[arg-type]
        created_at=datetime.now(timezone.utc),
        input=PackageInput(
            story_sha256=hashlib.sha256(config.story_text.encode()).hexdigest(),
            config=config.model_dump(mode="json", exclude={"story_text"}),
            library_version=manifest.library_version,
        ),
        story=PackageStory(
            title=bible.title,
            logline=bible.logline,
            synopsis=bible.synopsis,
            genre=bible.genre,
            tone=bible.tone,
            themes=bible.themes,
            setting=bible.setting,
            beats=bible.beats,
        ),
        style_guide=direction.style_guide,
        characters=characters.characters,
        locations=world.locations,
        props=world.props,
        scenes=scenes,
        build_requests=build_requests,
        approvals=approvals,
        continuity_report=report,
        stats=stats,
    )
