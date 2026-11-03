"""Command line: run a story, answer gates, show status.

    studio run --story story.txt --run-dir runs/lantern [--config config.json] [--agents stub]
    studio approve runs/lantern story_analyst
    studio edit runs/lantern story_analyst edited.json
    studio revise runs/lantern story_analyst "Mia should be older"
    studio status runs/lantern
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

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd in ("approve", "revise", "edit"):
        return cmd_decide(args, args.cmd)
    if args.cmd == "status":
        return cmd_status(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
