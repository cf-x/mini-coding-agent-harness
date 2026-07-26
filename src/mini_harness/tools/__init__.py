"""Built-in coding tools."""

from mini_harness.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from mini_harness.tools.registry import ToolRegistry
from mini_harness.tools.shell import BashTool

__all__ = ["BashTool", "EditFileTool", "ReadFileTool", "ToolRegistry", "WriteFileTool"]


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(BashTool())
    return registry
