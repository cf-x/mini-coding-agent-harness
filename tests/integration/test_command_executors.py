import os
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from mini_harness.executors import (
    CommandExecutor,
    DockerCommandExecutor,
    LocalCommandExecutor,
    docker_cli_environment,
)
from mini_harness.messages import ToolCall, ToolResultStatus
from mini_harness.tools import default_registry
from mini_harness.tools.base import ToolContext

DOCKER_TEST_IMAGE = os.getenv("MINI_HARNESS_DOCKER_TEST_IMAGE", "python:3.12-slim")


@pytest.fixture(params=("local", "docker"))
def command_executor(request: pytest.FixtureRequest) -> CommandExecutor:
    if request.param == "local":
        return LocalCommandExecutor()
    return _docker_executor_or_skip()


@pytest.mark.asyncio
async def test_executor_contract_captures_streams_and_exit_code(
    command_executor: CommandExecutor,
    tmp_path: Path,
) -> None:
    execution = await command_executor.run(
        "printf 'standard-output'; printf 'standard-error' >&2; exit 7",
        workspace=tmp_path,
    )

    assert execution.stdout == "standard-output"
    assert execution.stderr == "standard-error"
    assert execution.output == "standard-outputstandard-error"
    assert execution.exit_code == 7


@pytest.mark.asyncio
async def test_executor_contract_uses_writable_workspace(
    command_executor: CommandExecutor,
    tmp_path: Path,
) -> None:
    (tmp_path / "input.txt").write_text("input", encoding="utf-8")

    execution = await command_executor.run(
        "cat input.txt && printf 'output' > output.txt",
        workspace=tmp_path,
    )

    assert execution.exit_code == 0
    assert execution.stdout == "input"
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "output"


@pytest.mark.asyncio
async def test_docker_real_isolation_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _docker_executor_or_skip()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "input.txt").write_text("workspace-input", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("host-only", encoding="utf-8")
    monkeypatch.setenv("MINI_HARNESS_TEST_SECRET", "must-not-reach-container")
    quoted_outside = shlex.quote(str(outside.resolve()))
    command = "\n".join(
        [
            'printf "uid=%s\\n" "$(id -u)"',
            'printf "input=%s\\n" "$(cat input.txt)"',
            "printf 'workspace-output' > output.txt",
            (
                f"if test -e {quoted_outside}; then printf 'outside=visible\\n'; "
                "else printf 'outside=hidden\\n'; fi"
            ),
            'printf "secret=%s\\n" "${MINI_HARNESS_TEST_SECRET-unset}"',
            "printf \"interfaces=%s\\n\" \"$(ls /sys/class/net | tr '\\n' ',')\"",
        ]
    )

    execution = await executor.run(command, workspace=workspace)

    assert execution.exit_code == 0, execution.output
    values = dict(line.split("=", 1) for line in execution.stdout.splitlines())
    assert int(values["uid"]) > 0
    assert values["input"] == "workspace-input"
    assert values["outside"] == "hidden"
    assert values["secret"] == "unset"
    assert {name for name in values["interfaces"].split(",") if name} == {"lo"}
    assert (workspace / "output.txt").read_text(encoding="utf-8") == "workspace-output"


@pytest.mark.asyncio
async def test_docker_timeout_leaves_no_container(tmp_path: Path) -> None:
    _docker_executor_or_skip()
    container_name = f"mini-harness-timeout-test-{uuid.uuid4().hex}"
    executor = DockerCommandExecutor(
        image=DOCKER_TEST_IMAGE,
        user=(os.getuid(), os.getgid()),
        container_name_factory=lambda: container_name,
    )
    context = ToolContext(
        workspace=tmp_path,
        timeout_seconds=0.2,
        max_output_chars=1000,
    )

    result = await default_registry(executor).execute(
        ToolCall(id="timeout", name="bash", arguments={"command": "sleep 30"}),
        context,
    )

    assert result.status is ToolResultStatus.TIMEOUT
    completed = _docker_cli(
        "ps",
        "--all",
        "--quiet",
        "--filter",
        f"name=^/{container_name}$",
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == ""


def _docker_executor_or_skip() -> DockerCommandExecutor:
    reason = _docker_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    if os.getuid() <= 0 or os.getgid() <= 0:
        pytest.skip("Docker integration requires a non-root host UID and GID")
    return DockerCommandExecutor(
        image=DOCKER_TEST_IMAGE,
        user=(os.getuid(), os.getgid()),
    )


def _docker_skip_reason() -> str | None:
    if shutil.which("docker") is None:
        return "Docker integration skipped: Docker CLI is not installed"
    try:
        info = _docker_cli("info")
    except subprocess.TimeoutExpired:
        return "Docker integration skipped: Docker daemon did not respond within 5 seconds"
    if info.returncode != 0:
        detail = info.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        return f"Docker integration skipped: Docker daemon is unavailable{suffix}"
    image = _docker_cli("image", "inspect", DOCKER_TEST_IMAGE)
    if image.returncode != 0:
        return (
            "Docker integration skipped: required local image "
            f"{DOCKER_TEST_IMAGE!r} is unavailable (tests never pull images)"
        )
    return None


def _docker_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        env=docker_cli_environment(),
    )
