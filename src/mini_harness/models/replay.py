"""Model client backed by responses from a previous trace."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from mini_harness.errors import ReplayExhaustedError
from mini_harness.messages import Message, ModelResponse
from mini_harness.tools.base import ToolDefinition
from mini_harness.trace.reader import TraceReader


class ReplayModelClient:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = deepcopy(responses)
        self._position = 0

    @classmethod
    def from_trace(cls, path: Path) -> ReplayModelClient:
        return cls(TraceReader(path).model_responses())

    @property
    def consumed(self) -> int:
        return self._position

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        del messages, tools
        if self._position >= len(self._responses):
            raise ReplayExhaustedError("replay trace has no remaining model response")
        response = self._responses[self._position]
        self._position += 1
        return response.model_copy(deep=True)
