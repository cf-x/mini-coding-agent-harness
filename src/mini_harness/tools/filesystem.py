"""Workspace-scoped file tools."""

from pydantic import BaseModel, Field

from mini_harness.tools.base import (
    Tool,
    ToolContext,
    ToolExecution,
    resolve_workspace_path,
)


class ReadFileArguments(BaseModel):
    path: str = Field(description="Path relative to the workspace")


class WriteFileArguments(BaseModel):
    path: str = Field(description="Path relative to the workspace")
    content: str


class EditFileArguments(BaseModel):
    path: str = Field(description="Path relative to the workspace")
    old_text: str = Field(min_length=1)
    new_text: str
    replace_all: bool = False


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a UTF-8 text file inside the workspace."
    arguments_model = ReadFileArguments

    async def execute(self, arguments: BaseModel, context: ToolContext) -> ToolExecution:
        values = ReadFileArguments.model_validate(arguments)
        path = resolve_workspace_path(context.workspace, values.path)
        return ToolExecution(output=path.read_text(encoding="utf-8"))


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create a new UTF-8 file or completely rewrite one inside the workspace. "
        "Prefer edit_file for a local change to an existing file."
    )
    arguments_model = WriteFileArguments

    async def execute(self, arguments: BaseModel, context: ToolContext) -> ToolExecution:
        values = WriteFileArguments.model_validate(arguments)
        path = resolve_workspace_path(context.workspace, values.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(values.content, encoding="utf-8")
        relative = path.relative_to(context.workspace)
        return ToolExecution(output=f"wrote {len(values.content)} characters to {relative}")


class EditFileTool(Tool):
    name = "edit_file"
    description = "Make a precise local change to an existing UTF-8 file by replacing exact text."
    arguments_model = EditFileArguments

    async def execute(self, arguments: BaseModel, context: ToolContext) -> ToolExecution:
        values = EditFileArguments.model_validate(arguments)
        path = resolve_workspace_path(context.workspace, values.path)
        original = path.read_text(encoding="utf-8")
        occurrences = original.count(values.old_text)
        if occurrences == 0:
            raise ValueError("old_text was not found")
        if occurrences > 1 and not values.replace_all:
            raise ValueError(
                f"old_text matched {occurrences} times; set replace_all=true to replace all"
            )
        count = -1 if values.replace_all else 1
        updated = original.replace(values.old_text, values.new_text, count)
        path.write_text(updated, encoding="utf-8")
        relative = path.relative_to(context.workspace)
        replaced = occurrences if values.replace_all else 1
        return ToolExecution(output=f"replaced {replaced} occurrence(s) in {relative}")
