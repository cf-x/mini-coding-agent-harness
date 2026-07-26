from pathlib import Path

import pytest
from anthropic.types import MessageParam
from pydantic import ValidationError

from mini_harness.config import HarnessConfig, load_config
from mini_harness.messages import Message, ModelResponse, ToolCall, ToolResult, ToolResultStatus
from mini_harness.models.anthropic import AnthropicModelClient


def test_model_response_requires_content_or_tool_call() -> None:
    with pytest.raises(ValidationError):
        ModelResponse()


def test_message_from_tool_result_preserves_pairing() -> None:
    result = ToolResult(
        tool_call_id="call_1",
        tool_name="read_file",
        status=ToolResultStatus.SUCCESS,
        output="hello",
        duration_ms=3,
    )

    message = Message.from_tool_result(result)

    assert message.role == "tool"
    assert message.tool_call_id == "call_1"
    assert message.tool_name == "read_file"
    assert message.content == "hello"


def test_response_kind_prefers_tool_calls() -> None:
    response = ModelResponse(
        content="I will inspect it.",
        tool_calls=[ToolCall(id="call_1", name="read_file", arguments={"path": "a.txt"})],
    )

    assert response.kind == "tool_call"


def test_load_config_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[mini_harness]\nmax_turns = 4\nshell_policy = "deny"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MINI_HARNESS_MAX_TURNS", "5")

    config = load_config(config_path, max_turns=6, workspace=tmp_path)

    assert config.max_turns == 6
    assert config.shell_policy == "deny"
    assert config.workspace == tmp_path.resolve()


def test_relative_trace_directory_is_workspace_relative(tmp_path: Path) -> None:
    config = HarnessConfig(workspace=tmp_path, trace_dir=Path("trace-output"))

    assert config.resolved_trace_dir() == (tmp_path / "trace-output").resolve()


def test_anthropic_adapter_groups_parallel_tool_results() -> None:
    messages = [
        Message(role="user", content="read both"),
        Message(
            role="assistant",
            tool_calls=[
                ToolCall(id="a", name="read_file", arguments={"path": "a.txt"}),
                ToolCall(id="b", name="read_file", arguments={"path": "b.txt"}),
            ],
        ),
        Message(
            role="tool",
            content="alpha",
            tool_call_id="a",
            tool_name="read_file",
            tool_status=ToolResultStatus.SUCCESS,
        ),
        Message(
            role="tool",
            content="missing",
            tool_call_id="b",
            tool_name="read_file",
            tool_status=ToolResultStatus.ERROR,
        ),
    ]

    converted: list[MessageParam] = AnthropicModelClient._to_anthropic_messages(messages)

    assert len(converted) == 3
    tool_result_message = converted[2]
    assert tool_result_message["role"] == "user"
    content = tool_result_message["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0]["tool_use_id"] == "a"
    assert content[1]["tool_use_id"] == "b"
    assert content[1]["is_error"] is True
