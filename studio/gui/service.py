"""What the GUI does, without HTTP: list runs, start and resume them in background
threads, answer gates, read outputs, plan the build queue.

Everything goes through the same pieces the CLI uses (RunStore, Coordinator, decide,
plan_queue), so a run started in the GUI can be finished with ``studio`` and the reverse.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from studio.coordinator import AgentRegistry, AwaitingApproval, Coordinator, PipelineError, RunStore
from studio.coordinator.pipeline import decide, load_manifest
from studio.schemas import AGENT_FILES, AGENT_ORDER, Config

RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
LOG_LINES = 400


class GuiError(Exception):
    """A request the GUI refuses; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400, details: Any = None):
        super().__init__(message)
        self.status = status
        self.details = details


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    """One background coordinator run (a start or a resume)."""

    run_id: str
    agents: str
    thread: threading.Thread | None = None
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    outcome: str = "running"  # running | done | awaiting_approval | failed | error
    log: deque = field(default_factory=lambda: deque(maxlen=LOG_LINES))

    @property
    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class _JobLogHandler(logging.Handler):
    """Routes log records from a run's thread into that run's job log."""

    def __init__(self, manager: "StudioService"):
        super().__init__(level=logging.INFO)
        self.manager = manager
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        name = threading.current_thread().name
        if not name.startswith("studio-run:"):
            return
        job = self.manager.jobs.get(name.split(":", 1)[1])
        if job is not None:
            job.log.append(self.format(record))


class StudioService:
    """State shared by every request: where runs and the library live, and running jobs."""

    def __init__(
        self,
        runs_dir: Path | str = "runs",
        library: Path | str = "library",
        agents: str = "llm",
        allowed_agents: tuple[str, ...] = ("llm", "stub"),
        auto_resume: bool = True,
        registry_factory: Callable[[str, Config], AgentRegistry] | None = None,
    ):
        self.runs_dir = Path(runs_dir).resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.library = Path(library).resolve()
        self.default_agents = _agent_set(agents)
        self.allowed_agents = tuple(dict.fromkeys(_agent_set(a) for a in allowed_agents))
        if self.default_agents not in self.allowed_agents:
            raise ValueError(f"default agent set '{agents}' is not in allowed {self.allowed_agents}")
        self.auto_resume = auto_resume
        self.registry_factory = registry_factory or _default_registry
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._handler = _JobLogHandler(self)
        logging.getLogger("studio").addHandler(self._handler)
        studio_log = logging.getLogger("studio")
        if studio_log.level == logging.NOTSET or studio_log.level > logging.INFO:
            studio_log.setLevel(logging.INFO)

    def close(self) -> None:
        logging.getLogger("studio").removeHandler(self._handler)

    # ------------------------------------------------------------- paths
    def run_path(self, run_id: str, must_exist: bool = True) -> Path:
        if not RUN_ID.match(run_id or ""):
            raise GuiError("run names use letters, digits, '.', '_' and '-' (up to 64)")
        path = self.runs_dir / run_id
        if must_exist and not (path / "config.json").exists():
            raise GuiError(f"no run named '{run_id}'", 404)
        return path

    @staticmethod
    def safe_join(root: Path, rel: str) -> Path:
        target = (root / rel).resolve()
        if target != root.resolve() and root.resolve() not in target.parents:
            raise GuiError("path leaves the run directory", 403)
        if not target.is_file():
            raise GuiError(f"no file '{rel}'", 404)
        return target

    # ------------------------------------------------------------- info
    def info(self) -> dict[str, Any]:
        from studio.llm import describe_providers, default_provider_name
        from studio.schemas import Flags, HumanApproval
        from studio.schemas.config import LLM_AGENTS

        return {
            "llm_providers": describe_providers(),
            "llm_default_provider": default_provider_name(),
            "llm_agents": list(LLM_AGENTS[:6]),
            "runs_dir": str(self.runs_dir),
            "library": str(self.library),
            "default_agents": self.default_agents,
            "allowed_agents": list(self.allowed_agents),
            "auto_resume": self.auto_resume,
            "agent_order": list(AGENT_ORDER),
            "agent_files": AGENT_FILES,
            "flags_schema": Flags.model_json_schema(),
            "defaults": {
                "flags": Flags().model_dump(mode="json"),
                "human_approval": HumanApproval().model_dump(mode="json"),
                "target_duration_sec": 30.0 if self.default_agents == "stub" else 120.0,
                "visual_style": Config.model_fields["visual_style"].default,
                "fps": Config.model_fields["fps"].default,
                "aspect_ratio": Config.model_fields["aspect_ratio"].default,
                "audience": Config.model_fields["audience"].default,
                "library_manifest_path": str(self.library / "manifest.json"),
            },
        }

    # ------------------------------------------------------------- llm
    @staticmethod
    def _llm_choices(config: Config) -> dict[str, str] | None:
        from studio.llm import LLMSettings

        try:
            return LLMSettings.from_flags(config.flags).summary(config.agents_enabled())
        except Exception:
            return None

    def llm_summary(self, config: Config) -> str:
        """'openai:gpt-5' when every agent shares one model, else the distinct choices."""
        from studio.llm import LLMSettings

        choices = LLMSettings.from_flags(config.flags).summary(config.agents_enabled())
        distinct = list(dict.fromkeys(choices.values()))
        if len(distinct) == 1:
            return distinct[0]
        return ", ".join(f"{a}={c}" for a, c in choices.items())

    # ------------------------------------------------------------- runs
    def list_runs(self) -> list[dict[str, Any]]:
        runs = []
        for path in sorted(self.runs_dir.iterdir()) if self.runs_dir.exists() else []:
            if path.is_dir() and (path / "config.json").exists() and RUN_ID.match(path.name):
                runs.append(self._summary(path.name, path))
        runs.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
        return runs

    def _summary(self, run_id: str, path: Path) -> dict[str, Any]:
        store = RunStore(path)
        state = store.load_state()
        job = self.jobs.get(run_id)
        status = state.get("status", "new")
        if status == "running" and not (job and job.alive):
            status = "interrupted"
        title = None
        bible = path / "current" / "story_analyst.json"
        if bible.exists():
            try:
                title = json.loads(bible.read_text()).get("title")
            except (OSError, json.JSONDecodeError):
                title = None
        return {
            "id": run_id,
            "title": title,
            "status": status,
            "awaiting": state.get("awaiting") if status == "awaiting_approval" else None,
            "completed": state.get("completed", []),
            "package_status": state.get("package_status"),
            "updated_at": state.get("updated_at"),
            "active": bool(job and job.alive),
        }

    def run_detail(self, run_id: str) -> dict[str, Any]:
        path = self.run_path(run_id)
        store = RunStore(path)
        config = store.load_config()
        summary = self._summary(run_id, path)
        state = store.load_state()
        order = config.agents_enabled()
        awaiting = summary["awaiting"]
        steps = []
        for agent in order:
            versions = sorted(
                int(p.stem.rsplit(".v", 1)[1]) for p in (path / "outputs").glob(f"{agent}.v*.json")
            )
            if agent == awaiting:
                step = "awaiting_approval"
            elif agent in state.get("completed", []):
                step = "done"
            elif summary["status"] == "failed" and state.get("failed_agent") == agent:
                step = "failed"
            else:
                step = "pending"
            steps.append(
                {
                    "agent": agent,
                    "state": step,
                    "attempts": state.get("attempts", {}).get(agent, 0),
                    "versions": versions,
                    "has_current": (path / "current" / f"{agent}.json").exists(),
                    "gated": config.human_approval.gate_on(agent),
                }
            )
        if summary["active"]:
            nxt = next((s for s in steps if s["state"] == "pending"), None)
            if nxt:
                nxt["state"] = "running"
        review = None
        if awaiting and (path / "review" / f"{awaiting}.json").exists():
            review = json.loads((path / "review" / f"{awaiting}.json").read_text())
        job = self.jobs.get(run_id)
        last_error = None
        if (path / "gui_last_error.json").exists():
            last_error = json.loads((path / "gui_last_error.json").read_text())
        files = sorted(
            str(p.relative_to(path))
            for sub in ("exports", "assets")
            if (path / sub).exists()
            for p in (path / sub).rglob("*")
            if p.is_file()
        )
        if (path / "production_package.json").exists():
            files.insert(0, "production_package.json")
        return {
            **summary,
            "state": state,
            "config": config.model_dump(mode="json"),
            "agents_used": (json.loads((path / "gui.json").read_text()).get("agents") if (path / "gui.json").exists() else None),
            "llm": self._llm_choices(config),
            "steps": steps,
            "review": review,
            "approvals": json.loads(store.load_approvals().model_dump_json())["records"],
            "files": files,
            "log": list(job.log) if job else [],
            "job": {"outcome": job.outcome, "started_at": job.started_at, "finished_at": job.finished_at} if job else None,
            "last_error": last_error,
        }

    def create_run(self, body: dict[str, Any]) -> dict[str, Any]:
        name = (body.get("name") or "").strip()
        path = self.run_path(name, must_exist=False)
        if path.exists() and any(path.iterdir()):
            raise GuiError(f"a run named '{name}' already exists", 409)
        story = body.get("story_text") or ""
        if not story.strip():
            raise GuiError("the story is empty")
        agents = _agent_set(body.get("agents") or self.default_agents)
        if agents not in self.allowed_agents:
            raise GuiError(f"agent set '{agents}' is not allowed here; allowed: {', '.join(self.allowed_agents)}")
        data = {k: v for k, v in (body.get("config") or {}).items() if v is not None}
        data["story_text"] = story
        data.setdefault("target_duration_sec", self.info()["defaults"]["target_duration_sec"])
        data.setdefault("library_manifest_path", str(self.library / "manifest.json"))
        try:
            config = Config.model_validate(data)
        except ValidationError as exc:
            raise GuiError("the config is invalid", 422, json.loads(exc.json())) from exc
        path.mkdir(parents=True, exist_ok=True)
        RunStore(path).save_config(config)
        (path / "gui.json").write_text(json.dumps({"agents": agents, "created_at": _now()}, indent=2))
        self.start(name, agents)
        return self.run_detail(name)

    def start(self, run_id: str, agents: str | None = None) -> dict[str, Any]:
        """Run or resume ``run_id`` in a background thread."""
        path = self.run_path(run_id)
        if agents is None and (path / "gui.json").exists():
            agents = json.loads((path / "gui.json").read_text()).get("agents")
        agents = _agent_set(agents or self.default_agents)
        if agents not in self.allowed_agents:
            raise GuiError(f"agent set '{agents}' is not allowed here")
        with self._lock:
            job = self.jobs.get(run_id)
            if job and job.alive:
                raise GuiError("this run is already running", 409)
            old_log = list(job.log) if job else []
            job = Job(run_id=run_id, agents=agents)
            job.log.extend(old_log[-LOG_LINES // 2 :])
            label = agents
            if agents == "llm":
                try:
                    label = f"llm agents ({self.llm_summary(RunStore(path).load_config())})"
                except Exception as exc:  # a bad provider choice shows up again when the run starts
                    label = f"llm agents ({exc})"
            job.log.append(f"--- {'resume' if old_log or (path / 'state.json').exists() else 'start'} with {label}")
            job.thread = threading.Thread(target=self._run, args=(job, path), name=f"studio-run:{run_id}", daemon=True)
            self.jobs[run_id] = job
            job.thread.start()
        return {"started": True, "agents": agents}

    def _run(self, job: Job, path: Path) -> None:
        err_path = path / "gui_last_error.json"
        err_path.unlink(missing_ok=True)
        log = logging.getLogger("studio.gui")
        try:
            store = RunStore(path)
            config = store.load_config()
            manifest = load_manifest(config.library_manifest_path)
            coordinator = Coordinator(config, manifest, self.registry_factory(job.agents, config), store, project_id=path.name)
            result = coordinator.run()
            job.outcome = "done"
            log.info("done: %s", result.name)
        except AwaitingApproval as exc:
            job.outcome = "awaiting_approval"
            log.info("paused: %s needs approval", exc.agent)
        except PipelineError as exc:
            job.outcome = "failed"
            log.warning("failed: %s", exc)
            err_path.write_text(json.dumps({"agent": exc.agent, "message": str(exc), "issues": [i.model_dump(mode="json") for i in exc.issues[:50]]}, indent=2))
        except Exception as exc:  # anything else (missing API key, bad manifest): show it
            job.outcome = "error"
            log.exception("error: %s", exc)
            err_path.write_text(json.dumps({"agent": None, "message": f"{type(exc).__name__}: {exc}", "issues": []}, indent=2))
            try:
                RunStore(path).set_status("failed")
            except Exception:  # pragma: no cover
                pass
        finally:
            job.finished_at = _now()

    def wait(self, run_id: str, timeout: float = 30.0) -> str | None:
        job = self.jobs.get(run_id)
        if job and job.thread:
            job.thread.join(timeout)
        return job.outcome if job else None

    # ------------------------------------------------------------- gates
    def answer_gate(self, run_id: str, agent: str, body: dict[str, Any]) -> dict[str, Any]:
        path = self.run_path(run_id)
        detail = self._summary(run_id, path)
        if detail["active"]:
            raise GuiError("wait for the run to pause before answering", 409)
        if detail["awaiting"] != agent:
            raise GuiError(f"{agent} has no open gate", 409)
        decision = body.get("decision")
        edited = body.get("edited_payload")
        if decision == "edit":
            if isinstance(edited, str):
                try:
                    edited = json.loads(edited)
                except json.JSONDecodeError as exc:
                    raise GuiError(f"the edited JSON does not parse: {exc}") from exc
            if not isinstance(edited, dict):
                raise GuiError("edit needs the corrected JSON object")
        else:
            edited = None
        if decision == "revise" and not (body.get("notes") or "").strip():
            raise GuiError("revise needs notes for the agent")
        try:
            decide(path, agent, decision, body.get("notes") or "", edited)
        except ValueError as exc:
            raise GuiError(str(exc)) from exc
        # FileGate reads decided_by from the decision file; mark it as the GUI's.
        dpath = path / "review" / f"{agent}.decision.json"
        data = json.loads(dpath.read_text())
        data["decided_by"] = "gui"
        dpath.write_text(json.dumps(data, indent=2))
        resume = body.get("resume", self.auto_resume)
        if resume:
            self.start(run_id)
        return {"recorded": decision, "resumed": bool(resume)}

    # ------------------------------------------------------------- outputs
    def output(self, run_id: str, agent: str, version: int | None = None) -> Any:
        path = self.run_path(run_id)
        if agent not in AGENT_FILES:
            raise GuiError(f"unknown agent '{agent}'", 404)
        file = path / ("outputs" if version else "current") / (f"{agent}.v{version}.json" if version else f"{agent}.json")
        if not file.exists():
            raise GuiError(f"{agent} has no {'v' + str(version) if version else 'current'} output", 404)
        return json.loads(file.read_text())

    def file(self, run_id: str, rel: str) -> Path:
        return self.safe_join(self.run_path(run_id), rel)

    # ------------------------------------------------------------- queue
    def queue(self, run_id: str, save: bool = False) -> dict[str, Any]:
        from studio.assets.builder import GeneratorLibrary
        from studio.assets.characters import write_build_report
        from studio.assets.queue import apply_queue, metrics, metrics_markdown, plan_queue
        from studio.schemas import Characters, Layout, World

        path = self.run_path(run_id)
        store = RunStore(path)
        outputs = store.load_all_outputs()
        chars, world, layout = outputs.get("character_designer"), outputs.get("scene_builder"), outputs.get("layout_td")
        if not isinstance(chars, Characters) or not isinstance(world, World):
            return {"ready": False, "queue": [], "metrics": None, "reason": "the run has no character_designer and scene_builder outputs yet"}
        layout = layout if isinstance(layout, Layout) else None
        config = store.load_config()
        manifest = load_manifest(self.library / "manifest.json")
        generators = GeneratorLibrary(self.library) if (self.library / "generators").exists() else None
        requests = list(chars.build_requests) + list(world.build_requests) + (list(layout.build_requests) if layout else [])
        planned, records = plan_queue(requests, layout, world, manifest, config, generators)
        by_id = {br.id: br for br in planned}
        for r in records:
            br = by_id.get(r["id"])
            if br is not None:
                r["spec"] = br.spec
                r["attempts"] = len(br.attempts)
                r["result_library_id"] = br.result_library_id
        m = metrics(planned)
        if save:
            if self.jobs.get(run_id) and self.jobs[run_id].alive:
                raise GuiError("wait for the run to pause before saving routes", 409)
            write_build_report(store.root, "build_queue", records)
            (store.root / "assets" / "metrics.json").write_text(json.dumps(m, indent=2) + "\n")
            (store.root / "assets" / "metrics.md").write_text(metrics_markdown(m, run_id))
            c2, w2, l2 = apply_queue(chars, world, layout, planned)
            store.save_output("character_designer", c2)
            store.save_output("scene_builder", w2)
            if l2 is not None:
                store.save_output("layout_td", l2)
        return {"ready": True, "queue": records, "metrics": m, "saved": save}

    # ------------------------------------------------------------- library
    def library_manifest(self) -> dict[str, Any]:
        path = self.library / "manifest.json"
        if not path.exists():
            return {"found": False, "path": str(path)}
        data = json.loads(path.read_text())
        previews = []
        pdir = self.library / "preview"
        if pdir.exists():
            previews = sorted(str(p.relative_to(pdir)) for p in pdir.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
        scripts = self.library / "generators" / "scripts" / "index.json"
        return {
            "found": True,
            "path": str(path),
            "manifest": data,
            "previews": previews,
            "generator_scripts": json.loads(scripts.read_text()) if scripts.exists() else None,
        }

    def preview(self, rel: str) -> Path:
        return self.safe_join(self.library / "preview", rel)


def _agent_set(kind: str) -> str:
    return "llm" if kind == "claude" else kind


def _default_registry(kind: str, config: Config | None = None) -> AgentRegistry:
    from studio.cli import build_registry

    try:
        return build_registry(kind, config)
    except SystemExit as exc:  # build_registry exits on an unknown kind; keep the thread alive
        raise ValueError(str(exc)) from None
