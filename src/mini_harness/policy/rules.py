"""Policy decision models and shell-risk rules."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PolicyOutcome(BaseModel):
    decision: PolicyDecision
    rule: str
    reason: str
    normalized_arguments: dict[str, Any]
    approved: bool | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is PolicyDecision.ALLOW or (
            self.decision is PolicyDecision.ASK and self.approved is True
        )


_DANGEROUS_SHELL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|[;&|]\s*)rm\s+(-[A-Za-z]*r[A-Za-z]*f|-rf|-fr)\b"), "recursive rm"),
    (re.compile(r"\bmkfs(?:\.\w+)?\b"), "filesystem formatting"),
    (re.compile(r"\bdd\s+[^;&|]*\bof=/dev/"), "raw device write"),
    (re.compile(r"(^|[;&|]\s*)shutdown\b|(^|[;&|]\s*)reboot\b"), "host shutdown"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*;\s*\}\s*;"), "fork bomb"),
    (re.compile(r"\bgit\s+(?:reset\s+--hard|clean\s+-[A-Za-z]*f)"), "destructive git"),
)


def classify_dangerous_shell(command: str) -> str | None:
    compact = " ".join(command.strip().split())
    for pattern, reason in _DANGEROUS_SHELL_PATTERNS:
        if pattern.search(compact):
            return reason
    return None
