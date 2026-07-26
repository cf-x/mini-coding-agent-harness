"""Shared message, tool-call, and result models."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ToolCall(BaseModel):
    """A model request to invoke one registered tool."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"
    TIMEOUT = "timeout"


class ToolResult(BaseModel):
    """The normalized result returned to the model after a tool call."""

    tool_call_id: str
    tool_name: str
    status: ToolResultStatus
    output: str
    duration_ms: int = Field(ge=0)
    truncated: bool = False
    exit_code: int | None = None


class Message(BaseModel):
    """Provider-neutral conversation message."""

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_status: ToolResultStatus | None = None

    @classmethod
    def from_tool_result(cls, result: ToolResult) -> "Message":
        return cls(
            role="tool",
            content=result.output,
            tool_call_id=result.tool_call_id,
            tool_name=result.tool_name,
            tool_status=result.status,
        )


class ModelResponse(BaseModel):
    """Provider-neutral response containing text, tool calls, or both."""

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str | None = None

    @model_validator(mode="after")
    def require_content_or_tool(self) -> "ModelResponse":
        if not self.content and not self.tool_calls:
            raise ValueError("a model response must contain text or at least one tool call")
        return self

    @property
    def kind(self) -> Literal["text", "tool_call"]:
        return "tool_call" if self.tool_calls else "text"
