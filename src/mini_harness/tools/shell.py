"""Timeout-aware workspace shell tool."""

from __future__ import annotations

import asyncio
import os
import signal

from pydantic import BaseModel, Field

from mini_harness.tools.base import Tool, ToolContext, ToolExecution


class BashArguments(BaseModel):
    command: str = Field(min_length=1)


class BashTool(Tool):
    name = "bash"
    description = "Run a shell command with the workspace as the current directory."
    arguments_model = BashArguments

    async def execute(self, arguments: BaseModel, context: ToolContext) -> ToolExecution:
        values = BashArguments.model_validate(arguments)
        process = await asyncio.create_subprocess_shell(
            values.command,
            cwd=context.workspace,
            env=self._minimal_environment(context),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            stdout, _ = await process.communicate()
        except asyncio.CancelledError:
            self._terminate_process_group(process)
            await process.wait()
            raise
        output = stdout.decode("utf-8", errors="replace")
        return ToolExecution(output=output, exit_code=process.returncode)

    @staticmethod
    def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _minimal_environment(context: ToolContext) -> dict[str, str]:
        allowed = ("PATH", "LANG", "LC_ALL", "TERM", "TMPDIR")
        environment = {name: os.environ[name] for name in allowed if name in os.environ}
        environment["HOME"] = str(context.workspace)
        return environment
