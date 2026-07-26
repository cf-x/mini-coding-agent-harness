"""TOML and environment-backed runtime configuration."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class HarnessConfig(BaseModel):
    """Configuration that is independent from any concrete model client."""

    workspace: Path = Field(default_factory=Path.cwd)
    max_turns: int = Field(default=12, ge=1, le=1000)
    finalization_turn: bool = True
    tool_timeout_seconds: float = Field(default=30.0, gt=0, le=3600)
    max_output_chars: int = Field(default=20_000, ge=64)
    write_policy: Literal["allow", "ask", "deny"] = "ask"
    shell_policy: Literal["allow", "ask", "deny"] = "ask"
    trace_dir: Path = Path("traces")
    provider: Literal["openai", "anthropic"] = "openai"
    model: str = "gpt-5.6-terra"
    max_model_tokens: int = Field(default=4096, ge=1)
    openai_base_url: str | None = None
    openai_tool_mode: Literal["auto", "function", "prompt"] = "auto"
    openai_client_profile: Literal["standard", "codex"] = "standard"
    sensitive_paths: list[str] = Field(
        default_factory=lambda: [".env", "*.pem", "*.key", "*credentials*"]
    )

    @field_validator("workspace")
    @classmethod
    def resolve_workspace(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("trace_dir")
    @classmethod
    def expand_trace_dir(cls, value: Path) -> Path:
        return value.expanduser()

    def resolved_trace_dir(self) -> Path:
        if self.trace_dir.is_absolute():
            return self.trace_dir.resolve()
        return (self.workspace / self.trace_dir).resolve()


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


_ENV_MAPPING: dict[str, tuple[str, Callable[[str], object]]] = {
    "MINI_HARNESS_MAX_TURNS": ("max_turns", int),
    "MINI_HARNESS_FINALIZATION_TURN": ("finalization_turn", _parse_bool),
    "MINI_HARNESS_TOOL_TIMEOUT_SECONDS": ("tool_timeout_seconds", float),
    "MINI_HARNESS_MAX_OUTPUT_CHARS": ("max_output_chars", int),
    "MINI_HARNESS_WRITE_POLICY": ("write_policy", str),
    "MINI_HARNESS_SHELL_POLICY": ("shell_policy", str),
    "MINI_HARNESS_PROVIDER": ("provider", str),
    "MINI_HARNESS_MODEL": ("model", str),
    "MINI_HARNESS_OPENAI_TOOL_MODE": ("openai_tool_mode", str),
    "MINI_HARNESS_OPENAI_CLIENT_PROFILE": ("openai_client_profile", str),
    "OPENAI_BASE_URL": ("openai_base_url", str),
}


def load_config(path: Path | None = None, **overrides: Any) -> HarnessConfig:
    """Load optional TOML, then environment values, then explicit overrides."""

    values: dict[str, Any] = {}
    if path is not None:
        with path.expanduser().open("rb") as handle:
            document = tomllib.load(handle)
        section = document.get("mini_harness", document)
        if not isinstance(section, dict):
            raise ValueError("TOML configuration must be a table")
        values.update(section)

    for environment_name, (field_name, converter) in _ENV_MAPPING.items():
        raw = os.getenv(environment_name)
        if raw is not None:
            values[field_name] = converter(raw)

    values.update({key: value for key, value in overrides.items() if value is not None})
    return HarnessConfig.model_validate(values)
