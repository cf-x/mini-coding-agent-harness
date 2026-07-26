"""Schema for real-model coding eval cases."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from mini_harness.evals.case import ExpectedOutcomes


class LiveEvalCase(BaseModel):
    """A clean fixture, a real-model task, and deterministic acceptance checks."""

    name: str
    description: str
    task: str
    fixture: str = "fixture"
    tags: list[str] = Field(default_factory=list)
    max_turns: int = Field(default=8, ge=1, le=100)
    finalization_turn: bool = True
    tool_timeout_seconds: float = Field(default=10, gt=0, le=300)
    max_output_chars: int = Field(default=20_000, ge=64)
    write_policy: Literal["allow", "ask", "deny"] = "allow"
    shell_policy: Literal["allow", "ask", "deny"] = "allow"
    expected: ExpectedOutcomes

    @model_validator(mode="after")
    def reject_replay_expectations(self) -> LiveEvalCase:
        if self.expected.replay is not None:
            raise ValueError("live eval cases cannot define replay expectations")
        return self

    @classmethod
    def from_file(cls, path: Path) -> LiveEvalCase:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(raw)
