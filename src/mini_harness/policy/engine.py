"""Ordered policy evaluation before tool execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from mini_harness.errors import PathOutsideWorkspaceError
from mini_harness.messages import ToolCall
from mini_harness.policy.rules import (
    PolicyDecision,
    PolicyOutcome,
    classify_dangerous_shell,
)
from mini_harness.tools.base import resolve_workspace_path


class PolicyEngine:
    """Small risk classifier; this is deliberately not an OS sandbox."""

    _FILE_TOOLS = {"read_file", "write_file", "edit_file"}

    def __init__(
        self,
        workspace: Path,
        *,
        write_policy: Literal["allow", "ask", "deny"] = "ask",
        shell_policy: Literal["allow", "ask", "deny"] = "ask",
    ) -> None:
        self.workspace = workspace.resolve()
        self.write_policy = PolicyDecision(write_policy)
        self.shell_policy = PolicyDecision(shell_policy)

    def evaluate(self, call: ToolCall) -> PolicyOutcome:
        arguments = self._normalize_arguments(call)

        if call.name in self._FILE_TOOLS:
            path = call.arguments.get("path")
            if not isinstance(path, str):
                return PolicyOutcome(
                    decision=PolicyDecision.ALLOW,
                    rule="invalid_arguments_passthrough",
                    reason="registry will return a structured validation error",
                    normalized_arguments=arguments,
                )
            try:
                resolved = resolve_workspace_path(self.workspace, path)
            except PathOutsideWorkspaceError:
                return PolicyOutcome(
                    decision=PolicyDecision.DENY,
                    rule="workspace_boundary",
                    reason="file path resolves outside the workspace",
                    normalized_arguments=arguments,
                )
            arguments["path"] = resolved.relative_to(self.workspace).as_posix()

            if call.name == "read_file":
                return PolicyOutcome(
                    decision=PolicyDecision.ALLOW,
                    rule="workspace_read",
                    reason="read-only access inside the workspace",
                    normalized_arguments=arguments,
                )
            return PolicyOutcome(
                decision=self.write_policy,
                rule="workspace_write",
                reason=f"workspace writes are configured as {self.write_policy.value}",
                normalized_arguments=arguments,
            )

        if call.name == "bash":
            command = call.arguments.get("command")
            if not isinstance(command, str):
                return PolicyOutcome(
                    decision=PolicyDecision.ALLOW,
                    rule="invalid_arguments_passthrough",
                    reason="registry will return a structured validation error",
                    normalized_arguments=arguments,
                )
            dangerous_reason = classify_dangerous_shell(command)
            if dangerous_reason is not None:
                return PolicyOutcome(
                    decision=PolicyDecision.DENY,
                    rule="dangerous_shell",
                    reason=f"blocked shell risk: {dangerous_reason}",
                    normalized_arguments=arguments,
                )
            return PolicyOutcome(
                decision=self.shell_policy,
                rule="shell_default",
                reason=f"shell commands are configured as {self.shell_policy.value}",
                normalized_arguments=arguments,
            )

        return PolicyOutcome(
            decision=PolicyDecision.ALLOW,
            rule="unknown_tool_passthrough",
            reason="registry will return a structured unknown-tool error",
            normalized_arguments=arguments,
        )

    @staticmethod
    def _normalize_arguments(call: ToolCall) -> dict[str, Any]:
        normalized = _normalize_value(call.arguments)
        if not isinstance(normalized, dict):
            return {}
        return normalized


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value
