"""Command line: run a story, answer gates, show status.

    studio run --story story.txt --run-dir runs/lantern [--config config.json] [--agents stub]
    studio approve runs/lantern story_analyst
    studio edit runs/lantern story_analyst edited.json
    studio revise runs/lantern story_analyst "Mia should be older"
    studio status runs/lantern
    studio build-characters runs/lantern [--library library] [--toon]   (asset build phase 1; needs bpy)
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

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd in ("approve", "revise", "edit"):
        return cmd_decide(args, args.cmd)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "build-characters":
        return cmd_build_characters(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
