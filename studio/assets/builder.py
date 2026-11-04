"""The Asset Builder QA loop and the generator library (asset build phase 4).

For a prop build request: the Asset Builder agent writes a script, the sandbox runs it into
``library/props/lib_prop_<slug>.blend``, the turntable renders a contact sheet under
``library/preview``, Render QA scores it, and the loop repeats with the notes up to
``flags.asset_builder_rounds`` times. A score at or above ``flags.qa_accept_score`` accepts
the request, promotes the prop in world.json and saves the script to the generator library
so the next similar request can reuse it. Anything else is parked for a human under
``library/preview/pending`` (which the scanner ignores) with every attempt recorded."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from studio.agents.asset_builder import BuildScript, default_params
from studio.agents.render_qa import QAVerdict
from studio.schemas import Config, Manifest, World
from studio.schemas.build_requests import BuildAttempt, BuildDecision, BuildRequest
from tools.asset_builder.sandbox import RunResult, run_script, static_check

SCRIPTS_DIR = Path("generators") / "scripts"
PENDING_DIR = Path("preview") / "pending"


class ScriptWriter(Protocol):
    def write_script(self, request: BuildRequest, manifest: Manifest, config: Config, previous=None, scripts=None) -> BuildScript: ...


class Reviewer(Protocol):
    def review(self, contact_sheet: Path, request: BuildRequest, metrics: dict[str, Any], config: Config) -> QAVerdict: ...


def library_id_for(request: BuildRequest) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", request.for_id.lower()).strip("_")
    slug = re.sub(r"^prop_", "", slug)
    return f"lib_prop_{slug}"


# ------------------------------------------------------------------ generator library
class GeneratorLibrary:
    """Accepted scripts with their params and tags, in library/generators/scripts/index.json."""

    def __init__(self, library: Path):
        self.library = Path(library)
        self.dir = self.library / SCRIPTS_DIR
        self.index_path = self.dir / "index.json"
        self.entries: list[dict[str, Any]] = json.loads(self.index_path.read_text()) if self.index_path.exists() else []

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self.entries, indent=2) + "\n")

    def add(self, request: BuildRequest, script_rel: str, params: dict[str, Any], tags: list[str], library_id: str, score: float) -> dict[str, Any]:
        entry = {
            "id": f"gen_script_{library_id.removeprefix('lib_prop_')}",
            "kind": request.kind,
            "script": script_rel,
            "params": params,
            "tags": sorted(set(tags)),
            "bounds_m": list(request.bounds_m) if request.bounds_m else None,
            "spec": request.spec,
            "from_request": request.id,
            "library_id": library_id,
            "qa_score": score,
        }
        self.entries = [e for e in self.entries if e["id"] != entry["id"]] + [entry]
        self.save()
        return entry

    def match(self, tags: list[str], kind: str = "prop", min_overlap: float = 0.5) -> dict[str, Any] | None:
        """The accepted script whose tags overlap the wanted tags most (Jaccard), or None."""
        want = set(tags)
        if not want:
            return None
        best, best_score = None, 0.0
        for e in self.entries:
            if e["kind"] != kind:
                continue
            have = set(e["tags"])
            score = len(want & have) / len(want | have) if want | have else 0.0
            if score > best_score:
                best, best_score = e, score
        return best if best_score >= min_overlap else None

    def script_text(self, entry: dict[str, Any]) -> str:
        return (self.library / entry["script"]).read_text()


# ------------------------------------------------------------------ stubs (offline)
BOX_SCRIPT = '''import bpy


def build(params):
    w, d, h = params.get("width", 0.3), params.get("depth", 0.3), params.get("height", 0.3)
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, h * 0.4))
    body = bpy.context.active_object
    body.name = "body"
    body.scale = (w, d, h * 0.8)
    bpy.ops.mesh.primitive_cylinder_add(radius=min(w, d) * 0.08, depth=h * 0.2, location=(0, 0, h * 0.9))
    handle = bpy.context.active_object
    handle.name = "handle"
    mat = bpy.data.materials.new("body_mat")
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.7, 0.5, 0.2, 1.0)
    body.data.materials.append(mat)
    handle.data.materials.append(mat)
'''


class StubAssetBuilder:
    """Writes a two-part box of the requested size; enough to exercise the loop offline."""

    name = "asset_builder"

    def __init__(self, script: str = BOX_SCRIPT):
        self.script = script
        self.calls = 0

    def write_script(self, request: BuildRequest, manifest: Manifest, config: Config, previous=None, scripts=None) -> BuildScript:
        self.calls += 1
        words = [w for w in re.findall(r"[a-z]+", request.spec.lower()) if len(w) > 3][:4]
        return BuildScript(script=self.script, params=default_params(request), tags=sorted(set(words + ["prop"])), summary="stub box")


class StubRenderQA:
    """Scores from the measured bounds alone: within 20 percent of the request scores 0.9."""

    name = "render_qa"

    def __init__(self, score: float | None = None):
        self.fixed = score

    def review(self, contact_sheet: Path, request: BuildRequest, metrics: dict[str, Any], config: Config) -> QAVerdict:
        if self.fixed is not None:
            return QAVerdict(score=self.fixed, notes="stub")
        want, got = request.bounds_m, metrics.get("bounds_m")
        if want and got:
            off = max(abs(g - w) / w for g, w in zip(got, want) if w)
            if off > 0.2:
                return QAVerdict(score=0.5, problems=[f"bounds off by {off:.0%}"], notes="stub bounds check")
        return QAVerdict(score=0.9, matches=["bounds"], notes="stub bounds check")


# ------------------------------------------------------------------ the loop
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _render_sheet(library: Path, blend: Path, library_id: str, frames: int = 8, size: int = 256) -> Path:
    from tools.turntable import contact_sheet, render_turntable

    preview = library / "preview"
    paths = render_turntable(blend, preview / library_id, frames, size)
    sheet = preview / f"{library_id}.png"
    contact_sheet(paths, sheet, size)
    return sheet


def build_prop(
    library: Path,
    request: BuildRequest,
    manifest: Manifest,
    config: Config,
    builder: ScriptWriter,
    qa: Reviewer,
    time_limit_s: float = 300.0,
    render: bool = True,
    attach_bone: str | None = None,
) -> tuple[BuildRequest, dict[str, Any]]:
    """Run the QA loop for one prop request. Returns the updated request and a report record."""
    library = Path(library).resolve()
    library_id = library_id_for(request)
    out_file = library / "props" / f"{library_id}.blend"
    scripts_dir = library / SCRIPTS_DIR
    scripts_dir.mkdir(parents=True, exist_ok=True)
    generators = GeneratorLibrary(library)
    attempts = list(request.attempts)
    scripts: dict[str, str] = {a.script: (library / a.script).read_text() for a in attempts if a.script and (library / a.script).exists()}
    record: dict[str, Any] = {"request_id": request.id, "library_id": library_id, "rounds": []}
    start_round = max((a.round for a in attempts), default=0)
    accepted = False
    for rnd in range(start_round + 1, start_round + 1 + config.flags.asset_builder_rounds):
        bs = builder.write_script(request, manifest, config, previous=attempts, scripts=scripts)
        script_rel = str(SCRIPTS_DIR / f"{request.id}_r{rnd}.py")
        (library / script_rel).write_text(bs.script)
        scripts[script_rel] = bs.script
        params = {**default_params(request), **bs.params}
        errors = static_check(bs.script)
        if errors:
            note = "static check failed: " + "; ".join(errors)
            attempts.append(BuildAttempt(route="asset_builder", round=rnd, script=script_rel, qa_score=0.0, qa_notes=note))
            record["rounds"].append({"round": rnd, "outcome": "static_check", "notes": note})
            continue
        scene_props = {
            "studio_kind": "prop",
            "studio_attach_bone": attach_bone or "",
            "studio_tags": ",".join(bs.tags),
            "studio_built_by": "asset_builder",
            "studio_build_request": request.id,
            "studio_params": json.dumps(params),
        }
        if not attach_bone:
            scene_props.pop("studio_attach_bone")
        result: RunResult = run_script(library, bs.script, params, out_file, scene_props, time_limit_s)
        if not result.ok:
            attempts.append(BuildAttempt(route="asset_builder", round=rnd, script=script_rel, qa_score=0.0, qa_notes=result.error))
            record["rounds"].append({"round": rnd, "outcome": "run_failed", "notes": result.error})
            continue
        metrics = result.metrics()
        sheet_rel = None
        if render:
            sheet = _render_sheet(library, out_file, library_id)
            sheet_rel = str(sheet.relative_to(library))
        else:
            sheet = library / "preview" / f"{library_id}.png"
            sheet.parent.mkdir(parents=True, exist_ok=True)
            sheet.write_bytes(b"\x89PNG\r\n\x1a\n")
            sheet_rel = str(sheet.relative_to(library))
        verdict = qa.review(sheet, request, metrics, config)
        attempts.append(BuildAttempt(route="asset_builder", round=rnd, script=script_rel, turntable=sheet_rel, qa_score=verdict.score, qa_notes=verdict.summary()))
        record["rounds"].append({"round": rnd, "outcome": "scored", "score": verdict.score, "metrics": metrics, "notes": verdict.summary()})
        if verdict.score >= config.flags.qa_accept_score:
            generators.add(request, script_rel, params, bs.tags, library_id, verdict.score)
            accepted = True
            break

    if accepted:
        updated = request.model_copy(
            update={
                "route": "asset_builder", "status": "accepted", "attempts": attempts, "result_library_id": library_id,
                "decision": BuildDecision(by="qa_agent", at=_now(), note=f"accepted at QA score {attempts[-1].qa_score:.2f} in round {attempts[-1].round}"),
            }
        )
        record.update(status="accepted", file=str(out_file.relative_to(library)), score=attempts[-1].qa_score)
    else:
        # Park the last build (if any) where the scanner does not look, for a human to judge.
        pending = library / PENDING_DIR
        if out_file.exists():
            pending.mkdir(parents=True, exist_ok=True)
            shutil.move(str(out_file), str(pending / out_file.name))
            record["pending_file"] = str((pending / out_file.name).relative_to(library))
        updated = request.model_copy(update={"route": "asset_builder", "status": "review", "attempts": attempts})
        record.update(status="review", score=max((a.qa_score or 0.0 for a in attempts), default=0.0))
    return BuildRequest.model_validate(updated.model_dump(mode="json")), record


def prop_requests(world: World, only: list[str] | None = None) -> list[BuildRequest]:
    return [
        br for br in world.build_requests
        if br.kind == "prop" and br.status in ("open", "in_progress", "review") and (not only or br.id in only)
    ]


def promote_props(world: World, results: dict[str, BuildRequest]) -> World:
    """world.json with the processed requests replaced and accepted props pointing at the library."""
    data = world.model_dump(mode="json")
    data["build_requests"] = [results.get(br["id"], BuildRequest.model_validate(br)).model_dump(mode="json") for br in data["build_requests"]]
    accepted = {br.id: br.result_library_id for br in results.values() if br.status == "accepted" and br.result_library_id}
    for prop in data["props"]:
        br_id = prop["asset"].get("build_request_id")
        if br_id in accepted:
            prop["asset"] = {"status": "library", "library_id": accepted[br_id], "build_request_id": br_id}
    return World.model_validate(data)


def build_props(
    library: Path, world: World, manifest: Manifest, config: Config, builder: ScriptWriter, qa: Reviewer,
    only: list[str] | None = None, time_limit_s: float = 300.0, render: bool = True,
) -> tuple[list[dict[str, Any]], World]:
    props = {p.asset.build_request_id: p for p in world.props if p.asset.build_request_id}
    results: dict[str, BuildRequest] = {}
    records = []
    for request in prop_requests(world, only):
        prop = props.get(request.id)
        attach_bone = "hand.R" if prop is not None and prop.owner_id else None
        updated, record = build_prop(library, request, manifest, config, builder, qa, time_limit_s, render, attach_bone)
        results[request.id] = updated
        records.append(record)
    return records, promote_props(world, results)
