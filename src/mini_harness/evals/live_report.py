"""Text and Markdown reports for real-model eval runs."""

from __future__ import annotations

from collections import defaultdict

from mini_harness.evals.live_runner import LiveAttemptResult, LiveSuiteResult


def _cost(value: float | int | None, *, decimals: int = 4) -> str:
    return "not estimated" if value is None else f"{value:.{decimals}f}"


def _rate(value: float | None) -> str:
    return "not provided" if value is None else f"${value}"


def format_live_text_report(suite: LiveSuiteResult) -> str:
    rows = ["CASE                 RUN  RESULT  TURNS  TOOLS  TOKENS  COST_USD"]
    for attempt in suite.attempts:
        rows.append(
            f"{attempt.case_name:<20} {attempt.attempt:>3} "
            f"{'PASS' if attempt.passed else 'FAIL':<6} {attempt.turns:>5} "
            f"{attempt.tool_calls:>6} {attempt.usage.total_tokens:>7} "
            f"{_cost(attempt.estimated_cost_usd):>13}"
        )
        if not attempt.passed:
            rows.append(f"  - failure_category: {attempt.failure_category.value}")
            for assertion in attempt.assertions:
                if not assertion.passed:
                    rows.append(f"  - {assertion.evaluator}: {assertion.message}")
    metrics = suite.metrics()
    rows.extend(
        [
            "",
            f"model: {suite.model}",
            f"model_backend: {suite.model_backend}",
            f"git_commit: {suite.git_commit}",
            f"task_pass_rate: {metrics['task_pass_rate']:.1%}",
            f"pass_at_{suite.runs_per_case}: {metrics['pass_at_k']:.1%}",
            f"average_turns: {metrics['average_turns']:.2f}",
            f"average_tool_calls: {metrics['average_tool_calls']:.2f}",
            f"input_tokens: {metrics['input_tokens']}",
            f"output_tokens: {metrics['output_tokens']}",
            (f"total_estimated_cost_usd: {_cost(metrics['total_estimated_cost_usd'])}"),
        ]
    )
    return "\n".join(rows)


def format_live_markdown_report(suite: LiveSuiteResult) -> str:
    metrics = suite.metrics()
    lines = [
        "# Live Eval Results",
        "",
        "> These results measure one model on small checked-in fixtures. They are not a",
        "> general coding benchmark and are separate from the offline harness conformance evals.",
        "",
        "## Run Context",
        "",
        f"- Model: `{suite.model}`",
        f"- Backend: `{suite.model_backend}`",
        f"- Git commit: `{suite.git_commit}`",
        f"- Started: `{suite.started_at.isoformat()}`",
        f"- Completed: `{suite.completed_at.isoformat()}`",
        f"- Runs per case: `{suite.runs_per_case}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Attempts | {metrics['attempts']} |",
        f"| Passed attempts | {metrics['passed_attempts']} |",
        f"| Task pass rate | {metrics['task_pass_rate']:.1%} |",
        f"| Pass@{suite.runs_per_case} | {metrics['pass_at_k']:.1%} |",
        f"| Average turns | {metrics['average_turns']:.2f} |",
        f"| Average tool calls | {metrics['average_tool_calls']:.2f} |",
        f"| Input tokens | {metrics['input_tokens']} |",
        f"| Output tokens | {metrics['output_tokens']} |",
        f"| Estimated cost (USD) | {_cost(metrics['total_estimated_cost_usd'])} |",
        "",
        "## Per Case",
        "",
        "| Case | Passed | Attempts | Pass Rate |",
        "|---|---:|---:|---:|",
    ]
    attempts_by_case: dict[str, list[LiveAttemptResult]] = defaultdict(list)
    for attempt in suite.attempts:
        attempts_by_case[attempt.case_name].append(attempt)
    for case_name, attempts in sorted(attempts_by_case.items()):
        passed = sum(attempt.passed for attempt in attempts)
        lines.append(
            f"| `{case_name}` | {passed} | {len(attempts)} | {passed / len(attempts):.1%} |"
        )

    lines.extend(
        [
            "",
            "## Failure Categories",
            "",
            "| Category | Count |",
            "|---|---:|",
        ]
    )
    failures: dict[str, int] = defaultdict(int)
    for attempt in suite.attempts:
        if not attempt.passed:
            failures[attempt.failure_category.value] += 1
    if failures:
        for category, count in sorted(failures.items()):
            lines.append(f"| `{category}` | {count} |")
    else:
        lines.append("| None | 0 |")

    lines.extend(
        [
            "",
            "## Pricing Assumptions",
            "",
            "Rates are never inferred. Values are configurable and stored with `results.json`:",
            "",
            f"- Source: `{suite.pricing.source}`",
            f"- Basis: {suite.pricing.basis}",
            f"- Input: `{_rate(suite.pricing.input_per_million)}` / 1M tokens",
            f"- Output: `{_rate(suite.pricing.output_per_million)}` / 1M tokens",
            (f"- Cache creation: `{_rate(suite.pricing.cache_creation_per_million)}` / 1M tokens"),
            f"- Cache read: `{_rate(suite.pricing.cache_read_per_million)}` / 1M tokens",
            "",
        ]
    )
    return "\n".join(lines)
