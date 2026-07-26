"""YAML schema for deterministic evaluation cases."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from mini_harness.messages import ModelResponse


class FileContainsExpectation(BaseModel):
    path: str
    text: str


class CommandExitCodeExpectation(BaseModel):
    command: str
    value: int


class PolicyDecisionExpectation(BaseModel):
    tool_name: str
    decision: Literal["allow", "ask", "deny"]


class ReplayExpectation(BaseModel):
    matches: bool
    responses: list[ModelResponse] | None = None
    divergence_turn: int | None = None


class ExpectedOutcomes(BaseModel):
    file_exists: list[str] = Field(default_factory=list)
    file_contains: list[FileContainsExpectation] = Field(default_factory=list)
    command_exit_code: list[CommandExitCodeExpectation] = Field(default_factory=list)
    tool_called: list[str] = Field(default_factory=list)
    tool_called_any: list[list[str]] = Field(default_factory=list)
    tool_not_called: list[str] = Field(default_factory=list)
    policy_decision_equals: list[PolicyDecisionExpectation] = Field(default_factory=list)
    max_tool_calls: int | None = None
    run_status_equals: Literal["completed", "max_turns", "failed"] | None = None
    truncated_tools: list[str] = Field(default_factory=list)
    tool_status_equals: dict[str, Literal["success", "error", "denied", "timeout"]] = Field(
        default_factory=dict
    )
    replay: ReplayExpectation | None = None


class EvalCase(BaseModel):
    name: str
    description: str
    task: str
    fixture: str = "fixture"
    max_turns: int = Field(default=8, ge=1)
    finalization_turn: bool = True
    tool_timeout_seconds: float = Field(default=5, gt=0)
    max_output_chars: int = Field(default=20_000, ge=64)
    write_policy: Literal["allow", "ask", "deny"] = "allow"
    shell_policy: Literal["allow", "ask", "deny"] = "allow"
    responses: list[ModelResponse]
    expected: ExpectedOutcomes

    @classmethod
    def from_file(cls, path: Path) -> EvalCase:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(raw)
