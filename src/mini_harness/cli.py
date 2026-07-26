"""Command-line interface for live runs, replay, trace inspection, and evals."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Annotated

import typer

from mini_harness.config import HarnessConfig, load_config
from mini_harness.evals.report import format_json_report, format_text_report
from mini_harness.evals.runner import EvalRunner
from mini_harness.models.anthropic import AnthropicModelClient
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
) -> None:
    """Run a task against the Anthropic API."""

    config = load_config(config_path, workspace=workspace)
    model = AnthropicModelClient(
        model=config.model,
        max_tokens=config.max_model_tokens,
    )
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
    model: AnthropicModelClient | ReplayModelClient,
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
