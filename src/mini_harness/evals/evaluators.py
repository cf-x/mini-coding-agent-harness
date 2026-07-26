"""Small, deterministic assertions over a run and its workspace."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from mini_harness.evals.case import (
    CommandExitCodeExpectation,
    ExpectedOutcomes,
    FileContainsExpectation,
    PolicyDecisionExpectation,
)
from mini_harness.runtime import RunResult
from mini_harness.trace.matcher import Divergence


class AssertionResult(BaseModel):
    evaluator: str
    passed: bool
    message: str


@dataclass(frozen=True)
class EvalContext:
    workspace: Path
    run: RunResult
    events: list[dict[str, Any]]
    replay_divergence: Divergence | None = None


class Evaluator(Protocol):
    async def evaluate(self, context: EvalContext) -> AssertionResult:
        """Evaluate one deterministic condition."""


@dataclass(frozen=True)
class FileExists:
    path: str

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        exists = (context.workspace / self.path).exists()
        return AssertionResult(
            evaluator="FileExists",
            passed=exists,
            message=f"{self.path} {'exists' if exists else 'does not exist'}",
        )


@dataclass(frozen=True)
class FileContains:
    expectation: FileContainsExpectation

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        path = context.workspace / self.expectation.path
        actual = path.read_text(encoding="utf-8") if path.exists() else ""
        passed = self.expectation.text in actual
        return AssertionResult(
            evaluator="FileContains",
            passed=passed,
            message=(
                f"{self.expectation.path} contains expected text"
                if passed
                else f"{self.expectation.path} is missing expected text"
            ),
        )


@dataclass(frozen=True)
class CommandExitCode:
    expectation: CommandExitCodeExpectation
    timeout_seconds: float = 10

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        process = await asyncio.create_subprocess_shell(
            self.expectation.command,
            cwd=context.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), self.timeout_seconds)
            exit_code = process.returncode
            detail = stdout.decode("utf-8", errors="replace").strip()
        except TimeoutError:
            process.kill()
            await process.wait()
            exit_code = None
            detail = "command timed out"
        passed = exit_code == self.expectation.value
        message = f"exit code {exit_code}, expected {self.expectation.value}"
        if not passed and detail:
            message += f": {detail[-300:]}"
        return AssertionResult(
            evaluator="CommandExitCode",
            passed=passed,
            message=message,
        )


@dataclass(frozen=True)
class ToolCalled:
    tool_name: str

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        names = [result.tool_name for result in context.run.tool_results]
        passed = self.tool_name in names
        return AssertionResult(
            evaluator="ToolCalled",
            passed=passed,
            message=f"{self.tool_name} {'was' if passed else 'was not'} called",
        )


@dataclass(frozen=True)
class ToolNotCalled:
    tool_name: str

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        names = [result.tool_name for result in context.run.tool_results]
        passed = self.tool_name not in names
        return AssertionResult(
            evaluator="ToolNotCalled",
            passed=passed,
            message=f"{self.tool_name} {'was not' if passed else 'was'} called",
        )


@dataclass(frozen=True)
class PolicyDecisionEquals:
    expectation: PolicyDecisionExpectation

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        decisions = [
            event.get("decision")
            for event in context.events
            if event["type"] == "policy_decided"
            and event.get("tool_name") == self.expectation.tool_name
        ]
        passed = self.expectation.decision in decisions
        return AssertionResult(
            evaluator="PolicyDecisionEquals",
            passed=passed,
            message=(
                f"{self.expectation.tool_name} decisions {decisions}, "
                f"expected {self.expectation.decision}"
            ),
        )


@dataclass(frozen=True)
class MaxToolCalls:
    maximum: int

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        actual = context.run.tool_call_count
        return AssertionResult(
            evaluator="MaxToolCalls",
            passed=actual <= self.maximum,
            message=f"{actual} tool calls, maximum {self.maximum}",
        )


@dataclass(frozen=True)
class RunStatusEquals:
    expected: str

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        actual = context.run.status.value
        return AssertionResult(
            evaluator="RunStatusEquals",
            passed=actual == self.expected,
            message=f"run status {actual}, expected {self.expected}",
        )


@dataclass(frozen=True)
class ReplayMatches:
    expected: bool

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        actual = context.replay_divergence is None
        detail = (
            "no divergence"
            if context.replay_divergence is None
            else context.replay_divergence.reason
        )
        return AssertionResult(
            evaluator="ReplayMatches",
            passed=actual is self.expected,
            message=f"replay matches={actual}, expected={self.expected}: {detail}",
        )


@dataclass(frozen=True)
class DivergenceTurnEquals:
    expected: int

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        actual = context.replay_divergence.turn if context.replay_divergence is not None else None
        return AssertionResult(
            evaluator="DivergenceTurnEquals",
            passed=actual == self.expected,
            message=f"first divergence turn {actual}, expected {self.expected}",
        )


@dataclass(frozen=True)
class ToolOutputTruncated:
    tool_name: str

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        matching = [
            result for result in context.run.tool_results if result.tool_name == self.tool_name
        ]
        passed = any(result.truncated for result in matching)
        return AssertionResult(
            evaluator="ToolOutputTruncated",
            passed=passed,
            message=f"{self.tool_name} truncated flags {[item.truncated for item in matching]}",
        )


@dataclass(frozen=True)
class ToolStatusEquals:
    tool_name: str
    expected: str

    async def evaluate(self, context: EvalContext) -> AssertionResult:
        statuses = [
            result.status.value
            for result in context.run.tool_results
            if result.tool_name == self.tool_name
        ]
        return AssertionResult(
            evaluator="ToolStatusEquals",
            passed=self.expected in statuses,
            message=f"{self.tool_name} statuses {statuses}, expected {self.expected}",
        )


def build_evaluators(expected: ExpectedOutcomes) -> list[Evaluator]:
    """Build the shared deterministic assertions for scripted and live evals."""

    evaluators: list[Evaluator] = []
    evaluators.extend(FileExists(path) for path in expected.file_exists)
    evaluators.extend(FileContains(item) for item in expected.file_contains)
    evaluators.extend(CommandExitCode(item) for item in expected.command_exit_code)
    evaluators.extend(ToolCalled(name) for name in expected.tool_called)
    evaluators.extend(ToolNotCalled(name) for name in expected.tool_not_called)
    evaluators.extend(PolicyDecisionEquals(item) for item in expected.policy_decision_equals)
    if expected.max_tool_calls is not None:
        evaluators.append(MaxToolCalls(expected.max_tool_calls))
    if expected.run_status_equals is not None:
        evaluators.append(RunStatusEquals(expected.run_status_equals))
    evaluators.extend(ToolOutputTruncated(name) for name in expected.truncated_tools)
    evaluators.extend(
        ToolStatusEquals(name, status) for name, status in expected.tool_status_equals.items()
    )
    if expected.replay is not None:
        evaluators.append(ReplayMatches(expected.replay.matches))
        if expected.replay.divergence_turn is not None:
            evaluators.append(DivergenceTurnEquals(expected.replay.divergence_turn))
    return evaluators
