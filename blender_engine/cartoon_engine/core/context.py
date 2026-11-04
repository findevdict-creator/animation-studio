"""What a job handler gets: its workspace, its params, and a way to report progress."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from studio_contracts.jobs import JobEnvelope, OutputFile, ProgressEvent


@dataclass
class JobContext:
    envelope: JobEnvelope
    workspace: Path
    outputs: list[OutputFile] = field(default_factory=list)

    @property
    def out_dir(self) -> Path:
        d = self.workspace / "out"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def progress(self, value: float, message: str = "") -> None:
        event = ProgressEvent(progress=max(0.0, min(1.0, value)), message=message, at=datetime.now(timezone.utc))
        with open(self.workspace / "progress.jsonl", "a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")

    def output(self, path: Path, role: str, mime_type: str) -> None:
        """Declare a file under ``out/`` as a job output (the worker hashes and uploads it)."""
        rel = path.resolve().relative_to(self.out_dir.resolve())
        self.outputs.append(OutputFile(path=rel.as_posix(), role=role, mime_type=mime_type))


def read_envelope(path: Path) -> JobEnvelope:
    return JobEnvelope.model_validate_json(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
