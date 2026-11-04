"""The build queue (asset build phase 5): ranking by screen time, route selection, dedupe
and variants, and per-story metrics. Pure functions over the stage 1 outputs; the
coordinator runs ``plan_queue`` when it builds the package, and ``studio build-requests``
prints the queue and the metrics for a run."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from studio.schemas import Characters, Config, Layout, Manifest, World
from studio.schemas.build_requests import BuildRequest, BuildRoute

STOP = {"with", "that", "this", "from", "into", "than", "very", "small", "large", "which", "their", "have", "made"}
ROUTE_ORDER: tuple[BuildRoute, ...] = ("variant", "generator", "asset_builder", "image_to_3d", "human")
AUTOMATED: frozenset[str] = frozenset({"variant", "generator", "asset_builder", "image_to_3d"})


# ------------------------------------------------------------------ screen time
def screen_time(layout: Layout | None, world: World | None = None) -> dict[str, int]:
    """Frames on screen per character id, prop id, location id and clip-gap build request id."""
    frames: Counter[str] = Counter()
    if layout is None:
        return {}
    scene_location = {s.id: s.location_id for s in world.scenes} if world else {}
    for shot in layout.shots:
        length = shot.frame_end - shot.frame_start + 1
        for c in shot.characters:
            frames[c.character_id] += length
        for p in shot.props:
            frames[p.prop_id] += length
        loc = scene_location.get(shot.scene_id)
        if loc:
            frames[loc] += length
        for gap in shot.clip_gaps:
            if gap.build_request_id:
                frames[gap.build_request_id] += gap.frames[1] - gap.frames[0] + 1
    return dict(frames)


def rank(requests: list[BuildRequest], frames: dict[str, int], world: World | None = None) -> list[BuildRequest]:
    """Requests with screen_time_frames filled, most frames first; ties by id."""
    outfit_owner = {}
    ranked = []
    for br in requests:
        key = br.id if br.kind == "clip" else br.for_id
        n = frames.get(key, 0)
        if br.kind == "garment" and n == 0:
            n = max((frames.get(cid, 0) for cid in outfit_owner.get(br.for_id, [])), default=0)
        ranked.append(br.model_copy(update={"screen_time_frames": n}))
    return sorted(ranked, key=lambda b: (-b.screen_time_frames, b.id))


# ------------------------------------------------------------------ similarity
def spec_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3 and w not in STOP}


def request_tags(request: BuildRequest, manifest: Manifest | None = None) -> list[str]:
    tags = set(spec_words(request.spec))
    if manifest and request.nearest_library_id:
        near = _library_entry(manifest, request)
        if near is not None:
            tags |= set(getattr(near, "tags", []) or [])
    return sorted(tags)


def _library_entry(manifest: Manifest, request: BuildRequest):
    pools = {"prop": manifest.props, "character": manifest.characters, "environment": manifest.environments, "clip": manifest.clips}
    for entry in pools.get(request.kind, []):
        if entry.id == request.nearest_library_id:
            return entry
    return None


def variant_fit(request: BuildRequest, manifest: Manifest) -> tuple[float, list[str]]:
    """How well the nearest library asset could serve as a base for a variant, 0 to 1, and the
    changes a variant would need. Characters need the same rig type; props and environments
    need bounds within forty percent; a tag or spec-word overlap raises the fit."""
    near = _library_entry(manifest, request)
    if near is None:
        return 0.0, []
    changes = ["materials"]
    fit = 0.5
    if request.kind == "character":
        if request.rig_type and near.rig_type != request.rig_type:
            return 0.0, []
        if request.height_m and abs(near.height_m - request.height_m) / near.height_m > 0.25:
            return 0.2, ["proportions"]
        changes += ["proportions", "outfit"]
        fit = 0.7
    elif request.kind in ("prop", "environment"):
        if request.bounds_m and near.bounds_m:
            off = max(abs(a - b) / b for a, b in zip(request.bounds_m, near.bounds_m) if b)
            if off > 0.4:
                return 0.2, ["scale"]
            changes.append("scale")
            fit = 0.6 if off > 0.1 else 0.7
    elif request.kind == "clip":
        if request.rig_type and near.rig_type != request.rig_type:
            return 0.0, []
        changes = ["speed", "trim"]
        fit = 0.5
    overlap = spec_words(request.spec) & set(getattr(near, "tags", []) or [])
    fit = min(1.0, fit + 0.1 * len(overlap))
    return round(fit, 2), changes


def choose_route(request: BuildRequest, manifest: Manifest, config: Config, generators=None) -> tuple[BuildRoute, str]:
    """The first route in variant, generator, asset_builder, image_to_3d, human that the
    request qualifies for and ``flags.auto_routes`` allows. Characters and clips can only be
    variants or human work; the Asset Builder and image-to-3D take props."""
    allowed = set(config.flags.auto_routes)
    fit, changes = variant_fit(request, manifest)
    if "variant" in allowed and fit >= 0.6:
        return "variant", f"{request.nearest_library_id} fits {fit:.0%}; change {', '.join(changes)}"
    if request.kind in ("character", "clip", "garment"):
        return "human", f"{request.kind} requests end at a human when no library variant fits"
    if "generator" in allowed and generators is not None:
        entry = generators.match(request_tags(request, manifest), kind=request.kind)
        if entry is not None:
            return "generator", f"accepted script {entry['id']} matches the tags"
    if request.kind == "prop":
        if "asset_builder" in allowed:
            return "asset_builder", "prop with no close library match"
        if "image_to_3d" in allowed and request.reference_images:
            return "image_to_3d", "prop with reference images; image_to_3d enabled"
    if request.kind == "environment" and request.nearest_library_id and "variant" in allowed and fit > 0:
        return "variant", f"nearest environment {request.nearest_library_id}; needs {', '.join(changes)}"
    return "human", "no automated route qualifies"


# ------------------------------------------------------------------ dedupe
def dedupe(requests: list[BuildRequest], min_overlap: float = 0.6) -> dict[str, str]:
    """Map from a duplicate request id to the request it duplicates: same kind, same nearest
    library asset (or none) and spec words overlapping at least ``min_overlap`` (Jaccard).
    The earlier request in the given order is kept."""
    duplicates: dict[str, str] = {}
    kept: list[BuildRequest] = []
    for br in requests:
        words = spec_words(br.spec)
        match = None
        for k in kept:
            if k.kind != br.kind or k.nearest_library_id != br.nearest_library_id:
                continue
            kw = spec_words(k.spec)
            union = words | kw
            if not union:
                continue
            if len(words & kw) / len(union) >= min_overlap or br.spec.strip().lower() == k.spec.strip().lower():
                match = k
                break
        if match is not None:
            duplicates[br.id] = match.id
        else:
            kept.append(br)
    return duplicates


# ------------------------------------------------------------------ the plan
def plan_queue(
    requests: list[BuildRequest], layout: Layout | None, world: World | None, manifest: Manifest, config: Config, generators=None
) -> tuple[list[BuildRequest], list[dict[str, Any]]]:
    """Ranked requests with screen_time_frames and (for open requests without one) a route,
    plus one record per request explaining the choice. Requests that already carry a
    route, or that are decided, keep theirs."""
    ranked = rank(requests, screen_time(layout, world), world)
    duplicates = dedupe(ranked)
    planned: list[BuildRequest] = []
    records: list[dict[str, Any]] = []
    for br in ranked:
        reason = "route already set" if br.route else ""
        route = br.route
        if br.status in ("open", "in_progress") and route is None:
            route, reason = choose_route(br, manifest, config, generators)
        dup_of = duplicates.get(br.id)
        if dup_of and br.status == "open" and not br.route:
            route, reason = "variant", f"duplicate of {dup_of}; build once and vary"
        updated = br.model_copy(update={"route": route}) if route != br.route else br
        planned.append(updated)
        fit, changes = variant_fit(br, manifest)
        records.append(
            {
                "id": br.id, "kind": br.kind, "for_id": br.for_id, "screen_time_frames": updated.screen_time_frames,
                "route": updated.route, "status": updated.status, "reason": reason, "duplicate_of": dup_of,
                "variant_of": br.nearest_library_id if fit >= 0.6 else None, "variant_changes": changes if fit >= 0.6 else [],
            }
        )
    return planned, records


def apply_queue(characters: Characters, world: World, layout: Layout | None, planned: list[BuildRequest]) -> tuple[Characters, World, Layout | None]:
    """The owner files with their build_requests replaced by the planned versions."""
    by_id = {br.id: br for br in planned}

    def swap(model):
        if model is None:
            return None
        data = model.model_dump(mode="json")
        data["build_requests"] = [by_id[br["id"]].model_dump(mode="json") if br["id"] in by_id else br for br in data["build_requests"]]
        return type(model).model_validate(data)

    return swap(characters), swap(world), swap(layout)


# ------------------------------------------------------------------ metrics
def metrics(requests: list[BuildRequest]) -> dict[str, Any]:
    """Per story: requests opened, closed by code, closed by a human, still open, and the
    automated rounds spent, by kind and route."""
    by_kind = Counter(br.kind for br in requests)
    by_route = Counter(br.route or "unrouted" for br in requests)
    closed_code = [br for br in requests if br.status == "accepted" and br.decision and br.decision.by == "qa_agent"]
    closed_human = [br for br in requests if br.status in ("accepted", "rejected") and br.decision and br.decision.by == "human"]
    open_ = [br for br in requests if br.status in ("open", "in_progress")]
    review = [br for br in requests if br.status == "review"]
    rounds = [len(br.attempts) for br in requests if br.attempts]
    frames_code = sum(br.screen_time_frames for br in closed_code)
    frames_all = sum(br.screen_time_frames for br in requests)
    return {
        "opened": len(requests),
        "closed_by_code": len(closed_code),
        "closed_by_human": len(closed_human),
        "open": len(open_),
        "in_review": len(review),
        "automated_share": round(len(closed_code) / len(requests), 3) if requests else 1.0,
        "screen_time_share_closed_by_code": round(frames_code / frames_all, 3) if frames_all else 1.0,
        "avg_rounds": round(sum(rounds) / len(rounds), 2) if rounds else 0.0,
        "by_kind": dict(sorted(by_kind.items())),
        "by_route": dict(sorted(by_route.items())),
    }


def metrics_markdown(m: dict[str, Any], project_id: str = "") -> str:
    lines = [f"# Build requests{f' for {project_id}' if project_id else ''}", "", "| Metric | Value |", "|---|---|"]
    for key in ("opened", "closed_by_code", "closed_by_human", "open", "in_review", "automated_share", "screen_time_share_closed_by_code", "avg_rounds"):
        lines.append(f"| {key.replace('_', ' ')} | {m[key]} |")
    lines += ["", "| Kind | Requests |", "|---|---|"] + [f"| {k} | {v} |" for k, v in m["by_kind"].items()]
    lines += ["", "| Route | Requests |", "|---|---|"] + [f"| {k} | {v} |" for k, v in m["by_route"].items()]
    return "\n".join(lines) + "\n"


def queue_table(records: list[dict[str, Any]]) -> str:
    lines = [f"{'#':>3} {'request':<28} {'kind':<12} {'frames':>7} {'route':<14} {'status':<11} reason"]
    for n, r in enumerate(records, start=1):
        lines.append(f"{n:>3} {r['id']:<28} {r['kind']:<12} {r['screen_time_frames']:>7} {r['route'] or '-':<14} {r['status']:<11} {r['reason']}")
    return "\n".join(lines)
