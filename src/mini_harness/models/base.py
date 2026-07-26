"""Provider-neutral model-client protocol."""

from typing import Protocol

from mini_harness.messages import Message, ModelResponse
from mini_harness.tools.base import ToolDefinition


class ModelClient(Protocol):
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        """Return the next model response for the current conversation."""
