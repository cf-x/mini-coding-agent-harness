from pathlib import Path

import pytest

from mini_harness.config import HarnessConfig
from mini_harness.messages import ModelResponse, ToolCall, ToolResultStatus
from mini_harness.models.replay import ReplayModelClient
from mini_harness.policy.approval import AlwaysApprove, AlwaysDeny
from mini_harness.policy.engine import PolicyEngine
from mini_harness.runtime import AgentRuntime, RunStatus
from mini_harness.tools import default_registry
from mini_harness.trace.reader import TraceReader
from mini_harness.trace.recorder import TraceRecorder


def runtime(
    tmp_path: Path,
    responses: list[ModelResponse],
    *,
    max_turns: int = 4,
    write_policy: str = "allow",
    shell_policy: str = "allow",
    approve: bool = True,
    sensitive_paths: list[str] | None = None,
) -> AgentRuntime:
    config = HarnessConfig(
        workspace=tmp_path,
        trace_dir=tmp_path / "traces",
        max_turns=max_turns,
        write_policy=write_policy,
        shell_policy=shell_policy,
        sensitive_paths=sensitive_paths or [],
    )
    return AgentRuntime(
        model=ReplayModelClient(responses),
        tools=default_registry(),
        policy=PolicyEngine(
            tmp_path,
            write_policy=write_policy,  # type: ignore[arg-type]
            shell_policy=shell_policy,  # type: ignore[arg-type]
        ),
        config=config,
        approval=AlwaysApprove() if approve else AlwaysDeny(),
    )


@pytest.mark.asyncio
async def test_runtime_completes_tool_loop_and_records_order(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    agent = runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=[ToolCall(id="read", name="read_file", arguments={"path": "a.txt"})]
            ),
            ModelResponse(content="done"),
        ],
    )

    result = await agent.run("read a.txt")
    events = TraceReader(result.trace_path).read()

    assert result.status is RunStatus.COMPLETED
    assert result.final_text == "done"
    assert result.tool_results[0].output == "hello"
    assert [event["type"] for event in events] == [
        "run_started",
        "model_request",
        "model_response",
        "tool_requested",
        "policy_decided",
        "tool_started",
        "tool_finished",
        "model_request",
        "model_response",
        "run_finished",
    ]


@pytest.mark.asyncio
async def test_ask_denial_skips_tool_execution(tmp_path: Path) -> None:
    agent = runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="write",
                        name="write_file",
                        arguments={"path": "a.txt", "content": "blocked"},
                    )
                ]
            ),
            ModelResponse(content="denied"),
        ],
        write_policy="ask",
        approve=False,
    )

    result = await agent.run("write")
    events = TraceReader(result.trace_path).read()

    assert result.tool_results[0].status is ToolResultStatus.DENIED
    assert not (tmp_path / "a.txt").exists()
    policy_event = next(event for event in events if event["type"] == "policy_decided")
    assert policy_event["decision"] == "ask"
    assert policy_event["approved"] is False
    assert policy_event["effective_decision"] == "deny"
    assert all(event["type"] != "tool_started" for event in events)


@pytest.mark.asyncio
async def test_runtime_stops_at_max_turns(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    tool_response = ModelResponse(
        tool_calls=[ToolCall(id="read", name="read_file", arguments={"path": "a.txt"})]
    )
    agent = runtime(tmp_path, [tool_response, tool_response], max_turns=2)

    result = await agent.run("loop")

    assert result.status is RunStatus.MAX_TURNS
    assert result.turns == 2
    assert result.tool_call_count == 2
    terminal = TraceReader(result.trace_path).read()[-1]
    assert terminal["type"] == "run_finished"
    assert terminal["status"] == "max_turns"


class FailingModel:
    async def complete(self, messages: object, tools: object) -> ModelResponse:
        del messages, tools
        raise RuntimeError("provider unavailable")


@pytest.mark.asyncio
async def test_model_failure_returns_failed_result_and_trace(tmp_path: Path) -> None:
    config = HarnessConfig(workspace=tmp_path, trace_dir=tmp_path / "traces")
    agent = AgentRuntime(
        model=FailingModel(),
        tools=default_registry(),
        policy=PolicyEngine(tmp_path),
        config=config,
    )

    result = await agent.run("fail")
    terminal = TraceReader(result.trace_path).read()[-1]

    assert result.status is RunStatus.FAILED
    assert "provider unavailable" in (result.error or "")
    assert terminal["type"] == "run_failed"


@pytest.mark.asyncio
async def test_sensitive_file_content_is_not_written_to_trace(tmp_path: Path) -> None:
    secret = "MY_EXACT_PRIVATE_VALUE"
    (tmp_path / ".env").write_text(secret, encoding="utf-8")
    trace = tmp_path / "audit.jsonl"
    config = HarnessConfig(
        workspace=tmp_path,
        trace_dir=tmp_path,
        sensitive_paths=[".env"],
    )
    agent = AgentRuntime(
        model=ReplayModelClient(
            [
                ModelResponse(
                    tool_calls=[ToolCall(id="secret", name="read_file", arguments={"path": ".env"})]
                ),
                ModelResponse(content=f"I saw {secret}"),
            ]
        ),
        tools=default_registry(),
        policy=PolicyEngine(tmp_path),
        config=config,
        recorder=TraceRecorder(trace, "run_secret", sensitive_paths=[".env"]),
    )

    result = await agent.run("read .env")

    assert result.status is RunStatus.COMPLETED
    assert secret not in trace.read_text(encoding="utf-8")
    assert result.tool_results[0].output == secret
