import asyncio
from pathlib import Path

import pytest

from mini_harness.config import HarnessConfig
from mini_harness.executors import (
    CommandExecution,
    DockerCommandExecutor,
    LocalCommandExecutor,
    create_command_executor,
    docker_cli_environment,
)
from mini_harness.messages import ToolCall, ToolResultStatus
from mini_harness.tools import default_registry
from mini_harness.tools.base import ToolContext


def _context(workspace: Path) -> ToolContext:
    return ToolContext(
        workspace=workspace,
        timeout_seconds=1,
        max_output_chars=1000,
    )


class StubCommandExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []

    async def run(self, command: str, *, workspace: Path) -> CommandExecution:
        self.calls.append((command, workspace))
        return CommandExecution(stdout="stdout", stderr="stderr", exit_code=7)


@pytest.mark.asyncio
async def test_default_registry_injects_bash_executor(tmp_path: Path) -> None:
    executor = StubCommandExecutor()

    result = await default_registry(executor).execute(
        ToolCall(id="bash", name="bash", arguments={"command": "example"}),
        _context(tmp_path),
    )

    assert executor.calls == [("example", tmp_path)]
    assert result.status is ToolResultStatus.ERROR
    assert result.output == "stdoutstderr"
    assert result.exit_code == 7


def test_docker_command_has_isolation_and_resource_limits(tmp_path: Path) -> None:
    executor = DockerCommandExecutor(
        image="example/python:test",
        cpus=1.5,
        memory_mb=768,
        pids_limit=64,
        user=(123, 456),
    )

    command = executor.build_command(
        "python -m pytest",
        workspace=tmp_path,
        container_name="mini-harness-offline-test",
    )

    assert command[:3] == ["docker", "run", "--rm"]
    assert "--pull=never" in command
    assert _option(command, "--name") == "mini-harness-offline-test"
    assert _option(command, "--network") == "none"
    assert _option(command, "--cpus") == "1.5"
    assert _option(command, "--memory") == "768m"
    assert _option(command, "--pids-limit") == "64"
    assert _option(command, "--user") == "123:456"
    assert _option(command, "--workdir") == "/workspace"
    assert _option(command, "--cap-drop") == "ALL"
    assert _option(command, "--security-opt") == "no-new-privileges=true"
    assert "--init" in command
    assert _option(command, "--mount") == (
        f"type=bind,source={tmp_path.resolve()},target=/workspace"
    )
    assert len(_options(command, "--mount")) == 1
    assert _option(command, "--tmpfs") == "/tmp:rw,nosuid,nodev,size=64m,mode=1777"
    assert _options(command, "--env") == ["HOME=/tmp/mini-harness-home"]
    assert command[-1] == "python -m pytest"


def test_docker_cli_environment_excludes_host_secrets_and_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/host/home")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-inherited")
    monkeypatch.setenv("DOCKER_HOST", "unix:///example/docker.sock")
    monkeypatch.setenv("PATH", "/usr/bin")

    environment = docker_cli_environment()

    assert environment["PATH"] == "/usr/bin"
    assert environment["DOCKER_HOST"] == "unix:///example/docker.sock"
    assert "HOME" not in environment
    assert "OPENAI_API_KEY" not in environment
    assert "ANTHROPIC_API_KEY" not in environment


@pytest.mark.asyncio
async def test_docker_cancellation_force_removes_container_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = BlockingProcess()
    executor = RecordingDockerExecutor(
        image="example/python:test",
        user=(123, 456),
        container_name_factory=lambda: "mini-harness-cancel-test",
    )

    async def fake_create_subprocess_exec(
        *args: str,
        **kwargs: object,
    ) -> BlockingProcess:
        del args, kwargs
        return process

    def fake_terminate(target: BlockingProcess) -> None:
        target.returncode = -9

    monkeypatch.setattr(
        "mini_harness.executors.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("mini_harness.executors._terminate_process_group", fake_terminate)

    task = asyncio.create_task(executor.run("sleep 30", workspace=tmp_path))
    await process.communicate_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert executor.removed_names == [
        "mini-harness-cancel-test",
        "mini-harness-cancel-test",
    ]
    assert process.waited is True


@pytest.mark.asyncio
async def test_docker_force_remove_uses_cli_even_when_container_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    cleanup = CompletedProcess()

    async def fake_create_subprocess_exec(
        *args: str,
        **kwargs: object,
    ) -> CompletedProcess:
        del kwargs
        calls.append(args)
        return cleanup

    monkeypatch.setattr(
        "mini_harness.executors.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    executor = DockerCommandExecutor(image="example/python:test", user=(123, 456))

    await executor._force_remove_container("mini-harness-cleanup-test")

    assert calls == [
        ("docker", "rm", "--force", "mini-harness-cleanup-test"),
    ]
    assert cleanup.waited is True


def test_executor_factory_defaults_local_and_builds_configured_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(create_command_executor(HarnessConfig()), LocalCommandExecutor)
    monkeypatch.setattr("mini_harness.executors.os.getuid", lambda: 123)
    monkeypatch.setattr("mini_harness.executors.os.getgid", lambda: 456)

    executor = create_command_executor(
        HarnessConfig(
            executor="docker",
            docker_image="example/python:test",
            docker_cpus=2,
            docker_memory_mb=1024,
            docker_pids_limit=96,
        )
    )

    assert isinstance(executor, DockerCommandExecutor)
    assert executor.image == "example/python:test"
    assert executor.cpus == 2
    assert executor.memory_mb == 1024
    assert executor.pids_limit == 96


class BlockingProcess:
    def __init__(self) -> None:
        self.pid = 999_999
        self.returncode: int | None = None
        self.communicate_started = asyncio.Event()
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communicate_started.set()
        await asyncio.Event().wait()
        return b"", b""

    async def wait(self) -> int:
        self.waited = True
        self.returncode = -9
        return self.returncode


class CompletedProcess:
    def __init__(self) -> None:
        self.pid = 999_998
        self.returncode: int | None = None
        self.waited = False

    async def wait(self) -> int:
        self.waited = True
        self.returncode = 1
        return self.returncode


class RecordingDockerExecutor(DockerCommandExecutor):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.removed_names: list[str] = []

    async def _force_remove_container(self, container_name: str) -> None:
        self.removed_names.append(container_name)


def _option(command: list[str], name: str) -> str:
    return command[command.index(name) + 1]


def _options(command: list[str], name: str) -> list[str]:
    return [command[index + 1] for index, value in enumerate(command) if value == name]
