from pathlib import Path

import pytest

from mini_harness.errors import ReplayExhaustedError, TraceFormatError
from mini_harness.messages import ModelResponse, ToolCall
from mini_harness.models.replay import ReplayModelClient
from mini_harness.trace.matcher import ToolInvocation, TraceMatcher
from mini_harness.trace.reader import TraceReader
from mini_harness.trace.recorder import TraceRecorder


def test_recorder_redacts_key_and_secret_patterns(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(trace, "run_1")

    recorder.record(
        "run_started",
        task="use sk-ant-abcdefghijklmnop",
        api_key="not-visible",
    )
    event = TraceReader(trace).read()[0]

    assert event["api_key"] == "[REDACTED]"
    assert "[REDACTED]" in event["task"]
    assert "sk-ant-" not in trace.read_text(encoding="utf-8")


def test_recorder_redacts_registered_sensitive_file_content(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(trace, "run_1", sensitive_paths=[".env"])
    recorder.register_sensitive_value("TOP_SECRET_VALUE")

    recorder.record("model_response", response={"content": "TOP_SECRET_VALUE"})

    assert "TOP_SECRET_VALUE" not in trace.read_text(encoding="utf-8")


def test_reader_tolerates_only_partial_last_line(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(trace, "run_1")
    recorder.record("run_started", task="test")
    with trace.open("a", encoding="utf-8") as handle:
        handle.write('{"type":')

    events = TraceReader(trace).read()

    assert len(events) == 1
    with pytest.raises(TraceFormatError):
        TraceReader(trace, tolerate_partial_last_line=False).read()


def test_reader_rejects_invalid_middle_line(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '{"type":"run_started","timestamp":"2026-01-01T00:00:00Z","run_id":"x","sequence":1}\n'
        "not-json\n"
        '{"type":"run_finished","timestamp":"2026-01-01T00:00:01Z","run_id":"x","sequence":2}\n',
        encoding="utf-8",
    )

    with pytest.raises(TraceFormatError, match="line 2"):
        TraceReader(trace).read()


def test_reader_rejects_complete_invalid_last_line(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '{"type":"run_started","timestamp":"2026-01-01T00:00:00Z",'
        '"run_id":"x","sequence":1}\n'
        "not-json\n",
        encoding="utf-8",
    )

    with pytest.raises(TraceFormatError, match="line 2"):
        TraceReader(trace).read()


@pytest.mark.asyncio
async def test_replay_returns_copies_then_exhausts() -> None:
    client = ReplayModelClient([ModelResponse(content="done")])

    response = await client.complete([], [])

    assert response.content == "done"
    assert client.consumed == 1
    with pytest.raises(ReplayExhaustedError):
        await client.complete([], [])


def test_matcher_reports_first_argument_divergence() -> None:
    matcher = TraceMatcher()
    expected = [
        ToolInvocation(turn=1, tool="read_file", arguments={"path": "a.txt"}),
        ToolInvocation(turn=2, tool="bash", arguments={"command": "pytest"}),
    ]
    actual = [
        ToolInvocation(turn=1, tool="read_file", arguments={"path": "b.txt"}),
        ToolInvocation(turn=2, tool="bash", arguments={"command": "pytest"}),
    ]

    divergence = matcher.compare_invocations(expected, actual)

    assert divergence is not None
    assert divergence.turn == 1
    assert divergence.reason == "normalized tool arguments differ"


def test_matcher_reports_missing_and_extra_calls() -> None:
    item = ToolInvocation(turn=1, tool="read_file", arguments={"path": "a.txt"})
    matcher = TraceMatcher()

    missing = matcher.compare_invocations([item], [])
    extra = matcher.compare_invocations([], [item])

    assert missing is not None and "ended before" in missing.reason
    assert extra is not None and "unexpected extra" in extra.reason


def test_model_response_can_be_extracted_from_trace(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(trace, "run_1")
    response = ModelResponse(
        tool_calls=[ToolCall(id="c", name="read_file", arguments={"path": "a"})]
    )
    recorder.record(
        "model_response",
        turn=1,
        response_kind="tool_call",
        response=response.model_dump(mode="json"),
    )

    extracted = TraceReader(trace).model_responses()

    assert extracted == [response]
