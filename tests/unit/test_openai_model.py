import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import AsyncOpenAI

import mini_harness.models.openai as openai_module
from mini_harness.messages import Message, ToolCall, ToolResultStatus
from mini_harness.models.openai import OpenAIModelClient
from mini_harness.tools.base import ToolDefinition


class FakeResponses:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def create(self, **request: Any) -> Any:
        self.requests.append(request)
        return self.responses.pop(0)


def fake_response(
    response_id: str,
    *,
    output_text: str = "",
    output: list[Any] | None = None,
    input_tokens: int = 20,
    output_tokens: int = 5,
    cached_tokens: int = 4,
) -> Any:
    return SimpleNamespace(
        id=response_id,
        output=output or [],
        output_text=output_text,
        status="completed",
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_tokens_details=SimpleNamespace(
                cache_write_tokens=0,
                cached_tokens=cached_tokens,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_openai_adapter_handles_parallel_calls_and_tool_outputs() -> None:
    responses = FakeResponses(
        [
            fake_response(
                "resp_1",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        call_id="call_a",
                        name="read_file",
                        arguments='{"path":"a.txt"}',
                    ),
                    SimpleNamespace(
                        type="function_call",
                        call_id="call_b",
                        name="read_file",
                        arguments='{"path":"b.txt"}',
                    ),
                ],
            ),
            fake_response("resp_2", output_text="done", cached_tokens=10),
        ]
    )
    client = OpenAIModelClient(
        model="gpt-test",
        api_key="test-key",
        client=cast(AsyncOpenAI, SimpleNamespace(responses=responses)),
    )
    tools = [
        ToolDefinition(
            name="read_file",
            description="Read a file.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
    ]

    first = await client.complete([Message(role="user", content="read both")], tools)
    second = await client.complete(
        [
            Message(role="user", content="read both"),
            Message(role="assistant", tool_calls=first.tool_calls),
            Message(
                role="tool",
                content="alpha",
                tool_call_id="call_a",
                tool_name="read_file",
                tool_status=ToolResultStatus.SUCCESS,
            ),
            Message(
                role="tool",
                content="missing",
                tool_call_id="call_b",
                tool_name="read_file",
                tool_status=ToolResultStatus.ERROR,
            ),
        ],
        tools,
    )

    assert first.tool_calls == [
        ToolCall(id="call_a", name="read_file", arguments={"path": "a.txt"}),
        ToolCall(id="call_b", name="read_file", arguments={"path": "b.txt"}),
    ]
    assert first.usage is not None
    assert first.usage.total_tokens == 25
    assert first.usage.cache_read_input_tokens == 4
    assert second.content == "done"
    continued = responses.requests[1]
    assert continued["previous_response_id"] == "resp_1"
    assert [item["call_id"] for item in continued["input"]] == ["call_a", "call_b"]
    first_output = json.loads(continued["input"][0]["output"])
    second_output = json.loads(continued["input"][1]["output"])
    assert first_output == {"status": "success", "output": "alpha"}
    assert second_output == {"status": "error", "output": "missing"}


def test_openai_tool_definition_uses_responses_shape() -> None:
    definition = ToolDefinition(
        name="bash",
        description="Run a command.",
        input_schema={"type": "object"},
    )

    assert OpenAIModelClient._tool(definition) == {
        "type": "function",
        "name": "bash",
        "description": "Run a command.",
        "parameters": {"type": "object"},
    }


def test_codex_client_profile_sets_gateway_identity_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **options: Any) -> None:
            captured.update(options)

    monkeypatch.setattr(openai_module, "AsyncOpenAI", FakeAsyncOpenAI)

    OpenAIModelClient(
        model="gpt-test",
        api_key="test-key",
        base_url="https://gateway.example/v1",
        client_profile="codex",
    )

    headers = captured["default_headers"]
    assert headers["User-Agent"].startswith("codex_cli_rs/0.114.0")
    assert headers["originator"] == "codex_cli_rs"
    assert headers["x-codex-window-id"]
    assert headers["OpenAI-Beta"] == "responses=experimental"


@pytest.mark.asyncio
async def test_codex_client_profile_uses_stateless_http_continuation() -> None:
    first_output = SimpleNamespace(
        type="function_call",
        call_id="call_codex",
        name="read_file",
        arguments='{"path":"note.txt"}',
    )
    responses = FakeResponses(
        [
            fake_response("resp_codex_1", output=[first_output]),
            fake_response("resp_codex_2", output_text="done"),
        ]
    )
    client = OpenAIModelClient(
        model="gpt-test",
        api_key="test-key",
        client_profile="codex",
        client=cast(AsyncOpenAI, SimpleNamespace(responses=responses)),
    )
    tools = [
        ToolDefinition(
            name="read_file",
            description="Read a file.",
            input_schema={"type": "object"},
        )
    ]

    first = await client.complete([Message(role="user", content="read note")], tools)
    await client.complete(
        [
            Message(role="user", content="read note"),
            Message(role="assistant", tool_calls=first.tool_calls),
            Message(
                role="tool",
                content="hello",
                tool_call_id="call_codex",
                tool_name="read_file",
                tool_status=ToolResultStatus.SUCCESS,
            ),
        ],
        tools,
    )

    initial_request, continued_request = responses.requests
    assert initial_request["store"] is False
    assert initial_request["include"] == ["reasoning.encrypted_content"]
    assert "previous_response_id" not in continued_request
    assert continued_request["input"][0] == {"role": "user", "content": "read note"}
    assert continued_request["input"][1] is first_output
    assert continued_request["input"][2]["type"] == "function_call_output"


@pytest.mark.asyncio
async def test_prompt_tool_mode_parses_calls_and_continues_with_results() -> None:
    responses = FakeResponses(
        [
            fake_response(
                "resp_prompt_1",
                output_text=json.dumps(
                    {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "prompt_call",
                                "name": "read_file",
                                "arguments": {"path": "note.txt"},
                            }
                        ],
                    }
                ),
            ),
            fake_response(
                "resp_prompt_2",
                output_text='{"content":"finished","tool_calls":[]}',
            ),
        ]
    )
    client = OpenAIModelClient(
        model="gpt-test",
        api_key="test-key",
        tool_mode="prompt",
        client=cast(AsyncOpenAI, SimpleNamespace(responses=responses)),
    )
    tools = [
        ToolDefinition(
            name="read_file",
            description="Read a file.",
            input_schema={"type": "object"},
        )
    ]

    first = await client.complete([Message(role="user", content="read note")], tools)
    second = await client.complete(
        [
            Message(role="user", content="read note"),
            Message(role="assistant", tool_calls=first.tool_calls),
            Message(
                role="tool",
                content="hello",
                tool_call_id="prompt_call",
                tool_name="read_file",
                tool_status=ToolResultStatus.SUCCESS,
            ),
        ],
        tools,
    )

    assert first.tool_calls[0].name == "read_file"
    assert second.content == "finished"
    assert "tools" not in responses.requests[0]
    assert "Available tools" in responses.requests[0]["instructions"]
    assert responses.requests[1]["previous_response_id"] == "resp_prompt_1"
    assert "prompt_call" in responses.requests[1]["input"][0]["content"]


def test_prompt_tool_parser_accepts_markdown_fence() -> None:
    content, calls = OpenAIModelClient._parse_prompt_response(
        '```json\n{"content":"done","tool_calls":[]}\n```'
    )

    assert content == "done"
    assert calls == []
