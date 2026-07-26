"""Provider-neutral coding-agent state machine."""

from __future__ import annotations

import asyncio
import time
import uuid
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from mini_harness.config import HarnessConfig
from mini_harness.messages import Message, ToolCall, ToolResult, ToolResultStatus
from mini_harness.models.base import ModelClient
from mini_harness.policy.approval import AlwaysDeny, ApprovalHandler
from mini_harness.policy.engine import PolicyEngine
from mini_harness.policy.rules import PolicyDecision
from mini_harness.tools.base import ToolContext
from mini_harness.tools.registry import ToolRegistry
from mini_harness.trace.recorder import TraceRecorder


class RunStatus(StrEnum):
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    FAILED = "failed"


class RunResult(BaseModel):
    run_id: str
    status: RunStatus
    final_text: str = ""
    turns: int = Field(ge=0)
    tool_results: list[ToolResult] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    trace_path: Path
    duration_ms: int = Field(ge=0)
    error: str | None = None

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_results)


class AgentRuntime:
    """Orchestrate model decisions without implementing model or tool behavior."""

    def __init__(
        self,
        *,
        model: ModelClient,
        tools: ToolRegistry,
        policy: PolicyEngine,
        config: HarnessConfig,
        approval: ApprovalHandler | None = None,
        recorder: TraceRecorder | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.policy = policy
        self.config = config
        self.approval = approval or AlwaysDeny()
        self.recorder = recorder

    async def run(self, task: str) -> RunResult:
        run_id = self.recorder.run_id if self.recorder is not None else f"run_{uuid.uuid4().hex}"
        recorder = self.recorder or TraceRecorder(
            self.config.resolved_trace_dir() / f"{run_id}.jsonl",
            run_id,
            sensitive_paths=self.config.sensitive_paths,
        )
        started = time.monotonic()
        messages = [Message(role="user", content=task)]
        tool_results: list[ToolResult] = []
        turns = 0
        recorder.record(
            "run_started",
            task=task,
            workspace=str(self.config.workspace),
            max_turns=self.config.max_turns,
        )

        try:
            for turn in range(1, self.config.max_turns + 1):
                turns = turn
                recorder.record(
                    "model_request",
                    turn=turn,
                    message_count=len(messages),
                    tools=[definition.name for definition in self.tools.definitions],
                )
                response = await self.model.complete(
                    [message.model_copy(deep=True) for message in messages],
                    self.tools.definitions,
                )
                recorder.record(
                    "model_response",
                    turn=turn,
                    response_kind=response.kind,
                    tool_names=[call.name for call in response.tool_calls],
                    response=response.model_dump(mode="json"),
                )
                messages.append(
                    Message(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                )

                if not response.tool_calls:
                    return self._finish(
                        recorder=recorder,
                        started=started,
                        status=RunStatus.COMPLETED,
                        turns=turns,
                        messages=messages,
                        tool_results=tool_results,
                        final_text=response.content,
                    )

                for call in response.tool_calls:
                    result = await self._execute_call(
                        recorder=recorder,
                        turn=turn,
                        call=call,
                    )
                    tool_results.append(result)
                    messages.append(Message.from_tool_result(result))

            return self._finish(
                recorder=recorder,
                started=started,
                status=RunStatus.MAX_TURNS,
                turns=turns,
                messages=messages,
                tool_results=tool_results,
                error=f"maximum turn limit reached: {self.config.max_turns}",
            )
        except asyncio.CancelledError:
            recorder.record(
                "run_failed",
                status="cancelled",
                turns=turns,
                error="run cancelled",
                duration_ms=self._duration_ms(started),
            )
            raise
        except Exception as exc:
            recorder.record(
                "run_failed",
                status=RunStatus.FAILED.value,
                turns=turns,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=self._duration_ms(started),
            )
            return RunResult(
                run_id=run_id,
                status=RunStatus.FAILED,
                turns=turns,
                messages=messages,
                tool_results=tool_results,
                trace_path=recorder.path,
                duration_ms=self._duration_ms(started),
                error=f"{type(exc).__name__}: {exc}",
            )

    async def _execute_call(
        self,
        *,
        recorder: TraceRecorder,
        turn: int,
        call: ToolCall,
    ) -> ToolResult:
        recorder.record(
            "tool_requested",
            turn=turn,
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=call.arguments,
        )
        outcome = self.policy.evaluate(call)
        if outcome.decision is PolicyDecision.ASK:
            outcome.approved = await self.approval.approve(call, outcome)
        recorder.record(
            "policy_decided",
            turn=turn,
            tool_call_id=call.id,
            tool_name=call.name,
            decision=outcome.decision.value,
            rule=outcome.rule,
            reason=outcome.reason,
            approved=outcome.approved,
            effective_decision="allow" if outcome.allowed else "deny",
            normalized_arguments=outcome.normalized_arguments,
        )

        if not outcome.allowed:
            result = ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                status=ToolResultStatus.DENIED,
                output=f"permission denied: {outcome.reason}",
                duration_ms=0,
            )
        else:
            recorder.record(
                "tool_started",
                turn=turn,
                tool_call_id=call.id,
                tool_name=call.name,
            )
            context = ToolContext(
                workspace=self.config.workspace,
                timeout_seconds=self.config.tool_timeout_seconds,
                max_output_chars=self.config.max_output_chars,
            )
            result = await self.tools.execute(call, context)

        path = outcome.normalized_arguments.get("path")
        if (
            call.name == "read_file"
            and result.status is ToolResultStatus.SUCCESS
            and isinstance(path, str)
            and recorder.path_is_sensitive(path)
        ):
            recorder.register_sensitive_value(result.output)
            trace_result = result.model_copy(update={"output": "[REDACTED]"})
        else:
            trace_result = result
        recorder.record(
            "tool_finished",
            turn=turn,
            **trace_result.model_dump(mode="json"),
        )
        return result

    def _finish(
        self,
        *,
        recorder: TraceRecorder,
        started: float,
        status: RunStatus,
        turns: int,
        messages: list[Message],
        tool_results: list[ToolResult],
        final_text: str = "",
        error: str | None = None,
    ) -> RunResult:
        duration_ms = self._duration_ms(started)
        recorder.record(
            "run_finished",
            status=status.value,
            turns=turns,
            tool_calls=len(tool_results),
            duration_ms=duration_ms,
            error=error,
        )
        return RunResult(
            run_id=recorder.run_id,
            status=status,
            final_text=final_text,
            turns=turns,
            tool_results=tool_results,
            messages=messages,
            trace_path=recorder.path,
            duration_ms=duration_ms,
            error=error,
        )

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))
