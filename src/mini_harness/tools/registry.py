"""Tool registration, argument validation, timeout, and result normalization."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

from pydantic import ValidationError

from mini_harness.messages import ToolCall, ToolResult, ToolResultStatus
from mini_harness.tools.base import Tool, ToolContext, ToolDefinition, truncate_output


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    @property
    def definitions(self) -> list[ToolDefinition]:
        return [tool.definition for tool in self._tools.values()]

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        started = time.monotonic()
        tool = self._tools.get(call.name)
        if tool is None:
            return self._result(
                call,
                ToolResultStatus.ERROR,
                f"unknown tool: {call.name}",
                started,
                context.max_output_chars,
            )

        try:
            validated = tool.arguments_model.model_validate(call.arguments)
        except ValidationError as exc:
            return self._result(
                call,
                ToolResultStatus.ERROR,
                f"invalid arguments: {exc}",
                started,
                context.max_output_chars,
            )

        try:
            execution = await asyncio.wait_for(
                tool.execute(validated, context),
                timeout=context.timeout_seconds,
            )
        except TimeoutError:
            return self._result(
                call,
                ToolResultStatus.TIMEOUT,
                f"tool timed out after {context.timeout_seconds:g}s",
                started,
                context.max_output_chars,
            )
        except Exception as exc:
            return self._result(
                call,
                ToolResultStatus.ERROR,
                f"{type(exc).__name__}: {exc}",
                started,
                context.max_output_chars,
            )

        output, truncated = truncate_output(execution.output, context.max_output_chars)
        return ToolResult(
            tool_call_id=call.id,
            tool_name=call.name,
            status=ToolResultStatus.SUCCESS,
            output=output,
            duration_ms=self._duration_ms(started),
            truncated=truncated,
            exit_code=execution.exit_code,
        )

    @classmethod
    def _result(
        cls,
        call: ToolCall,
        status: ToolResultStatus,
        output: str,
        started: float,
        max_output_chars: int,
    ) -> ToolResult:
        limited_output, truncated = truncate_output(output, max_output_chars)
        return ToolResult(
            tool_call_id=call.id,
            tool_name=call.name,
            status=status,
            output=limited_output,
            duration_ms=cls._duration_ms(started),
            truncated=truncated,
        )

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))
