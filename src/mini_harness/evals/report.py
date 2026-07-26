"""Human-readable and JSON eval reporting."""

from __future__ import annotations

import json
from typing import Any

from mini_harness.evals.runner import EvalSuiteResult


def format_text_report(suite: EvalSuiteResult) -> str:
    rows = ["CASE                 RESULT  TURNS  TOOLS  DURATION_MS"]
    for case in suite.cases:
        rows.append(
            f"{case.name:<20} {'PASS' if case.passed else 'FAIL':<6} "
            f"{case.run.turns:>5} {case.run.tool_call_count:>6} {case.run.duration_ms:>12}"
        )
        for assertion in case.assertions:
            if not assertion.passed:
                rows.append(f"  - {assertion.evaluator}: {assertion.message}")

    metrics = suite.metrics()
    rows.extend(
        [
            "",
            f"task_pass_rate: {metrics['task_pass_rate']:.1%}",
            f"average_turns: {metrics['average_turns']:.2f}",
            f"average_tool_calls: {metrics['average_tool_calls']:.2f}",
            f"tool_error_rate: {metrics['tool_error_rate']:.1%}",
            f"policy_denial_count: {metrics['policy_denial_count']}",
            f"replay_match_rate: {metrics['replay_match_rate']:.1%}",
            f"average_duration_ms: {metrics['average_duration_ms']:.2f}",
        ]
    )
    return "\n".join(rows)


def format_json_report(suite: EvalSuiteResult) -> str:
    payload: dict[str, Any] = {
        "passed": suite.passed,
        "metrics": suite.metrics(),
        "cases": [
            {
                "name": case.name,
                "passed": case.passed,
                "status": case.run.status.value,
                "turns": case.run.turns,
                "tool_calls": case.run.tool_call_count,
                "duration_ms": case.run.duration_ms,
                "assertions": [assertion.model_dump(mode="json") for assertion in case.assertions],
                "divergence": (
                    case.divergence.model_dump(mode="json") if case.divergence is not None else None
                ),
            }
            for case in suite.cases
        ],
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)
