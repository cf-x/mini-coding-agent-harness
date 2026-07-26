"""Trace event names and lightweight validation."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal[
    "run_started",
    "model_request",
    "model_response",
    "tool_requested",
    "policy_decided",
    "tool_started",
    "tool_finished",
    "run_finished",
    "run_failed",
]


class TraceEvent(BaseModel):
    """Common event envelope; event-specific fields remain top-level JSON."""

    model_config = ConfigDict(extra="allow")

    type: EventType
    timestamp: datetime
    run_id: str
    sequence: int = Field(ge=1)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)
