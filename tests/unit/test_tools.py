import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from mini_harness.errors import PathOutsideWorkspaceError
from mini_harness.messages import ToolCall, ToolResultStatus
from mini_harness.tools import default_registry
from mini_harness.tools.base import (
    Tool,
    ToolContext,
    ToolExecution,
    resolve_workspace_path,
)
from mini_harness.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from mini_harness.tools.registry import ToolRegistry


def context(workspace: Path, *, timeout: float = 1, maximum: int = 1000) -> ToolContext:
    return ToolContext(
        workspace=workspace,
        timeout_seconds=timeout,
        max_output_chars=maximum,
    )


def test_resolve_workspace_path_rejects_parent_escape(tmp_path: Path) -> None:
    with pytest.raises(PathOutsideWorkspaceError):
        resolve_workspace_path(tmp_path, "../outside.txt")


def test_resolve_workspace_path_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathOutsideWorkspaceError):
        resolve_workspace_path(tmp_path, "link/secret.txt")


@pytest.mark.asyncio
async def test_file_tools_round_trip_and_exact_edit(tmp_path: Path) -> None:
    write = WriteFileTool()
    read = ReadFileTool()
    edit = EditFileTool()

    await write.execute(
        write.arguments_model(path="nested/a.txt", content="alpha beta"),
        context(tmp_path),
    )
    await edit.execute(
        edit.arguments_model(
            path="nested/a.txt",
            old_text="beta",
            new_text="gamma",
        ),
        context(tmp_path),
    )
    result = await read.execute(
        read.arguments_model(path="nested/a.txt"),
        context(tmp_path),
    )

    assert result.output == "alpha gamma"


@pytest.mark.asyncio
async def test_edit_rejects_ambiguous_match(tmp_path: Path) -> None:
    path = tmp_path / "repeat.txt"
    path.write_text("same same", encoding="utf-8")
    tool = EditFileTool()

    with pytest.raises(ValueError, match="matched 2 times"):
        await tool.execute(
            tool.arguments_model(
                path="repeat.txt",
                old_text="same",
                new_text="new",
            ),
            context(tmp_path),
        )


@pytest.mark.asyncio
async def test_registry_returns_unknown_tool_error(tmp_path: Path) -> None:
    result = await default_registry().execute(
        ToolCall(id="unknown", name="no_such_tool", arguments={}),
        context(tmp_path),
    )

    assert result.status is ToolResultStatus.ERROR
    assert "unknown tool" in result.output


@pytest.mark.asyncio
async def test_registry_returns_argument_validation_error(tmp_path: Path) -> None:
    result = await default_registry().execute(
        ToolCall(id="bad", name="read_file", arguments={}),
        context(tmp_path),
    )

    assert result.status is ToolResultStatus.ERROR
    assert "invalid arguments" in result.output


class TextArguments(BaseModel):
    text: str = ""


class EchoTool(Tool):
    name = "echo"
    description = "Return text."
    arguments_model = TextArguments

    async def execute(self, arguments: BaseModel, context: ToolContext) -> ToolExecution:
        del context
        values = TextArguments.model_validate(arguments)
        return ToolExecution(output=values.text)


class SlowTool(Tool):
    name = "slow"
    description = "Sleep."
    arguments_model = TextArguments

    async def execute(self, arguments: BaseModel, context: ToolContext) -> ToolExecution:
        del arguments, context
        import asyncio

        await asyncio.sleep(5)
        return ToolExecution(output="late")


class ErrorTool(Tool):
    name = "error"
    description = "Raise a large error."
    arguments_model = TextArguments

    async def execute(self, arguments: BaseModel, context: ToolContext) -> ToolExecution:
        del arguments, context
        raise RuntimeError("x" * 200)


@pytest.mark.asyncio
async def test_registry_truncates_output(tmp_path: Path) -> None:
    registry = ToolRegistry([EchoTool()])

    result = await registry.execute(
        ToolCall(id="echo", name="echo", arguments={"text": "x" * 200}),
        context(tmp_path, maximum=64),
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert result.truncated is True
    assert len(result.output) == 64
    assert "truncated" in result.output


@pytest.mark.asyncio
async def test_registry_times_out_tool(tmp_path: Path) -> None:
    registry = ToolRegistry([SlowTool()])
    started = time.monotonic()

    result = await registry.execute(
        ToolCall(id="slow", name="slow", arguments={}),
        context(tmp_path, timeout=0.02),
    )

    assert result.status is ToolResultStatus.TIMEOUT
    assert time.monotonic() - started < 1


@pytest.mark.asyncio
async def test_registry_truncates_error_output(tmp_path: Path) -> None:
    registry = ToolRegistry([ErrorTool()])

    result = await registry.execute(
        ToolCall(id="error", name="error", arguments={}),
        context(tmp_path, maximum=64),
    )

    assert result.status is ToolResultStatus.ERROR
    assert result.truncated is True
    assert len(result.output) == 64


@pytest.mark.asyncio
async def test_bash_timeout_kills_process_group(tmp_path: Path) -> None:
    registry = default_registry()

    result = await registry.execute(
        ToolCall(id="bash", name="bash", arguments={"command": "sleep 5"}),
        context(tmp_path, timeout=0.05),
    )

    assert result.status is ToolResultStatus.TIMEOUT


@pytest.mark.asyncio
async def test_bash_does_not_inherit_secret_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-shell")

    result = await default_registry().execute(
        ToolCall(
            id="env",
            name="bash",
            arguments={"command": 'printf \'%s:%s\' "$HOME" "$ANTHROPIC_API_KEY"'},
        ),
        context(tmp_path),
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert "must-not-reach-shell" not in result.output
    assert result.output == f"{tmp_path}:"
