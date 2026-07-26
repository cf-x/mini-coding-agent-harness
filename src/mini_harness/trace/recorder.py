"""Synchronous, append-only JSONL trace recorder."""

from __future__ import annotations

import fnmatch
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mini_harness.trace.events import EventType, TraceEvent

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "environment",
    "env",
}
_SECRET_PATTERNS = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}=*\b"),
)


class TraceRecorder:
    """Write one flushed JSON object per line and redact common secrets."""

    def __init__(
        self,
        path: Path,
        run_id: str,
        *,
        sensitive_paths: list[str] | None = None,
        fsync: bool = False,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.run_id = run_id
        self.sensitive_paths = sensitive_paths or []
        self.fsync = fsync
        self._sequence = 0
        self._sensitive_values: set[str] = set()

    def record(self, event_type: EventType, **fields: Any) -> dict[str, Any]:
        self._sequence += 1
        payload = {
            "type": event_type,
            "timestamp": datetime.now(UTC),
            "run_id": self.run_id,
            "sequence": self._sequence,
            **fields,
        }
        event = TraceEvent.model_validate(self._sanitize(payload)).as_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
            handle.flush()
            if self.fsync:
                os.fsync(handle.fileno())
        return event

    def register_sensitive_value(self, value: str) -> None:
        if value:
            self._sensitive_values.add(value)

    def path_is_sensitive(self, relative_path: str) -> bool:
        normalized = Path(relative_path).as_posix()
        name = Path(normalized).name
        return any(
            fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(name, pattern)
            for pattern in self.sensitive_paths
        )

    def _sanitize(self, value: Any, key: str | None = None) -> Any:
        if key is not None and key.lower() in _SENSITIVE_KEYS:
            return "[REDACTED]"
        if isinstance(value, dict):
            return {item_key: self._sanitize(item, item_key) for item_key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize(item) for item in value]
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, str):
            sanitized = value
            for sensitive in sorted(self._sensitive_values, key=len, reverse=True):
                sanitized = sanitized.replace(sensitive, "[REDACTED]")
            for pattern in _SECRET_PATTERNS:
                sanitized = pattern.sub("[REDACTED]", sanitized)
            return sanitized
        return value
