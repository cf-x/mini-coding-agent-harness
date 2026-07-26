"""OpenAI Responses API adapter."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from openai import AsyncOpenAI, PermissionDeniedError

from mini_harness.messages import Message, ModelResponse, ModelUsage, ToolCall
from mini_harness.tools.base import ToolDefinition

DEFAULT_OPENAI_MODEL = "gpt-5.6-terra"
DEFAULT_SYSTEM_PROMPT = """You are a coding agent working inside one workspace.
Use the provided tools to inspect and modify the workspace. Keep changes scoped to the task.
Run the relevant tests before finishing.
When the task is complete, return a concise final response."""
OpenAIToolMode = Literal["auto", "function", "prompt"]
OpenAIClientProfile = Literal["standard", "codex"]
PROMPT_TOOL_RESPONSE_SHAPE = (
    '{"content":"text for the user or empty","tool_calls":['
    '{"id":"unique id","name":"tool name","arguments":{}}]}'
)


class OpenAIModelClient:
    """Translate harness messages to stateful Responses API requests.

    One client instance belongs to one agent run. ``previous_response_id`` keeps
    provider output items, including reasoning items, intact between tool turns.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_OPENAI_MODEL,
        max_output_tokens: int = 4096,
        api_key: str | None = None,
        base_url: str | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        tool_mode: OpenAIToolMode = "auto",
        client_profile: OpenAIClientProfile = "standard",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.system_prompt = system_prompt
        self.tool_mode = tool_mode
        self.client_profile = client_profile
        self._active_tool_mode: Literal["function", "prompt"] = (
            "function" if tool_mode in {"auto", "function"} else "prompt"
        )
        client_options: dict[str, Any] = {"api_key": api_key}
        if base_url is not None:
            client_options["base_url"] = base_url
        if client_profile == "codex":
            client_options["default_headers"] = {
                "User-Agent": (
                    "codex_cli_rs/0.114.0 (macOS 15.0; arm64) "
                    "Terminal.app (codex_cli_rs; 0.114.0)"
                ),
                "originator": "codex_cli_rs",
                "x-codex-window-id": str(uuid.uuid4()),
                "OpenAI-Beta": "responses=experimental",
            }
        self._client = client or AsyncOpenAI(**client_options)
        self._previous_response_id: str | None = None
        self._stateless_input: list[Any] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        if self._active_tool_mode == "prompt":
            return await self._complete_prompt_mode(messages, tools)
        try:
            return await self._complete_function_mode(messages, tools)
        except PermissionDeniedError:
            if self.tool_mode != "auto" or self._previous_response_id is not None:
                raise
            self._active_tool_mode = "prompt"
            return await self._complete_prompt_mode(messages, tools)

    async def _complete_function_mode(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": self.system_prompt,
            "input": self._function_request_input(messages),
            "tools": [self._tool(tool) for tool in tools],
            "max_output_tokens": self.max_output_tokens,
            "parallel_tool_calls": True,
            "store": self.client_profile != "codex",
        }
        if self.client_profile == "codex":
            request["include"] = ["reasoning.encrypted_content"]
        elif self._previous_response_id is not None:
            request["previous_response_id"] = self._previous_response_id

        response = await self._client.responses.create(**request)
        self._advance_conversation(request["input"], response)
        tool_calls: list[ToolCall] = []
        for item in response.output:
            if item.type != "function_call":
                continue
            arguments = json.loads(item.arguments)
            if not isinstance(arguments, dict):
                raise ValueError(f"function arguments must be an object: {item.name}")
            tool_calls.append(
                ToolCall(
                    id=item.call_id,
                    name=item.name,
                    arguments=arguments,
                )
            )

        return ModelResponse(
            content=response.output_text,
            tool_calls=tool_calls,
            stop_reason=response.status,
            usage=self._usage(response),
        )

    async def _complete_prompt_mode(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": self._prompt_instructions(tools),
            "input": self._prompt_request_input(messages),
            "max_output_tokens": self.max_output_tokens,
            "store": self.client_profile != "codex",
        }
        if self.client_profile == "codex":
            request["include"] = ["reasoning.encrypted_content"]
        elif self._previous_response_id is not None:
            request["previous_response_id"] = self._previous_response_id

        response = await self._client.responses.create(**request)
        self._advance_conversation(request["input"], response)
        content, tool_calls = self._parse_prompt_response(response.output_text)
        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=response.status,
            usage=self._usage(response),
        )

    def _function_request_input(self, messages: list[Message]) -> list[dict[str, Any]]:
        if self._stateless_input:
            tool_messages = self._latest_tool_messages(messages)
            if not tool_messages:
                raise ValueError("a continued Responses API turn requires at least one tool result")
            tool_outputs = [self._tool_output(message) for message in tool_messages]
            return [*self._stateless_input, *tool_outputs]
        if self._previous_response_id is None:
            return self._initial_input(messages)

        tool_messages = self._latest_tool_messages(messages)
        if not tool_messages:
            raise ValueError("a continued Responses API turn requires at least one tool result")
        return [self._tool_output(message) for message in tool_messages]

    def _prompt_request_input(self, messages: list[Message]) -> list[dict[str, Any]]:
        if not self._stateless_input and self._previous_response_id is None:
            return self._initial_input(messages)
        tool_messages = self._latest_tool_messages(messages)
        if not tool_messages:
            raise ValueError("a continued prompt-tool turn requires at least one tool result")
        results = [
            {
                "call_id": message.tool_call_id,
                "name": message.tool_name,
                "status": (
                    message.tool_status.value if message.tool_status is not None else "unknown"
                ),
                "output": message.content,
            }
            for message in tool_messages
        ]
        new_input = [
            {
                "role": "user",
                "content": (
                    "Tool execution results follow. Continue the task using these results:\n"
                    + json.dumps(results, ensure_ascii=True)
                ),
            }
        ]
        return [*self._stateless_input, *new_input]

    def _advance_conversation(self, request_input: list[Any], response: Any) -> None:
        if self.client_profile == "codex":
            self._stateless_input = [*request_input, *response.output]
            return
        self._previous_response_id = response.id

    @staticmethod
    def _initial_input(messages: list[Message]) -> list[dict[str, Any]]:
        assistant_messages = [message for message in messages if message.role == "assistant"]
        if assistant_messages:
            raise ValueError("a new OpenAIModelClient cannot resume an assistant conversation")
        return [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role == "user"
        ]

    @staticmethod
    def _latest_tool_messages(messages: list[Message]) -> list[Message]:
        last_assistant = max(
            (index for index, message in enumerate(messages) if message.role == "assistant"),
            default=-1,
        )
        return [message for message in messages[last_assistant + 1 :] if message.role == "tool"]

    def _prompt_instructions(self, tools: list[ToolDefinition]) -> str:
        definitions = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
            for tool in tools
        ]
        return f"""{self.system_prompt}

This API gateway does not expose native function tools. Emulate tool calls using one JSON object.
Return JSON only, without Markdown fences or surrounding prose, in exactly this shape:
{PROMPT_TOOL_RESPONSE_SHAPE}
Use tool_calls when workspace inspection or execution is needed. Use an empty tool_calls list only
when the task is complete. Tool arguments must satisfy the listed JSON Schema. Available tools:
{json.dumps(definitions, ensure_ascii=True, separators=(",", ":"))}"""

    @staticmethod
    def _parse_prompt_response(raw: str) -> tuple[str, list[ToolCall]]:
        candidate = raw.strip()
        if candidate.startswith("```"):
            first_newline = candidate.find("\n")
            candidate = candidate[first_newline + 1 :] if first_newline >= 0 else candidate
            if candidate.endswith("```"):
                candidate = candidate[:-3].rstrip()
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("prompt-tool response is not a JSON object") from None
            payload = json.loads(candidate[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("prompt-tool response must be a JSON object")
        content = payload.get("content", "")
        calls = payload.get("tool_calls", [])
        if not isinstance(content, str) or not isinstance(calls, list):
            raise ValueError("prompt-tool response has invalid content or tool_calls")
        tool_calls: list[ToolCall] = []
        for call in calls:
            if not isinstance(call, dict):
                raise ValueError("prompt-tool call must be an object")
            tool_calls.append(ToolCall.model_validate(call))
        return content, tool_calls

    @staticmethod
    def _usage(response: Any) -> ModelUsage | None:
        if response.usage is None:
            return None
        input_details = response.usage.input_tokens_details
        return ModelUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_creation_input_tokens=input_details.cache_write_tokens,
            cache_read_input_tokens=input_details.cached_tokens,
            request_count=1,
        )

    @staticmethod
    def _tool(tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }

    @staticmethod
    def _tool_output(message: Message) -> dict[str, Any]:
        if message.tool_call_id is None:
            raise ValueError("tool message is missing tool_call_id")
        output = json.dumps(
            {
                "status": (
                    message.tool_status.value if message.tool_status is not None else "unknown"
                ),
                "output": message.content,
            },
            ensure_ascii=True,
        )
        return {
            "type": "function_call_output",
            "call_id": message.tool_call_id,
            "output": output,
        }
