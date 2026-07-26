"""Read and query JSONL traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mini_harness.errors import TraceFormatError
from mini_harness.messages import ModelResponse
from mini_harness.trace.events import TraceEvent


class TraceReader:
    def __init__(self, path: Path, *, tolerate_partial_last_line: bool = True) -> None:
        self.path = path.expanduser().resolve()
        self.tolerate_partial_last_line = tolerate_partial_last_line

    def read(self) -> list[dict[str, Any]]:
        document = self.path.read_text(encoding="utf-8")
        lines = document.splitlines()
        has_complete_last_line = document.endswith("\n")
        events: list[dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                event = TraceEvent.model_validate(raw).as_dict()
            except (json.JSONDecodeError, ValueError) as exc:
                if (
                    self.tolerate_partial_last_line
                    and index == len(lines)
                    and not has_complete_last_line
                ):
                    break
                raise TraceFormatError(f"invalid trace event on line {index}: {exc}") from exc
            events.append(event)
        return events

    def model_responses(self) -> list[ModelResponse]:
        responses: list[ModelResponse] = []
        for event in self.read():
            if event["type"] == "model_response":
                responses.append(ModelResponse.model_validate(event["response"]))
        return responses

    def task(self) -> str:
        for event in self.read():
            if event["type"] == "run_started":
                task = event.get("task")
                if isinstance(task, str):
                    return task
        raise TraceFormatError("trace has no run_started task")
