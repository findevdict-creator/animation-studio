"""Command line: run a story, answer gates, show status.

    studio run --story story.txt --run-dir runs/lantern [--config config.json] [--agents stub]
    studio approve runs/lantern story_analyst
    studio edit runs/lantern story_analyst edited.json
    studio revise runs/lantern story_analyst "Mia should be older"
    studio status runs/lantern
    studio build-characters runs/lantern [--library library] [--toon]   (asset build phase 1; needs bpy)
    studio build-environments runs/lantern [--library library]          (asset build phase 3; needs bpy)
    studio gui [--runs-dir runs] [--library library] [--agents claude|stub] [--port 8765]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from studio.coordinator import AgentRegistry, AwaitingApproval, Coordinator, PipelineError, RunStore
from studio.coordinator.pipeline import decide, load_manifest
from studio.schemas import Config


def build_registry(kind: str) -> AgentRegistry:
    if kind == "stub":
        from studio.agents.stubs import stub_registry

        return stub_registry()
    if kind == "claude":
        from studio.agents import claude_registry

        return claude_registry()
    raise SystemExit(f"unknown agent set '{kind}'")


def cmd_run(args: argparse.Namespace) -> int:
    story = Path(args.story).read_text()
    if args.config:
        data = json.loads(Path(args.config).read_text())
        data["story_text"] = story
        config = Config.model_validate(data)
    else:
        config = Config(story_text=story, target_duration_sec=args.duration)
    run_dir = Path(args.run_dir)
    store = RunStore(run_dir)
    manifest = load_manifest(config.library_manifest_path)
    coordinator = Coordinator(config, manifest, build_registry(args.agents), store, project_id=run_dir.name)
    try:
        path = coordinator.run()
    except AwaitingApproval as exc:
        print(f"Paused: {exc.agent} needs approval. Review {exc.review_path} then run:")
        print(f"  studio approve {run_dir} {exc.agent}")
        return 2
    except PipelineError as exc:
        print(f"Failed: {exc}")
        for issue in exc.issues[:20]:
            print(f"  - [{issue.category}] {issue.message}")
        return 1
    print(f"Done: {path}")
    return 0


def cmd_decide(args: argparse.Namespace, decision: str) -> int:
    edited = json.loads(Path(args.file).read_text()) if getattr(args, "file", None) else None
    notes = getattr(args, "notes", "") or ""
    path = decide(args.run_dir, args.agent, decision, notes, edited)
    print(f"Recorded {decision} for {args.agent} at {path}. Run `studio run` again to continue.")
    return 0


def cmd_build_characters(args: argparse.Namespace) -> int:
    from studio.assets.characters import build_characters, plan_character_builds, write_build_report
    from studio.schemas import Characters

    store = RunStore(Path(args.run_dir))
    characters = store.load_output("character_designer")
    if not isinstance(characters, Characters):
        print("no approved character_designer output in this run", file=sys.stderr)
        return 1
    library = Path(args.library).resolve()
    manifest = load_manifest(library / "manifest.json")
    plan = plan_character_builds(characters, manifest)
    if not plan:
        print("no parametric characters to build")
        return 0
    missing = [p for p in plan if not p["base_exists"]]
    for p in missing:
        print(f"{p['character_id']}: base {p['base_id']} is not in the library", file=sys.stderr)
    if missing:
        return 1
    records, promoted = build_characters(library, characters, only=args.only or None, toon=args.toon)
    report = write_build_report(store.root, "character_builds", records)
    version = store.save_output("character_designer", promoted)
    for r in records:
        print(f"built {r['library_id']} from {r['base_id']} ({len(r['garments'])} garments, {len(r['warnings'])} warnings)")
    print(f"characters.json v{version} now points at the built assets; report: {report}")
    return 0


def cmd_build_environments(args: argparse.Namespace) -> int:
    from studio.assets.characters import write_build_report
    from studio.assets.environments import build_environments, plan_environment_builds
    from studio.schemas import World

    store = RunStore(Path(args.run_dir))
    world = store.load_output("scene_builder")
    if not isinstance(world, World):
        print("no approved scene_builder output in this run", file=sys.stderr)
        return 1
    library = Path(args.library).resolve()
    plan = plan_environment_builds(world, load_manifest(library / "manifest.json"))
    if not plan:
        print("no composed locations to build")
        return 0
    bad = [p for p in plan if not p["kit_exists"] or p["missing_pieces"] or p["missing_generators"]]
    for p in bad:
        print(f"{p['location_id']}: kit {p['kit_id']} exists={p['kit_exists']}, missing pieces {p['missing_pieces']}, missing generators {p['missing_generators']}", file=sys.stderr)
    if bad:
        return 1
    records, promoted = build_environments(library, world, only=args.only or None)
    report = write_build_report(store.root, "environment_builds", records)
    version = store.save_output("scene_builder", promoted)
    for r in records:
        print(f"composed {r['library_id']} from {r['kit_id']} ({r['pieces']} pieces, scatter {r['scatter']})")
    print(f"world.json v{version} now points at the built sets; report: {report}")
    return 0


def cmd_build_props(args: argparse.Namespace) -> int:
    from studio.assets.builder import StubAssetBuilder, StubRenderQA, build_props, prop_requests
    from studio.assets.characters import write_build_report
    from studio.schemas import World

    store = RunStore(Path(args.run_dir))
    world = store.load_output("scene_builder")
    if not isinstance(world, World):
        print("no approved scene_builder output in this run", file=sys.stderr)
        return 1
    config = store.load_config()
    library = Path(args.library).resolve()
    manifest = load_manifest(library / "manifest.json")
    if not prop_requests(world, args.only or None):
        print("no open prop build requests")
        return 0
    if args.agents == "stub":
        builder, qa = StubAssetBuilder(), StubRenderQA()
    else:
        from studio.agents.asset_builder import AssetBuilder
        from studio.agents.render_qa import RenderQA

        builder, qa = AssetBuilder(), RenderQA()
    records, promoted = build_props(library, world, manifest, config, builder, qa, only=args.only or None, time_limit_s=args.time_limit, render=not args.no_render)
    report = write_build_report(store.root, "prop_builds", records)
    version = store.save_output("scene_builder", promoted)
    for r in records:
        print(f"{r['request_id']}: {r['status']} (best QA score {r.get('score', 0):.2f}, {len(r['rounds'])} rounds)" + (f" -> {r['file']}" if r.get("file") else ""))
    print(f"world.json v{version} updated; report: {report}")
    return 0


def cmd_build_requests(args: argparse.Namespace) -> int:
    from studio.assets.builder import GeneratorLibrary
    from studio.assets.characters import write_build_report
    from studio.assets.queue import apply_queue, metrics, metrics_markdown, plan_queue, queue_table
    from studio.schemas import Characters, Layout, World

    store = RunStore(Path(args.run_dir))
    outputs = store.load_all_outputs()
    chars, world, layout = outputs.get("character_designer"), outputs.get("scene_builder"), outputs.get("layout_td")
    if not isinstance(chars, Characters) or not isinstance(world, World):
        print("this run has no character_designer and scene_builder outputs yet", file=sys.stderr)
        return 1
    layout = layout if isinstance(layout, Layout) else None
    config = store.load_config()
    library = Path(args.library).resolve()
    manifest = load_manifest(library / "manifest.json")
    generators = GeneratorLibrary(library) if (library / "generators").exists() else None
    requests = list(chars.build_requests) + list(world.build_requests) + (list(layout.build_requests) if layout else [])
    planned, records = plan_queue(requests, layout, world, manifest, config, generators)
    m = metrics(planned)
    write_build_report(store.root, "build_queue", records)
    (store.root / "assets" / "metrics.json").write_text(json.dumps(m, indent=2) + "\n")
    (store.root / "assets" / "metrics.md").write_text(metrics_markdown(m, store.root.name))
    if args.save:
        c2, w2, l2 = apply_queue(chars, world, layout, planned)
        store.save_output("character_designer", c2)
        store.save_output("scene_builder", w2)
        if l2 is not None:
            store.save_output("layout_td", l2)
    if args.json:
        print(json.dumps({"queue": records, "metrics": m}, indent=2))
    else:
        print(queue_table(records) if records else "no build requests")
        print()
        print(metrics_markdown(m, store.root.name))
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from studio.gui import StudioService, serve

    allowed = tuple(a for a in args.allow_agents.split(",") if a) if args.allow_agents else ("claude", "stub")
    unknown = [a for a in allowed if a not in ("claude", "stub")]
    if unknown:
        raise SystemExit(f"unknown agent set(s) in --allow-agents: {', '.join(unknown)}")
    if args.agents not in allowed:
        allowed = (args.agents, *allowed)
    service = StudioService(
        runs_dir=args.runs_dir, library=args.library, agents=args.agents,
        allowed_agents=allowed, auto_resume=not args.no_auto_resume,
    )
    serve(service, host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = RunStore(Path(args.run_dir))
    state = store.load_state()
    print(json.dumps(state, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="studio")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run or resume a story")
    run.add_argument("--story", required=True)
    run.add_argument("--run-dir", required=True)
    run.add_argument("--config", help="config.json (story_text is filled from --story)")
    run.add_argument("--duration", type=float, default=120.0, help="target seconds when no config is given")
    run.add_argument("--agents", default="claude", choices=["claude", "stub"])

    for name in ("approve", "revise", "edit"):
        p = sub.add_parser(name, help=f"{name} an agent's output at an open gate")
        p.add_argument("run_dir")
        p.add_argument("agent")
        if name == "revise":
            p.add_argument("notes")
        if name == "edit":
            p.add_argument("file", help="corrected JSON payload")

    status = sub.add_parser("status")
    status.add_argument("run_dir")

    build = sub.add_parser("build-characters", help="build parametric characters into library assets (needs bpy)")
    build.add_argument("run_dir")
    build.add_argument("--library", default="library")
    build.add_argument("--only", action="append", default=[])
    build.add_argument("--toon", action="store_true")

    benv = sub.add_parser("build-environments", help="compose kit-based locations into library environments (needs bpy)")
    benv.add_argument("run_dir")
    benv.add_argument("--library", default="library")
    benv.add_argument("--only", action="append", default=[])

    bprop = sub.add_parser("build-props", help="run the Asset Builder QA loop on open prop build requests (needs bpy)")
    bprop.add_argument("run_dir")
    bprop.add_argument("--library", default="library")
    bprop.add_argument("--only", action="append", default=[])
    bprop.add_argument("--agents", default="claude", choices=["claude", "stub"])
    bprop.add_argument("--time-limit", type=float, default=300.0, help="seconds a script may run")
    bprop.add_argument("--no-render", action="store_true", help="skip the turntable (QA then sees a blank sheet)")

    breq = sub.add_parser("build-requests", help="rank and route a run's build requests; print the queue and metrics")
    breq.add_argument("run_dir")
    breq.add_argument("--library", default="library")
    breq.add_argument("--save", action="store_true", help="write screen time and routes back into the run's outputs")
    breq.add_argument("--json", action="store_true")

    gui = sub.add_parser("gui", help="serve the local web GUI")
    gui.add_argument("--runs-dir", default="runs", help="directory holding one folder per run")
    gui.add_argument("--library", default="library", help="asset library (manifest.json, preview/, generators/)")
    gui.add_argument("--agents", default="claude", choices=["claude", "stub"], help="agent set preselected for new runs")
    gui.add_argument("--allow-agents", default="", help="comma list of agent sets the GUI may start (default claude,stub)")
    gui.add_argument("--host", default="127.0.0.1", help="bind address; anything but loopback exposes the GUI without a login")
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    gui.add_argument("--no-auto-resume", action="store_true", help="after a gate decision, wait for Resume instead of continuing")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd in ("approve", "revise", "edit"):
        return cmd_decide(args, args.cmd)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "build-characters":
        return cmd_build_characters(args)
    if args.cmd == "build-environments":
        return cmd_build_environments(args)
    if args.cmd == "build-props":
        return cmd_build_props(args)
    if args.cmd == "build-requests":
        return cmd_build_requests(args)
    if args.cmd == "gui":
        return cmd_gui(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
