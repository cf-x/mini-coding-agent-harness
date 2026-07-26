"""Generate deterministic, sanitized trace and replay examples for the README."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from mini_harness.config import HarnessConfig
from mini_harness.messages import ModelResponse, ToolCall
from mini_harness.models.replay import ReplayModelClient
from mini_harness.policy.approval import AlwaysApprove
from mini_harness.policy.engine import PolicyEngine
from mini_harness.runtime import AgentRuntime
from mini_harness.tools import default_registry
from mini_harness.trace.matcher import TraceMatcher
from mini_harness.trace.reader import TraceReader
from mini_harness.trace.recorder import TraceRecorder

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "examples"


async def run_trace(
    *,
    workspace: Path,
    trace_path: Path,
    run_id: str,
    task: str,
    responses: list[ModelResponse],
) -> None:
    config = HarnessConfig(
        workspace=workspace,
        trace_dir=trace_path.parent,
        write_policy="allow",
        shell_policy="allow",
    )
    runtime = AgentRuntime(
        model=ReplayModelClient(responses),
        tools=default_registry(),
        policy=PolicyEngine(workspace, write_policy="allow", shell_policy="allow"),
        config=config,
        approval=AlwaysApprove(),
        recorder=TraceRecorder(trace_path, run_id),
    )
    await runtime.run(task)


def sanitize_trace(path: Path, workspace: Path) -> None:
    sanitized: list[str] = []
    workspace_text = str(workspace.resolve())
    for event in TraceReader(path).read():
        normalized = _replace_value(event, workspace_text, "<workspace>")
        sanitized.append(json.dumps(normalized, ensure_ascii=True, separators=(",", ":")))
    path.write_text("\n".join(sanitized) + "\n", encoding="utf-8")


def _replace_value(value: Any, source: str, replacement: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace_value(item, source, replacement) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_value(item, source, replacement) for item in value]
    if isinstance(value, str):
        return value.replace(source, replacement)
    return value


async def main() -> None:
    trace_dir = OUTPUT_DIR / "traces"
    replay_dir = OUTPUT_DIR / "replay"
    trace_dir.mkdir(parents=True, exist_ok=True)
    replay_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mini-harness-demo-") as raw_root:
        root = Path(raw_root)
        success_workspace = root / "success"
        shutil.copytree(
            PROJECT_ROOT / "evals" / "cases" / "edit_and_test" / "fixture",
            success_workspace,
        )
        success_trace = trace_dir / "successful-edit.jsonl"
        success_trace.unlink(missing_ok=True)
        await run_trace(
            workspace=success_workspace,
            trace_path=success_trace,
            run_id="demo_successful_edit",
            task="Fix add() in calculator.py and run the tests.",
            responses=[
                ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_edit",
                            name="edit_file",
                            arguments={
                                "path": "calculator.py",
                                "old_text": "    return a - b",
                                "new_text": "    return a + b",
                            },
                        )
                    ]
                ),
                ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_test",
                            name="bash",
                            arguments={"command": "python3 -m unittest -q"},
                        )
                    ]
                ),
                ModelResponse(content="Fixed add() and verified the tests."),
            ],
        )
        sanitize_trace(success_trace, success_workspace)

        denied_workspace = root / "denied"
        shutil.copytree(
            PROJECT_ROOT / "evals" / "cases" / "dangerous_command" / "fixture",
            denied_workspace,
        )
        denied_trace = trace_dir / "dangerous-command-denied.jsonl"
        denied_trace.unlink(missing_ok=True)
        await run_trace(
            workspace=denied_workspace,
            trace_path=denied_trace,
            run_id="demo_dangerous_command_denied",
            task="Delete important.txt recursively.",
            responses=[
                ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_rm",
                            name="bash",
                            arguments={"command": "rm -rf important.txt"},
                        )
                    ]
                ),
                ModelResponse(content="The destructive command was denied."),
            ],
        )
        sanitize_trace(denied_trace, denied_workspace)

        expected_workspace = root / "expected"
        actual_workspace = root / "actual"
        expected_workspace.mkdir()
        actual_workspace.mkdir()
        (expected_workspace / "a.txt").write_text("alpha\n", encoding="utf-8")
        (actual_workspace / "b.txt").write_text("beta\n", encoding="utf-8")
        expected_trace = root / "expected.jsonl"
        actual_trace = root / "actual.jsonl"
        await run_trace(
            workspace=expected_workspace,
            trace_path=expected_trace,
            run_id="demo_expected",
            task="Read a.txt.",
            responses=[
                ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_expected",
                            name="read_file",
                            arguments={"path": "a.txt"},
                        )
                    ]
                ),
                ModelResponse(content="Read a.txt."),
            ],
        )
        await run_trace(
            workspace=actual_workspace,
            trace_path=actual_trace,
            run_id="demo_actual",
            task="Read a.txt.",
            responses=[
                ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_actual",
                            name="read_file",
                            arguments={"path": "b.txt"},
                        )
                    ]
                ),
                ModelResponse(content="Read b.txt."),
            ],
        )
        divergence = TraceMatcher().compare(expected_trace, actual_trace)
        if divergence is None:
            raise RuntimeError("demo traces unexpectedly matched")
        (replay_dir / "first-divergence.json").write_text(
            divergence.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    asyncio.run(main())
