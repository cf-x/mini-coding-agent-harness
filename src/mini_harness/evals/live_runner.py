"""Run real model clients against clean fixtures and deterministic assertions."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mini_harness.config import HarnessConfig
from mini_harness.evals.evaluators import AssertionResult, EvalContext, build_evaluators
from mini_harness.evals.live_case import LiveEvalCase
from mini_harness.messages import ModelUsage, ToolResultStatus
from mini_harness.models.base import ModelClient
from mini_harness.policy.approval import AlwaysApprove
from mini_harness.policy.engine import PolicyEngine
from mini_harness.runtime import AgentRuntime, RunResult, RunStatus
from mini_harness.tools import default_registry
from mini_harness.trace.reader import TraceReader
from mini_harness.trace.recorder import TraceRecorder

ModelFactory = Callable[[], ModelClient]
OPENAI_PRICING_URL = "https://developers.openai.com/api/docs/pricing"


class FailureCategory(StrEnum):
    NONE = "none"
    MODEL_PLANNING_ERROR = "model_planning_error"
    WRONG_TOOL_ARGUMENTS = "wrong_tool_arguments"
    POLICY_DENIED = "policy_denied"
    TOOL_ERROR = "tool_error"
    TEST_FAILURE = "test_failure"
    MAX_TURNS = "max_turns"
    PROVIDER_ERROR = "provider_error"
    ENVIRONMENT_ERROR = "environment_error"


class LivePricing(BaseModel):
    """Optional USD rates per million tokens, recorded with every result set."""

    source: str = "not provided"
    basis: str = "USD per 1M tokens"
    input_per_million: float | None = Field(default=None, ge=0)
    output_per_million: float | None = Field(default=None, ge=0)
    cache_creation_per_million: float | None = Field(default=None, ge=0)
    cache_read_per_million: float | None = Field(default=None, ge=0)

    def estimate_usd(self, usage: ModelUsage) -> float | None:
        if self.input_per_million is None or self.output_per_million is None:
            return None
        if usage.cache_creation_input_tokens and self.cache_creation_per_million is None:
            return None
        if usage.cache_read_input_tokens and self.cache_read_per_million is None:
            return None
        uncached_input = max(
            0,
            usage.input_tokens - usage.cache_creation_input_tokens - usage.cache_read_input_tokens,
        )
        cost = (
            uncached_input * self.input_per_million
            + usage.output_tokens * self.output_per_million
            + usage.cache_creation_input_tokens * (self.cache_creation_per_million or 0)
            + usage.cache_read_input_tokens * (self.cache_read_per_million or 0)
        ) / 1_000_000
        return round(cost, 6)


def official_openai_pricing(model: str) -> LivePricing | None:
    """Return OpenAI Standard API rates verified on the pricing page."""

    rates = {
        "gpt-5.6-sol": (5.0, 0.5, 6.25, 30.0),
        "gpt-5.6-terra": (2.5, 0.25, 3.125, 15.0),
        "gpt-5.6-luna": (1.0, 0.1, 1.25, 6.0),
    }
    selected = rates.get(model)
    if selected is None:
        return None
    input_rate, cache_read_rate, cache_creation_rate, output_rate = selected
    return LivePricing(
        source=OPENAI_PRICING_URL,
        basis="OpenAI Standard API rates; custom gateway billing may differ",
        input_per_million=input_rate,
        output_per_million=output_rate,
        cache_creation_per_million=cache_creation_rate,
        cache_read_per_million=cache_read_rate,
    )


class LiveAttemptResult(BaseModel):
    case_name: str
    attempt: int = Field(ge=1)
    passed: bool
    failure_category: FailureCategory
    run_id: str
    status: str
    turns: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    usage: ModelUsage
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    tool_errors: int = Field(ge=0)
    policy_denials: int = Field(ge=0)
    trace_path: str
    assertions: list[AssertionResult]
    error: str | None = None


class LiveSuiteResult(BaseModel):
    model: str
    model_backend: str
    git_commit: str
    runs_per_case: int = Field(ge=1)
    pricing: LivePricing
    started_at: datetime
    completed_at: datetime
    attempts: list[LiveAttemptResult] = Field(default_factory=list)

    @property
    def passed_attempts(self) -> int:
        return sum(attempt.passed for attempt in self.attempts)

    @property
    def task_pass_rate(self) -> float:
        return self.passed_attempts / len(self.attempts) if self.attempts else 0.0

    @property
    def pass_at_k(self) -> float:
        case_names = {attempt.case_name for attempt in self.attempts}
        if not case_names:
            return 0.0
        passed_cases = {attempt.case_name for attempt in self.attempts if attempt.passed}
        return len(passed_cases) / len(case_names)

    @property
    def total_usage(self) -> ModelUsage:
        total = ModelUsage()
        for attempt in self.attempts:
            total.add(attempt.usage)
        return total

    @property
    def total_estimated_cost_usd(self) -> float | None:
        costs = [attempt.estimated_cost_usd for attempt in self.attempts]
        if any(cost is None for cost in costs):
            return None
        return round(sum(cost for cost in costs if cost is not None), 6)

    @property
    def average_turns(self) -> float:
        return _average([attempt.turns for attempt in self.attempts])

    @property
    def average_tool_calls(self) -> float:
        return _average([attempt.tool_calls for attempt in self.attempts])

    @property
    def average_duration_ms(self) -> float:
        return _average([attempt.duration_ms for attempt in self.attempts])

    def metrics(self) -> dict[str, float | int | None]:
        usage = self.total_usage
        return {
            "attempts": len(self.attempts),
            "passed_attempts": self.passed_attempts,
            "task_pass_rate": self.task_pass_rate,
            "pass_at_k": self.pass_at_k,
            "average_turns": self.average_turns,
            "average_tool_calls": self.average_tool_calls,
            "average_duration_ms": self.average_duration_ms,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "total_estimated_cost_usd": self.total_estimated_cost_usd,
        }


class LiveEvalRunner:
    """Run each live case repeatedly while preserving traces and incremental results."""

    def __init__(
        self,
        *,
        cases_dir: Path,
        output_dir: Path,
        model_factory: ModelFactory,
        model_name: str,
        model_backend: str = "unknown",
        runs_per_case: int = 3,
        pricing: LivePricing | None = None,
        git_commit: str = "unknown",
    ) -> None:
        if runs_per_case < 1:
            raise ValueError("runs_per_case must be at least 1")
        self.cases_dir = cases_dir.expanduser().resolve()
        self.output_dir = output_dir.expanduser().resolve()
        self.model_factory = model_factory
        self.model_name = model_name
        self.model_backend = model_backend
        self.runs_per_case = runs_per_case
        self.pricing = pricing or official_openai_pricing(model_name) or LivePricing()
        self.git_commit = git_commit

    def discover(self) -> list[tuple[Path, LiveEvalCase]]:
        discovered: list[tuple[Path, LiveEvalCase]] = []
        for path in sorted(self.cases_dir.glob("*/case.yaml")):
            case = LiveEvalCase.from_file(path)
            fixture = path.parent / case.fixture
            if not fixture.is_dir():
                raise ValueError(f"live eval fixture directory does not exist: {fixture}")
            discovered.append((path.parent, case))
        if not discovered:
            raise ValueError(f"no live eval cases found in {self.cases_dir}")
        return discovered

    def validate(self) -> list[LiveEvalCase]:
        return [case for _, case in self.discover()]

    async def run_all(self) -> LiveSuiteResult:
        started_at = datetime.now(UTC)
        suite = LiveSuiteResult(
            model=self.model_name,
            model_backend=self.model_backend,
            git_commit=self.git_commit,
            runs_per_case=self.runs_per_case,
            pricing=self.pricing,
            started_at=started_at,
            completed_at=started_at,
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

        for case_dir, case in self.discover():
            for attempt in range(1, self.runs_per_case + 1):
                result = await self.run_attempt(case_dir, case, attempt)
                suite.attempts.append(result)
                suite.completed_at = datetime.now(UTC)
                self._persist(suite)
        return suite

    async def run_attempt(
        self,
        case_dir: Path,
        case: LiveEvalCase,
        attempt: int,
    ) -> LiveAttemptResult:
        with tempfile.TemporaryDirectory(prefix=f"mini-harness-live-{case.name}-") as raw_root:
            workspace = Path(raw_root) / "workspace"
            shutil.copytree(case_dir / case.fixture, workspace)
            relative_trace = Path("traces") / case.name / f"attempt-{attempt}.jsonl"
            trace_path = self.output_dir / relative_trace
            trace_path.parent.mkdir(parents=True, exist_ok=True)

            config = HarnessConfig(
                workspace=workspace,
                max_turns=case.max_turns,
                tool_timeout_seconds=case.tool_timeout_seconds,
                max_output_chars=case.max_output_chars,
                write_policy=case.write_policy,
                shell_policy=case.shell_policy,
                trace_dir=trace_path.parent,
            )
            run_id = f"live_{case.name}_{attempt}_{uuid.uuid4().hex}"
            runtime = AgentRuntime(
                model=self.model_factory(),
                tools=default_registry(),
                policy=PolicyEngine(
                    workspace,
                    write_policy=case.write_policy,
                    shell_policy=case.shell_policy,
                ),
                config=config,
                approval=AlwaysApprove(),
                recorder=TraceRecorder(
                    trace_path,
                    run_id,
                    sensitive_paths=config.sensitive_paths,
                ),
            )
            run = await runtime.run(case.task)
            events = TraceReader(trace_path).read()
            context = EvalContext(workspace=workspace, run=run, events=events)
            assertions = [
                await evaluator.evaluate(context) for evaluator in build_evaluators(case.expected)
            ]
            passed = all(assertion.passed for assertion in assertions)
            usage = _usage_from_events(events)
            tool_errors = sum(
                result.status in {ToolResultStatus.ERROR, ToolResultStatus.TIMEOUT}
                for result in run.tool_results
            )
            policy_denials = sum(
                event.get("effective_decision") == "deny"
                for event in events
                if event["type"] == "policy_decided"
            )
            return LiveAttemptResult(
                case_name=case.name,
                attempt=attempt,
                passed=passed,
                failure_category=_classify_failure(
                    run,
                    assertions,
                    policy_denials=policy_denials,
                ),
                run_id=run.run_id,
                status=run.status.value,
                turns=run.turns,
                tool_calls=run.tool_call_count,
                duration_ms=run.duration_ms,
                usage=usage,
                estimated_cost_usd=self.pricing.estimate_usd(usage),
                tool_errors=tool_errors,
                policy_denials=policy_denials,
                trace_path=relative_trace.as_posix(),
                assertions=assertions,
                error=run.error,
            )

    def _persist(self, suite: LiveSuiteResult) -> None:
        path = self.output_dir / "results.json"
        path.write_text(suite.model_dump_json(indent=2), encoding="utf-8")
        from mini_harness.evals.live_report import format_live_markdown_report

        (self.output_dir / "README.md").write_text(
            format_live_markdown_report(suite),
            encoding="utf-8",
        )


def _usage_from_events(events: list[dict[str, Any]]) -> ModelUsage:
    total = ModelUsage()
    for event in events:
        if event["type"] != "model_response":
            continue
        response = event.get("response")
        if not isinstance(response, dict) or not isinstance(response.get("usage"), dict):
            continue
        total.add(ModelUsage.model_validate(response["usage"]))
    return total


def _classify_failure(
    run: RunResult,
    assertions: list[AssertionResult],
    *,
    policy_denials: int,
) -> FailureCategory:
    if all(assertion.passed for assertion in assertions):
        return FailureCategory.NONE
    if run.status is RunStatus.MAX_TURNS:
        return FailureCategory.MAX_TURNS
    if run.status is RunStatus.FAILED:
        return FailureCategory.PROVIDER_ERROR
    if policy_denials:
        return FailureCategory.POLICY_DENIED
    if any(
        result.status is ToolResultStatus.ERROR and "invalid arguments" in result.output
        for result in run.tool_results
    ):
        return FailureCategory.WRONG_TOOL_ARGUMENTS
    if any(
        result.status in {ToolResultStatus.ERROR, ToolResultStatus.TIMEOUT}
        for result in run.tool_results
    ):
        return FailureCategory.TOOL_ERROR
    if any(
        not assertion.passed
        and assertion.evaluator in {"CommandExitCode", "FileContains", "FileExists"}
        for assertion in assertions
    ):
        return FailureCategory.TEST_FAILURE
    return FailureCategory.MODEL_PLANNING_ERROR


def _average(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0
