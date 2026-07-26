"""Run YAML eval cases in isolated temporary workspaces."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mini_harness.config import HarnessConfig
from mini_harness.evals.case import EvalCase
from mini_harness.evals.evaluators import (
    AssertionResult,
    EvalContext,
    build_evaluators,
)
from mini_harness.messages import ToolResultStatus
from mini_harness.models.replay import ReplayModelClient
from mini_harness.policy.approval import AlwaysApprove
from mini_harness.policy.engine import PolicyEngine
from mini_harness.runtime import AgentRuntime, RunResult
from mini_harness.tools import default_registry
from mini_harness.trace.matcher import Divergence, TraceMatcher
from mini_harness.trace.reader import TraceReader
from mini_harness.trace.recorder import TraceRecorder


class EvalCaseResult(BaseModel):
    name: str
    description: str
    passed: bool
    assertions: list[AssertionResult]
    run: RunResult
    tool_errors: int = 0
    policy_denials: int = 0
    replay_checked: bool = False
    replay_matched: bool | None = None
    divergence: Divergence | None = None


class EvalSuiteResult(BaseModel):
    cases: list[EvalCaseResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    @property
    def task_pass_rate(self) -> float:
        if not self.cases:
            return 0.0
        return sum(case.passed for case in self.cases) / len(self.cases)

    @property
    def average_turns(self) -> float:
        return _average([case.run.turns for case in self.cases])

    @property
    def average_tool_calls(self) -> float:
        return _average([case.run.tool_call_count for case in self.cases])

    @property
    def tool_error_rate(self) -> float:
        calls = sum(case.run.tool_call_count for case in self.cases)
        errors = sum(case.tool_errors for case in self.cases)
        return errors / calls if calls else 0.0

    @property
    def policy_denial_count(self) -> int:
        return sum(case.policy_denials for case in self.cases)

    @property
    def replay_match_rate(self) -> float:
        checked = [case for case in self.cases if case.replay_checked]
        if not checked:
            return 0.0
        return sum(case.replay_matched is True for case in checked) / len(checked)

    @property
    def average_duration_ms(self) -> float:
        return _average([case.run.duration_ms for case in self.cases])

    def metrics(self) -> dict[str, float | int]:
        return {
            "task_pass_rate": self.task_pass_rate,
            "average_turns": self.average_turns,
            "average_tool_calls": self.average_tool_calls,
            "tool_error_rate": self.tool_error_rate,
            "policy_denial_count": self.policy_denial_count,
            "replay_match_rate": self.replay_match_rate,
            "average_duration_ms": self.average_duration_ms,
        }


class EvalRunner:
    def __init__(self, cases_dir: Path) -> None:
        self.cases_dir = cases_dir.expanduser().resolve()

    def discover(self) -> list[tuple[Path, EvalCase]]:
        discovered = []
        for path in sorted(self.cases_dir.glob("*/case.yaml")):
            discovered.append((path.parent, EvalCase.from_file(path)))
        return discovered

    async def run_all(self) -> EvalSuiteResult:
        results = []
        for case_dir, case in self.discover():
            results.append(await self.run_case(case_dir, case))
        return EvalSuiteResult(cases=results)

    async def run_case(self, case_dir: Path, case: EvalCase) -> EvalCaseResult:
        with tempfile.TemporaryDirectory(prefix=f"mini-harness-{case.name}-") as raw_root:
            root = Path(raw_root)
            workspace = root / "workspace"
            fixture = case_dir / case.fixture
            if fixture.exists():
                shutil.copytree(fixture, workspace)
            else:
                workspace.mkdir()

            run, events = await self._run_script(
                case,
                workspace,
                case.responses,
                root / "original.jsonl",
            )
            divergence: Divergence | None = None
            replay_checked = case.expected.replay is not None
            if case.expected.replay is not None:
                replay_workspace = root / "replay-workspace"
                if fixture.exists():
                    shutil.copytree(fixture, replay_workspace)
                else:
                    replay_workspace.mkdir()
                responses = case.expected.replay.responses
                replay_model = (
                    ReplayModelClient(responses)
                    if responses is not None
                    else ReplayModelClient.from_trace(run.trace_path)
                )
                replay_run, _ = await self._run_model(
                    case,
                    replay_workspace,
                    replay_model,
                    root / "replay.jsonl",
                )
                divergence = TraceMatcher().compare(run.trace_path, replay_run.trace_path)

            context = EvalContext(
                workspace=workspace,
                run=run,
                events=events,
                replay_divergence=divergence,
            )
            assertions = [
                await evaluator.evaluate(context) for evaluator in build_evaluators(case.expected)
            ]
            tool_errors = sum(
                result.status in {ToolResultStatus.ERROR, ToolResultStatus.TIMEOUT}
                for result in run.tool_results
            )
            policy_denials = sum(
                event.get("effective_decision") == "deny"
                for event in events
                if event["type"] == "policy_decided"
            )
            return EvalCaseResult(
                name=case.name,
                description=case.description,
                passed=all(assertion.passed for assertion in assertions),
                assertions=assertions,
                run=run,
                tool_errors=tool_errors,
                policy_denials=policy_denials,
                replay_checked=replay_checked,
                replay_matched=divergence is None if replay_checked else None,
                divergence=divergence,
            )

    async def _run_script(
        self,
        case: EvalCase,
        workspace: Path,
        responses: list[Any],
        trace_path: Path,
    ) -> tuple[RunResult, list[dict[str, Any]]]:
        return await self._run_model(
            case,
            workspace,
            ReplayModelClient(responses),
            trace_path,
        )

    async def _run_model(
        self,
        case: EvalCase,
        workspace: Path,
        model: ReplayModelClient,
        trace_path: Path,
    ) -> tuple[RunResult, list[dict[str, Any]]]:
        config = HarnessConfig(
            workspace=workspace,
            max_turns=case.max_turns,
            finalization_turn=case.finalization_turn,
            tool_timeout_seconds=case.tool_timeout_seconds,
            max_output_chars=case.max_output_chars,
            write_policy=case.write_policy,
            shell_policy=case.shell_policy,
            trace_dir=trace_path.parent,
        )
        run_id = f"eval_{case.name}_{uuid.uuid4().hex}"
        recorder = TraceRecorder(trace_path, run_id)
        runtime = AgentRuntime(
            model=model,
            tools=default_registry(),
            policy=PolicyEngine(
                workspace,
                write_policy=case.write_policy,
                shell_policy=case.shell_policy,
            ),
            config=config,
            approval=AlwaysApprove(),
            recorder=recorder,
        )
        run = await runtime.run(case.task)
        return run, TraceReader(trace_path).read()


def _average(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0
