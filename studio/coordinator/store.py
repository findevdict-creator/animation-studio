"""Run directory: versioned agent outputs, state and approvals.

    runs/<run_id>/
      config.json
      state.json               which agents are complete, current status
      outputs/<agent>.v<N>.json  every accepted version
      current/<agent>.json     the approved output used downstream
      review/                  gate files (FileGate)
      approvals.json
      production_package.json
      exports/
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from studio.schemas import AGENT_OUTPUTS, ApprovalRecord, Approvals, Config, StudioModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        for sub in ("outputs", "current", "review", "exports"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- config
    def save_config(self, config: Config) -> None:
        (self.root / "config.json").write_text(config.model_dump_json(indent=2))

    def load_config(self) -> Config:
        return Config.model_validate_json((self.root / "config.json").read_text())

    # -------------------------------------------------------------- state
    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"status": "new", "completed": [], "attempts": {}, "fix_round": 0, "updated_at": None}

    def save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = _now().isoformat()
        self.state_path.write_text(json.dumps(state, indent=2))

    def set_status(self, status: str, **extra: Any) -> dict[str, Any]:
        state = self.load_state()
        state["status"] = status
        state.update(extra)
        self.save_state(state)
        return state

    # ------------------------------------------------------------ outputs
    def next_version(self, agent: str) -> int:
        existing = list((self.root / "outputs").glob(f"{agent}.v*.json"))
        return len(existing) + 1

    def save_output(self, agent: str, model: StudioModel) -> int:
        version = self.next_version(agent)
        text = model.model_dump_json(indent=2)
        (self.root / "outputs" / f"{agent}.v{version}.json").write_text(text)
        (self.root / "current" / f"{agent}.json").write_text(text)
        return version

    def load_output(self, agent: str) -> StudioModel | None:
        path = self.root / "current" / f"{agent}.json"
        if not path.exists():
            return None
        return AGENT_OUTPUTS[agent].model_validate_json(path.read_text())

    def load_all_outputs(self) -> dict[str, StudioModel]:
        out: dict[str, StudioModel] = {}
        for agent in AGENT_OUTPUTS:
            model = self.load_output(agent)
            if model is not None:
                out[agent] = model
        return out

    def invalidate_from(self, agent: str, order: list[str]) -> None:
        """Drop current outputs of ``agent`` and everything after it."""
        if agent not in order:
            return
        for name in order[order.index(agent):]:
            (self.root / "current" / f"{name}.json").unlink(missing_ok=True)
        state = self.load_state()
        state["completed"] = [a for a in state["completed"] if a in order[: order.index(agent)]]
        self.save_state(state)

    # ---------------------------------------------------------- approvals
    @property
    def approvals_path(self) -> Path:
        return self.root / "approvals.json"

    def load_approvals(self) -> Approvals:
        if self.approvals_path.exists():
            return Approvals.model_validate_json(self.approvals_path.read_text())
        return Approvals()

    def record_approval(
        self, agent: str, attempt: int, decision: str, notes: str = "", decided_by: str = "cli",
        edited_payload: dict[str, Any] | None = None,
    ) -> ApprovalRecord:
        approvals = self.load_approvals()
        record = ApprovalRecord(
            agent=agent,  # type: ignore[arg-type]
            attempt=attempt,
            decision=decision,  # type: ignore[arg-type]
            notes=notes,
            decided_by=decided_by,
            decided_at=_now(),
            edited_payload=edited_payload,
        )
        approvals.records.append(record)
        self.approvals_path.write_text(approvals.model_dump_json(indent=2))
        return record

    # ------------------------------------------------------------ package
    def save_package(self, package: StudioModel) -> Path:
        path = self.root / "production_package.json"
        path.write_text(package.model_dump_json(indent=2))
        return path

    def save_export(self, name: str, text: str) -> Path:
        path = self.root / "exports" / name
        path.write_text(text)
        return path
