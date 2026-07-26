"""Workspace shell tool backed by an injectable command executor."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mini_harness.executors import (
    CommandExecutor,
    LocalCommandExecutor,
    shell_environment,
)
from mini_harness.tools.base import Tool, ToolContext, ToolExecution


class BashArguments(BaseModel):
    command: str = Field(min_length=1)


class BashTool(Tool):
    name = "bash"
    description = "Run a shell command with the workspace as the current directory."
    arguments_model = BashArguments

    def __init__(self, executor: CommandExecutor | None = None) -> None:
        self.executor = executor or LocalCommandExecutor()

    async def execute(self, arguments: BaseModel, context: ToolContext) -> ToolExecution:
        values = BashArguments.model_validate(arguments)
        execution = await self.executor.run(
            values.command,
            workspace=context.workspace,
        )
        return ToolExecution(output=execution.output, exit_code=execution.exit_code)


__all__ = ["BashArguments", "BashTool", "shell_environment"]
