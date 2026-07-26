"""Command execution backends for the Bash tool."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mini_harness.config import HarnessConfig


@dataclass(frozen=True, slots=True)
class CommandExecution:
    """Structured output from one command process."""

    stdout: str
    stderr: str
    exit_code: int

    @property
    def output(self) -> str:
        """Return the single text stream expected by the existing ToolResult contract."""

        return self.stdout + self.stderr


class CommandExecutor(Protocol):
    """Minimal execution boundary used by BashTool."""

    async def run(self, command: str, *, workspace: Path) -> CommandExecution:
        """Run a command in the supplied workspace."""


def shell_environment(workspace: Path) -> dict[str, str]:
    """Build the minimal environment used by local agent and evaluator commands."""

    allowed = ("PATH", "LANG", "LC_ALL", "TERM", "TMPDIR")
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    interpreter_dir = str(Path(sys.executable).resolve().parent)
    inherited_path = environment.get("PATH", "")
    environment["PATH"] = (
        f"{interpreter_dir}{os.pathsep}{inherited_path}" if inherited_path else interpreter_dir
    )
    environment["HOME"] = str(workspace)
    return environment


def docker_cli_environment() -> dict[str, str]:
    """Build a credential-free environment for Docker CLI subprocesses."""

    allowed = (
        "PATH",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    )
    return {name: os.environ[name] for name in allowed if name in os.environ}


class LocalCommandExecutor:
    """Run commands directly on the host with the historical BashTool behavior."""

    async def run(self, command: str, *, workspace: Path) -> CommandExecution:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=workspace,
            env=shell_environment(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            _terminate_process_group(process)
            await process.wait()
            raise
        if process.returncode is None:
            raise RuntimeError("local command process exited without a return code")
        return CommandExecution(
            stdout=_decode(stdout),
            stderr=_decode(stderr),
            exit_code=process.returncode,
        )


class DockerCommandExecutor:
    """Run commands in a short-lived, resource-limited Docker container."""

    container_workspace = Path("/workspace")
    container_home = Path("/tmp/mini-harness-home")

    def __init__(
        self,
        *,
        image: str,
        cpus: float = 1.0,
        memory_mb: int = 512,
        pids_limit: int = 128,
        docker_cli: str = "docker",
        user: tuple[int, int] | None = None,
        container_name_factory: Callable[[], str] | None = None,
        cleanup_timeout_seconds: float = 5.0,
    ) -> None:
        if not image.strip():
            raise ValueError("Docker image must not be empty")
        if cpus <= 0:
            raise ValueError("Docker CPU limit must be positive")
        if memory_mb <= 0:
            raise ValueError("Docker memory limit must be positive")
        if pids_limit <= 0:
            raise ValueError("Docker PID limit must be positive")
        if cleanup_timeout_seconds <= 0:
            raise ValueError("Docker cleanup timeout must be positive")

        selected_user = user or (os.getuid(), os.getgid())
        if selected_user[0] <= 0 or selected_user[1] <= 0:
            raise ValueError("Docker executor requires a non-root UID and GID")

        self.image = image
        self.cpus = cpus
        self.memory_mb = memory_mb
        self.pids_limit = pids_limit
        self.docker_cli = docker_cli
        self.user = selected_user
        self.container_name_factory = container_name_factory or (
            lambda: f"mini-harness-{uuid.uuid4().hex}"
        )
        self.cleanup_timeout_seconds = cleanup_timeout_seconds

    async def run(self, command: str, *, workspace: Path) -> CommandExecution:
        container_name = self.container_name_factory()
        docker_command = self.build_command(
            command,
            workspace=workspace,
            container_name=container_name,
        )
        process = await asyncio.create_subprocess_exec(
            *docker_command,
            env=docker_cli_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            await self._cleanup_cancelled_run(process, container_name)
            raise
        if process.returncode is None:
            raise RuntimeError("Docker CLI exited without a return code")
        return CommandExecution(
            stdout=_decode(stdout),
            stderr=_decode(stderr),
            exit_code=process.returncode,
        )

    def build_command(
        self,
        command: str,
        *,
        workspace: Path,
        container_name: str,
    ) -> list[str]:
        """Build the complete Docker CLI invocation without executing it."""

        resolved_workspace = workspace.expanduser().resolve()
        if not resolved_workspace.is_dir():
            raise NotADirectoryError(f"workspace directory does not exist: {resolved_workspace}")
        uid, gid = self.user
        return [
            self.docker_cli,
            "run",
            "--rm",
            "--pull=never",
            "--name",
            container_name,
            "--network",
            "none",
            "--cpus",
            f"{self.cpus:g}",
            "--memory",
            f"{self.memory_mb}m",
            "--pids-limit",
            str(self.pids_limit),
            "--user",
            f"{uid}:{gid}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--init",
            "--workdir",
            str(self.container_workspace),
            "--mount",
            (f"type=bind,source={resolved_workspace},target={self.container_workspace}"),
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
            "--env",
            f"HOME={self.container_home}",
            self.image,
            "/bin/sh",
            "-c",
            'mkdir -p "$HOME" && exec /bin/sh -c "$1"',
            "mini-harness",
            command,
        ]

    async def _cleanup_cancelled_run(
        self,
        process: asyncio.subprocess.Process,
        container_name: str,
    ) -> None:
        await self._force_remove_container(container_name)
        _terminate_process_group(process)
        await process.wait()
        await self._force_remove_container(container_name)

    async def _force_remove_container(self, container_name: str) -> None:
        try:
            cleanup = await asyncio.create_subprocess_exec(
                self.docker_cli,
                "rm",
                "--force",
                container_name,
                env=docker_cli_environment(),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return

        try:
            await asyncio.wait_for(
                cleanup.wait(),
                timeout=self.cleanup_timeout_seconds,
            )
        except TimeoutError:
            _terminate_process_group(cleanup)
            await cleanup.wait()


def create_command_executor(config: HarnessConfig) -> CommandExecutor:
    """Create the configured Bash execution backend."""

    if config.executor == "local":
        return LocalCommandExecutor()
    return DockerCommandExecutor(
        image=config.docker_image,
        cpus=config.docker_cpus,
        memory_mb=config.docker_memory_mb,
        pids_limit=config.docker_pids_limit,
    )


def _decode(output: bytes) -> str:
    return output.decode("utf-8", errors="replace")


def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
