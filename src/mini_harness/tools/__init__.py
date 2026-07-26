"""Built-in coding tools."""

from mini_harness.executors import CommandExecutor
from mini_harness.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from mini_harness.tools.registry import ToolRegistry
from mini_harness.tools.shell import BashTool

__all__ = [
    "BashTool",
    "CommandExecutor",
    "EditFileTool",
    "ReadFileTool",
    "ToolRegistry",
    "WriteFileTool",
]


def default_registry(executor: CommandExecutor | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(BashTool(executor))
    return registry
