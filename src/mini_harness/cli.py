"""Command-line interface for live runs, replay, trace inspection, and evals."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Annotated

import typer

from mini_harness.config import HarnessConfig, load_config
from mini_harness.evals.live_report import format_live_text_report
from mini_harness.evals.live_runner import LiveEvalRunner, LivePricing
from mini_harness.evals.report import format_json_report, format_text_report
from mini_harness.evals.runner import EvalRunner
from mini_harness.models.anthropic import AnthropicModelClient
from mini_harness.models.base import ModelClient
from mini_harness.models.openai import OpenAIModelClient
from mini_harness.models.replay import ReplayModelClient
from mini_harness.policy.approval import AlwaysApprove, InteractiveApproval
from mini_harness.policy.engine import PolicyEngine
from mini_harness.runtime import AgentRuntime, RunResult, RunStatus
from mini_harness.tools import default_registry
from mini_harness.trace.matcher import TraceMatcher
from mini_harness.trace.reader import TraceReader
from mini_harness.trace.recorder import TraceRecorder

app = typer.Typer(
    name="mini-harness",
    help="A testable coding-agent harness with traces, replay, and deterministic evals.",
    no_args_is_help=True,
)


@app.command("run")
def run_command(
    task: Annotated[str, typer.Argument(help="Coding task for the model.")],
    workspace: Annotated[
        Path, typer.Option("--workspace", "-w", help="Workspace available to tools.")
    ] = Path("."),
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Optional TOML configuration.")
    ] = None,
    trace_path: Annotated[
        Path | None, typer.Option("--trace", help="Explicit JSONL trace path.")
    ] = None,
    auto_approve: Annotated[
        bool, typer.Option("--auto-approve", help="Approve policy ASK decisions.")
    ] = False,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Model provider: openai or anthropic."),
    ] = None,
    model_name: Annotated[
        str | None,
        typer.Option("--model", help="Provider model ID."),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="OpenAI-compatible API root, normally ending in /v1."),
    ] = None,
    tool_mode: Annotated[
        str | None,
        typer.Option("--tool-mode", help="OpenAI tool transport: auto, function, or prompt."),
    ] = None,
) -> None:
    """Run a task against a live model API."""

    config = load_config(
        config_path,
        workspace=workspace,
        provider=provider,
        model=model_name,
        openai_base_url=base_url,
        openai_tool_mode=tool_mode,
    )
    _require_api_key(config.provider)
    model = _create_model_client(config)
    result = asyncio.run(
        _run_runtime(
            task,
            config,
            model,
            trace_path=trace_path,
            auto_approve=auto_approve,
        )
    )
    _print_run_result(result)


@app.command("replay")
def replay_command(
    trace: Annotated[Path, typer.Argument(help="Recorded JSONL trace.")],
    workspace: Annotated[
        Path, typer.Option("--workspace", "-w", help="Clean replay workspace.")
    ] = Path("."),
    output_trace: Annotated[
        Path | None,
        typer.Option("--output-trace", help="Path for the newly recorded replay trace."),
    ] = None,
    auto_approve: Annotated[
        bool, typer.Option("--auto-approve", help="Approve policy ASK decisions.")
    ] = False,
) -> None:
    """Drive the runtime with recorded model responses and compare tool calls."""

    reader = TraceReader(trace)
    config = HarnessConfig(workspace=workspace)
    result = asyncio.run(
        _run_runtime(
            reader.task(),
            config,
            ReplayModelClient.from_trace(trace),
            trace_path=output_trace,
            auto_approve=auto_approve,
        )
    )
    _print_run_result(result)
    divergence = TraceMatcher().compare(trace, result.trace_path)
    if divergence is None:
        typer.echo("replay: MATCH")
        return
    typer.echo("replay: DIVERGED")
    typer.echo(json.dumps(divergence.model_dump(mode="json"), indent=2))
    raise typer.Exit(1)


@app.command("eval")
def eval_command(
    cases_dir: Annotated[Path, typer.Argument(help="Directory containing */case.yaml.")] = Path(
        "evals/cases"
    ),
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit a machine-readable report.")
    ] = False,
) -> None:
    """Run deterministic cases without sending real model requests."""

    suite = asyncio.run(EvalRunner(cases_dir).run_all())
    typer.echo(format_json_report(suite) if json_output else format_text_report(suite))
    if not suite.passed:
        raise typer.Exit(1)


@app.command("live-eval")
def live_eval_command(
    cases_dir: Annotated[
        Path,
        typer.Argument(help="Directory containing live */case.yaml."),
    ] = Path("evals/live_cases"),
    output_dir: Annotated[
        Path,
        typer.Option("--output", help="Directory for incremental results and traces."),
    ] = Path("eval-results/live"),
    runs: Annotated[
        int,
        typer.Option("--runs", min=1, help="Attempts per case."),
    ] = 3,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Optional TOML configuration.")
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Model provider: openai or anthropic."),
    ] = None,
    model_name: Annotated[
        str | None,
        typer.Option("--model", help="Provider model ID."),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="OpenAI-compatible API root, normally ending in /v1."),
    ] = None,
    tool_mode: Annotated[
        str | None,
        typer.Option("--tool-mode", help="OpenAI tool transport: auto, function, or prompt."),
    ] = None,
    validate_only: Annotated[
        bool,
        typer.Option("--validate-only", help="Validate cases without API requests."),
    ] = False,
    input_price: Annotated[
        float | None,
        typer.Option("--input-price", min=0, help="USD per 1M uncached input tokens."),
    ] = None,
    output_price: Annotated[
        float | None,
        typer.Option("--output-price", min=0, help="USD per 1M output tokens."),
    ] = None,
    cache_creation_price: Annotated[
        float | None,
        typer.Option("--cache-creation-price", min=0, help="USD per 1M cache-write tokens."),
    ] = None,
    cache_read_price: Annotated[
        float | None,
        typer.Option("--cache-read-price", min=0, help="USD per 1M cached input tokens."),
    ] = None,
) -> None:
    """Run deterministic coding tasks against a real model."""

    config = load_config(
        config_path,
        provider=provider,
        model=model_name,
        openai_base_url=base_url,
        openai_tool_mode=tool_mode,
    )
    pricing = None
    if any(
        value is not None
        for value in (
            input_price,
            output_price,
            cache_creation_price,
            cache_read_price,
        )
    ):
        pricing = LivePricing(
            source="CLI override",
            basis="User-provided USD rates per 1M tokens",
            input_per_million=input_price,
            output_per_million=output_price,
            cache_creation_per_million=cache_creation_price,
            cache_read_per_million=cache_read_price,
        )
    runner = LiveEvalRunner(
        cases_dir=cases_dir,
        output_dir=output_dir,
        model_factory=lambda: _create_model_client(config),
        model_name=config.model,
        model_backend=(
            f"openai-responses-{config.openai_tool_mode}"
            if config.provider == "openai"
            else "anthropic-messages"
        ),
        runs_per_case=runs,
        pricing=pricing,
        git_commit=_git_commit(),
    )
    if validate_only:
        cases = runner.validate()
        typer.echo(f"validated {len(cases)} live eval cases; no model requests sent")
        return

    _require_api_key(config.provider)
    suite = asyncio.run(runner.run_all())
    typer.echo(format_live_text_report(suite))
    typer.echo(f"results: {(output_dir / 'results.json').resolve()}")
    if not all(attempt.passed for attempt in suite.attempts):
        raise typer.Exit(1)


@app.command("trace")
def trace_command(
    trace: Annotated[Path, typer.Argument(help="JSONL trace to summarize.")],
) -> None:
    """Print an event-count and run summary for a trace."""

    events = TraceReader(trace).read()
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event["type"])
        counts[event_type] = counts.get(event_type, 0) + 1
    terminal = next(
        (event for event in reversed(events) if event["type"] in {"run_finished", "run_failed"}),
        {},
    )
    payload = {
        "path": str(trace.resolve()),
        "events": len(events),
        "event_counts": counts,
        "terminal": terminal,
    }
    typer.echo(json.dumps(payload, ensure_ascii=True, indent=2))


async def _run_runtime(
    task: str,
    config: HarnessConfig,
    model: ModelClient,
    *,
    trace_path: Path | None,
    auto_approve: bool,
) -> RunResult:
    run_id = f"run_{uuid.uuid4().hex}"
    recorder = (
        TraceRecorder(
            trace_path,
            run_id,
            sensitive_paths=config.sensitive_paths,
        )
        if trace_path is not None
        else None
    )
    runtime = AgentRuntime(
        model=model,
        tools=default_registry(),
        policy=PolicyEngine(
            config.workspace,
            write_policy=config.write_policy,
            shell_policy=config.shell_policy,
        ),
        config=config,
        approval=AlwaysApprove() if auto_approve else InteractiveApproval(),
        recorder=recorder,
    )
    return await runtime.run(task)


def _create_model_client(config: HarnessConfig) -> ModelClient:
    if config.provider == "openai":
        return OpenAIModelClient(
            model=config.model,
            max_output_tokens=config.max_model_tokens,
            base_url=config.openai_base_url,
            tool_mode=config.openai_tool_mode,
        )
    return AnthropicModelClient(
        model=config.model,
        max_tokens=config.max_model_tokens,
    )


def _require_api_key(provider: str) -> None:
    environment_name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
    if not os.getenv(environment_name):
        raise typer.BadParameter(
            f"{environment_name} is required for provider {provider}; "
            "use --validate-only to check live cases without a credential"
        )


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _print_run_result(result: RunResult) -> None:
    if result.final_text:
        typer.echo(result.final_text)
    typer.echo(
        f"status={result.status.value} turns={result.turns} "
        f"tools={result.tool_call_count} trace={result.trace_path}"
    )
    if result.error:
        typer.echo(f"error={result.error}", err=True)
    if result.status is not RunStatus.COMPLETED:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
