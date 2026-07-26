"""Approval strategies for policy decisions that require confirmation."""

from __future__ import annotations

import asyncio
from typing import Protocol

import typer

from mini_harness.messages import ToolCall
from mini_harness.policy.rules import PolicyOutcome


class ApprovalHandler(Protocol):
    async def approve(self, call: ToolCall, outcome: PolicyOutcome) -> bool:
        """Return whether an ASK decision is approved."""


class AlwaysApprove:
    async def approve(self, call: ToolCall, outcome: PolicyOutcome) -> bool:
        return True


class AlwaysDeny:
    async def approve(self, call: ToolCall, outcome: PolicyOutcome) -> bool:
        return False


class InteractiveApproval:
    async def approve(self, call: ToolCall, outcome: PolicyOutcome) -> bool:
        prompt = f"Allow {call.name} ({outcome.reason})?"
        return await asyncio.to_thread(typer.confirm, prompt, default=False)
