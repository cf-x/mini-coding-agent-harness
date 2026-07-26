from pathlib import Path

from mini_harness.messages import ToolCall
from mini_harness.policy.engine import PolicyEngine
from mini_harness.policy.rules import PolicyDecision, classify_dangerous_shell


def call(name: str, arguments: dict[str, object]) -> ToolCall:
    return ToolCall(id="call_1", name=name, arguments=arguments)


def test_read_inside_workspace_is_allowed(tmp_path: Path) -> None:
    outcome = PolicyEngine(tmp_path).evaluate(call("read_file", {"path": "a.txt"}))

    assert outcome.decision is PolicyDecision.ALLOW
    assert outcome.rule == "workspace_read"
    assert outcome.normalized_arguments["path"] == "a.txt"


def test_read_outside_workspace_is_denied(tmp_path: Path) -> None:
    outcome = PolicyEngine(tmp_path).evaluate(call("read_file", {"path": "../a.txt"}))

    assert outcome.decision is PolicyDecision.DENY
    assert outcome.rule == "workspace_boundary"


def test_write_and_shell_modes_are_configurable(tmp_path: Path) -> None:
    policy = PolicyEngine(tmp_path, write_policy="ask", shell_policy="deny")

    write = policy.evaluate(call("write_file", {"path": "a.txt", "content": "x"}))
    shell = policy.evaluate(call("bash", {"command": "pytest -q"}))

    assert write.decision is PolicyDecision.ASK
    assert shell.decision is PolicyDecision.DENY


def test_dangerous_rule_has_priority_over_allow_mode(tmp_path: Path) -> None:
    outcome = PolicyEngine(tmp_path, shell_policy="allow").evaluate(
        call("bash", {"command": "rm -rf build"})
    )

    assert outcome.decision is PolicyDecision.DENY
    assert outcome.rule == "dangerous_shell"


def test_unknown_tool_passes_to_registry_for_structured_error(tmp_path: Path) -> None:
    outcome = PolicyEngine(tmp_path).evaluate(call("search_file", {"query": "TODO"}))

    assert outcome.decision is PolicyDecision.ALLOW
    assert outcome.rule == "unknown_tool_passthrough"


def test_normalization_does_not_change_write_content(tmp_path: Path) -> None:
    content = "  significant surrounding spaces  "

    outcome = PolicyEngine(tmp_path, write_policy="allow").evaluate(
        call("write_file", {"path": "./a.txt", "content": content})
    )

    assert outcome.normalized_arguments == {"content": content, "path": "a.txt"}


def test_shell_risk_classifier_examples() -> None:
    assert classify_dangerous_shell("git reset --hard") == "destructive git"
    assert classify_dangerous_shell("dd if=/dev/zero of=/dev/disk1") == "raw device write"
    assert classify_dangerous_shell("python3 -m unittest -q") is None
