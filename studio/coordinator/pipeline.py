"""The pipeline runner.

For each enabled agent, in order:
  1. call the agent with upstream outputs and any feedback
  2. validate the output against its schema
  3. run the code hard checks; on errors re-ask the agent, up to flags.agent_retries
  4. ask the approval gate (approve / edit / revise)
  5. store the output as the current version

After the last agent the continuity fix loop runs: errors in the report go
back to their owner agents, downstream agents rerun, up to
flags.continuity_fix_rounds. Then the package is merged and exported.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from studio import checks
from studio.coordinator.agents import Agent, AgentContext, AgentRegistry
from studio.coordinator.exports import EXPORTS
from studio.coordinator.gates import ApprovalGate, AutoApproveGate, AwaitingApproval, FileGate
from studio.coordinator.merge import build_package
from studio.coordinator.store import RunStore
from studio.schemas import AGENT_ORDER, AGENT_OUTPUTS, Config, ContinuityReport, Manifest, StudioModel
from studio.schemas.continuity import Issue

log = logging.getLogger("studio.coordinator")


class PipelineError(Exception):
    def __init__(self, agent: str, message: str, issues: list[Issue] | None = None):
        super().__init__(f"{agent}: {message}")
        self.agent = agent
        self.issues = issues or []


def schema_issues(agent: str, exc: ValidationError) -> list[Issue]:
    issues = []
    for n, err in enumerate(exc.errors()[:50], start=1):
        loc = ".".join(str(p) for p in err["loc"])
        issues.append(
            Issue(
                id=f"iss_{n:03d}",
                severity="error",
                category="schema",
                where=loc,
                message=f"{loc}: {err['msg']}",
                owner_agent=agent,  # type: ignore[arg-type]
            )
        )
    return issues


class Coordinator:
    def __init__(
        self,
        config: Config,
        manifest: Manifest,
        registry: AgentRegistry,
        store: RunStore,
        gate: ApprovalGate | None = None,
        project_id: str = "run",
    ):
        self.config = config
        self.manifest = manifest
        self.registry = registry
        self.store = store
        self.project_id = project_id
        self.gate = gate or (
            AutoApproveGate() if config.human_approval.mode == "none" else FileGate(store.root / "review")
        )
        self.agents = config.agents_enabled()
        self.store.save_config(config)

    # ----------------------------------------------------------- helpers
    def _upstream(self) -> dict[str, StudioModel]:
        return self.store.load_all_outputs()

    def _hard_checks(self, agent: str, model: StudioModel, upstream: dict[str, StudioModel]) -> list[Issue]:
        cfg, man = self.config, self.manifest
        if agent == "character_designer":
            return checks.check_characters(upstream["story_analyst"], model, man, cfg)  # type: ignore[arg-type]
        if agent == "scene_builder":
            return checks.check_world(upstream["story_analyst"], upstream["character_designer"], model, man, cfg)  # type: ignore[arg-type]
        if agent == "director":
            return checks.check_direction(
                upstream["story_analyst"], upstream["character_designer"], upstream["scene_builder"], model, cfg  # type: ignore[arg-type]
            )
        if agent == "layout_td":
            return checks.check_layout(
                upstream["character_designer"], upstream["scene_builder"], upstream["director"], model, man, cfg  # type: ignore[arg-type]
            )
        return []

    def code_continuity_report(self, upstream: dict[str, StudioModel]) -> ContinuityReport:
        """All hard checks over the current outputs, as a report. The LLM checker adds to this."""
        issues: list[Issue] = []
        for agent in ("character_designer", "scene_builder", "director", "layout_td"):
            if agent in upstream:
                issues += self._hard_checks(agent, upstream[agent], upstream)
        for n, issue in enumerate(issues, start=1):
            issue.id = f"iss_{n:03d}"
        open_ids = checks.open_build_requests(*(upstream.get(a) for a in ("character_designer", "scene_builder", "layout_td") if a in upstream))
        return ContinuityReport.from_issues(issues, open_ids)

    # -------------------------------------------------------- one agent
    def run_agent(self, agent_name: str, feedback: list[Issue] | None = None, revise_notes: str = "") -> StudioModel:
        agent: Agent = self.registry.get(agent_name)
        schema = AGENT_OUTPUTS[agent_name]
        feedback = list(feedback or [])
        retries = self.config.flags.agent_retries
        failures = 0  # schema/check failures in this invocation; revisions do not count
        attempt = 0

        while True:
            attempt += 1
            state = self.store.load_state()
            state["attempts"][agent_name] = int(state["attempts"].get(agent_name, 0)) + 1
            self.store.save_state(state)
            ctx = AgentContext(
                config=self.config,
                manifest=self.manifest,
                upstream=self._upstream(),
                attempt=attempt,
                feedback=feedback,
                revise_notes=revise_notes,
            )
            log.info("%s attempt %d", agent_name, attempt)
            if agent_name == "continuity_checker":
                model = self._run_checker(agent, ctx)
                issues: list[Issue] = []
            else:
                try:
                    raw = agent.run(ctx)
                    model = raw if isinstance(raw, schema) else schema.model_validate(raw)
                except ValidationError as exc:
                    issues = schema_issues(agent_name, exc)
                    model = None  # type: ignore[assignment]
                except Exception as exc:  # API refusal, truncation, network: a failed attempt
                    log.warning("%s: call failed: %s", agent_name, exc)
                    issues = [
                        Issue(
                            id="iss_001", severity="error", category="schema",
                            message=f"The previous call failed: {exc}", owner_agent=agent_name,  # type: ignore[arg-type]
                        )
                    ]
                    model = None  # type: ignore[assignment]
                else:
                    issues = [i for i in self._hard_checks(agent_name, model, ctx.upstream) if i.severity == "error"]

            if issues:
                failures += 1
                log.warning("%s: %d errors on attempt %d", agent_name, len(issues), attempt)
                if failures > retries:
                    self.store.set_status("failed", failed_agent=agent_name)
                    raise PipelineError(agent_name, f"still failing after {attempt} attempts", issues)
                feedback = issues
                continue

            # Gate.
            payload = model.model_dump(mode="json")
            if self.config.human_approval.gate_on(agent_name):
                decision = self.gate.decide(agent_name, attempt, payload)
            else:
                decision = AutoApproveGate().decide(agent_name, attempt, payload)
            self.store.record_approval(
                agent_name, attempt, decision.decision, decision.notes, decision.decided_by, decision.edited_payload
            )
            if decision.decision == "revise":
                feedback, revise_notes = [], decision.notes
                continue
            if decision.decision == "edit":
                try:
                    model = schema.model_validate(decision.edited_payload)
                except ValidationError as exc:
                    raise PipelineError(agent_name, "edited payload is invalid", schema_issues(agent_name, exc)) from exc
                errors = [i for i in self._hard_checks(agent_name, model, ctx.upstream) if i.severity == "error"]
                if errors:
                    raise PipelineError(agent_name, "edited payload fails hard checks", errors)

            version = self.store.save_output(agent_name, model)
            state = self.store.load_state()
            if agent_name not in state["completed"]:
                state["completed"].append(agent_name)
            self.store.save_state(state)
            log.info("%s stored as v%d", agent_name, version)
            return model

    def _run_checker(self, agent: Agent, ctx: AgentContext) -> ContinuityReport:
        code_report = self.code_continuity_report(ctx.upstream)
        raw = agent.run(ctx)
        llm_report = raw if isinstance(raw, ContinuityReport) else ContinuityReport.model_validate(raw)
        issues = list(code_report.issues) + list(llm_report.issues)
        for n, issue in enumerate(issues, start=1):
            issue.id = f"iss_{n:03d}"
        return ContinuityReport.from_issues(issues, code_report.open_build_requests)

    # --------------------------------------------------------- the run
    def run(self) -> Path:
        """Run or resume. Returns the package path. Raises AwaitingApproval when a gate is open."""
        try:
            self.store.set_status("running")
            state = self.store.load_state()
            for agent_name in self.agents:
                if agent_name in state["completed"]:
                    continue
                self.run_agent(agent_name)
                state = self.store.load_state()
            self._fix_loop()
            return self.finish()
        except AwaitingApproval as exc:
            self.store.set_status("awaiting_approval", awaiting=exc.agent)
            raise

    def _fix_loop(self) -> None:
        if "continuity_checker" not in self.agents:
            return
        rounds = self.config.flags.continuity_fix_rounds
        state = self.store.load_state()
        while True:
            report = self.store.load_output("continuity_checker")
            assert isinstance(report, ContinuityReport)
            errors = report.errors()
            if not errors:
                return
            if state["fix_round"] >= rounds:
                log.warning("continuity errors remain after %d rounds; package marked needs_review", rounds)
                state["needs_review"] = True
                self.store.save_state(state)
                return
            state["fix_round"] += 1
            self.store.save_state(state)
            # Rerun from the earliest owner agent, with that agent's errors as feedback.
            owners = [a for a in self.agents if any(e.owner_agent == a for e in errors)]
            first = min(owners, key=self.agents.index)
            log.info("fix round %d: rerunning from %s", state["fix_round"], first)
            self.store.invalidate_from(first, self.agents)
            for agent_name in self.agents[self.agents.index(first):]:
                feedback = [e for e in errors if e.owner_agent == agent_name]
                self.run_agent(agent_name, feedback=feedback)
            state = self.store.load_state()

    def finish(self) -> Path:
        upstream = self._upstream()
        state = self.store.load_state()
        report = upstream.get("continuity_checker")
        if report is None and self.config.flags.render_backend != "none":
            report = self.code_continuity_report(upstream)
        package = build_package(
            project_id=self.project_id,
            config=self.config,
            manifest=self.manifest,
            bible=upstream["story_analyst"],  # type: ignore[arg-type]
            characters=upstream["character_designer"],  # type: ignore[arg-type]
            world=upstream["scene_builder"],  # type: ignore[arg-type]
            direction=upstream["director"],  # type: ignore[arg-type]
            layout=upstream.get("layout_td"),  # type: ignore[arg-type]
            report=report,  # type: ignore[arg-type]
            approvals=self.store.load_approvals().records,
            needs_review=bool(state.get("needs_review")),
        )
        path = self.store.save_package(package)
        for name in self.config.flags.human_exports:
            filename, fn = EXPORTS[name]
            self.store.save_export(filename, fn(package))
        self.store.set_status("done", package_status=package.status)
        return path


def load_manifest(path: Path | str) -> Manifest:
    path = Path(path)
    if not path.exists():
        log.warning("manifest %s not found; using an empty manifest", path)
        return Manifest.empty()
    return Manifest.model_validate_json(path.read_text())


def decide(run_dir: Path | str, agent: str, decision: str, notes: str = "", edited_payload: dict[str, Any] | None = None) -> Path:
    """Answer an open gate. The next ``run`` resumes from it."""
    return FileGate.write_decision(Path(run_dir) / "review", agent, decision, notes, edited_payload)
