"""Tool abstractions and common execution helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from mini_harness.errors import PathOutsideWorkspaceError


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    workspace: Path
    timeout_seconds: float
    max_output_chars: int


class ToolExecution(BaseModel):
    output: str
    exit_code: int | None = None


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    arguments_model: ClassVar[type[BaseModel]]

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.arguments_model.model_json_schema(),
        )

    @abstractmethod
    async def execute(self, arguments: BaseModel, context: ToolContext) -> ToolExecution:
        """Execute validated arguments in a workspace."""


def resolve_workspace_path(workspace: Path, requested_path: str) -> Path:
    """Resolve a path and reject lexical or symlink escapes."""

    candidate = Path(requested_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError as exc:
        raise PathOutsideWorkspaceError(f"path is outside workspace: {requested_path}") from exc
    return resolved


def truncate_output(output: str, maximum: int) -> tuple[str, bool]:
    if len(output) <= maximum:
        return output, False
    omitted = len(output) - maximum
    marker = f"\n... [truncated {omitted} characters]"
    keep = max(0, maximum - len(marker))
    return output[:keep] + marker, True
