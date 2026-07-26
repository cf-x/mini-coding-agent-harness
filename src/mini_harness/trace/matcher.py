"""Find the first normalized tool-call divergence between two traces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from mini_harness.trace.reader import TraceReader


class ToolInvocation(BaseModel):
    turn: int
    tool: str
    arguments: dict[str, Any]


class Divergence(BaseModel):
    turn: int
    expected_tool: str | None
    actual_tool: str | None
    expected_arguments: dict[str, Any] | None
    actual_arguments: dict[str, Any] | None
    reason: str


class TraceMatcher:
    def compare(self, expected_trace: Path, actual_trace: Path) -> Divergence | None:
        expected = self.invocations(TraceReader(expected_trace).read())
        actual = self.invocations(TraceReader(actual_trace).read())
        return self.compare_invocations(expected, actual)

    def compare_invocations(
        self,
        expected: list[ToolInvocation],
        actual: list[ToolInvocation],
    ) -> Divergence | None:
        for index in range(max(len(expected), len(actual))):
            expected_item = expected[index] if index < len(expected) else None
            actual_item = actual[index] if index < len(actual) else None
            if expected_item is None:
                assert actual_item is not None
                return Divergence(
                    turn=actual_item.turn,
                    expected_tool=None,
                    actual_tool=actual_item.tool,
                    expected_arguments=None,
                    actual_arguments=actual_item.arguments,
                    reason="actual replay has an unexpected extra tool call",
                )
            if actual_item is None:
                return Divergence(
                    turn=expected_item.turn,
                    expected_tool=expected_item.tool,
                    actual_tool=None,
                    expected_arguments=expected_item.arguments,
                    actual_arguments=None,
                    reason="actual replay ended before the expected tool call",
                )
            if expected_item.tool != actual_item.tool:
                return Divergence(
                    turn=actual_item.turn,
                    expected_tool=expected_item.tool,
                    actual_tool=actual_item.tool,
                    expected_arguments=expected_item.arguments,
                    actual_arguments=actual_item.arguments,
                    reason="tool names differ",
                )
            if _canonical(expected_item.arguments) != _canonical(actual_item.arguments):
                return Divergence(
                    turn=actual_item.turn,
                    expected_tool=expected_item.tool,
                    actual_tool=actual_item.tool,
                    expected_arguments=expected_item.arguments,
                    actual_arguments=actual_item.arguments,
                    reason="normalized tool arguments differ",
                )
        return None

    @staticmethod
    def invocations(events: list[dict[str, Any]]) -> list[ToolInvocation]:
        invocations: list[ToolInvocation] = []
        for event in events:
            if event["type"] != "policy_decided":
                continue
            arguments = event.get("normalized_arguments", {})
            invocations.append(
                ToolInvocation(
                    turn=int(event["turn"]),
                    tool=str(event["tool_name"]),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        return invocations


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value
