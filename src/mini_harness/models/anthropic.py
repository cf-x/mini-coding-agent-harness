"""Anthropic SDK adapter."""

from __future__ import annotations

from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam

from mini_harness.messages import Message, ModelResponse, ModelUsage, ToolCall
from mini_harness.tools.base import ToolDefinition

DEFAULT_SYSTEM_PROMPT = """You are a coding agent working inside one workspace.
Use the provided tools to inspect and modify the workspace. Keep changes scoped to the task.
Run the relevant tests before finishing.
After the relevant tests pass, do not call more tools. Return a concise final response."""


class AnthropicModelClient:
    def __init__(
        self,
        *,
        model: str,
        max_tokens: int = 4096,
        api_key: str | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self._client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system_prompt,
            messages=self._to_anthropic_messages(messages),
            tools=[
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ],
        )
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                arguments = block.input if isinstance(block.input, dict) else {}
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=arguments))
        return ModelResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            usage=ModelUsage(
                input_tokens=(
                    response.usage.input_tokens
                    + (response.usage.cache_creation_input_tokens or 0)
                    + (response.usage.cache_read_input_tokens or 0)
                ),
                output_tokens=response.usage.output_tokens,
                cache_creation_input_tokens=response.usage.cache_creation_input_tokens or 0,
                cache_read_input_tokens=response.usage.cache_read_input_tokens or 0,
                request_count=1,
            ),
        )

    @staticmethod
    def _to_anthropic_messages(messages: list[Message]) -> list[MessageParam]:
        converted: list[dict[str, Any]] = []
        position = 0
        while position < len(messages):
            message = messages[position]
            if message.role == "user":
                converted.append({"role": "user", "content": message.content})
            elif message.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                    for call in message.tool_calls
                )
                converted.append({"role": "assistant", "content": blocks})
            else:
                tool_results: list[dict[str, Any]] = []
                while position < len(messages) and messages[position].role == "tool":
                    tool_message = messages[position]
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_message.tool_call_id,
                            "content": tool_message.content,
                            "is_error": tool_message.tool_status is not None
                            and tool_message.tool_status.value != "success",
                        }
                    )
                    position += 1
                converted.append(
                    {
                        "role": "user",
                        "content": tool_results,
                    }
                )
                continue
            position += 1
        return cast(list[MessageParam], converted)
