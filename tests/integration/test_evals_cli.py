from pathlib import Path

import pytest
from typer.testing import CliRunner

from mini_harness.cli import app
from mini_harness.evals.live_runner import (
    FailureCategory,
    LiveEvalRunner,
    official_openai_pricing,
)
from mini_harness.evals.runner import EvalRunner
from mini_harness.messages import ModelResponse, ModelUsage, ToolCall
from mini_harness.models.replay import ReplayModelClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = PROJECT_ROOT / "evals" / "cases"


@pytest.mark.asyncio
async def test_all_ten_evals_pass() -> None:
    suite = await EvalRunner(CASES_DIR).run_all()

    assert len(suite.cases) == 10
    assert suite.passed
    assert suite.task_pass_rate == 1.0
    assert suite.policy_denial_count == 2
    assert suite.replay_match_rate == 0.5


def test_eval_cli_text_report() -> None:
    result = CliRunner().invoke(app, ["eval", str(CASES_DIR)])

    assert result.exit_code == 0, result.output
    assert "task_pass_rate: 100.0%" in result.output
    assert "replay_divergence" in result.output


def test_cli_help_lists_primary_workflows() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("run", "replay", "eval", "live-eval", "trace"):
        assert command in result.output


def test_live_eval_validate_only_needs_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = CliRunner().invoke(
        app,
        ["live-eval", str(PROJECT_ROOT / "evals" / "live_cases"), "--validate-only"],
    )

    assert result.exit_code == 0, result.output
    assert "validated 5 live eval cases" in result.output
    assert "no model requests sent" in result.output


def test_live_eval_validate_only_accepts_docker_without_starting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = CliRunner().invoke(
        app,
        [
            "live-eval",
            str(PROJECT_ROOT / "evals" / "live_cases"),
            "--validate-only",
            "--executor",
            "docker",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "validated 5 live eval cases" in result.output


@pytest.mark.asyncio
async def test_live_eval_persists_incremental_reports(tmp_path: Path) -> None:
    cases = tmp_path / "cases"
    case_dir = cases / "small_fix"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        """
name: small_fix
description: Change a constant.
task: Change VALUE to 2.
expected:
  file_contains:
    - path: value.py
      text: "VALUE = 2"
  tool_called_any:
    - [edit_file, write_file]
  run_status_equals: completed
""".strip(),
        encoding="utf-8",
    )
    usage = ModelUsage(input_tokens=100, output_tokens=20, request_count=1)
    responses = [
        ModelResponse(
            tool_calls=[
                ToolCall(
                    id="write",
                    name="write_file",
                    arguments={
                        "path": "value.py",
                        "content": "VALUE = 2\n",
                    },
                )
            ],
            usage=usage,
        ),
        ModelResponse(content="done", usage=usage),
    ]
    output = tmp_path / "output"
    runner = LiveEvalRunner(
        cases_dir=cases,
        output_dir=output,
        model_factory=lambda: ReplayModelClient(responses),
        model_name="gpt-5.6-terra",
        model_backend="test-replay",
        runs_per_case=2,
        git_commit="abc123",
    )

    suite = await runner.run_all()

    assert len(suite.attempts) == 2
    assert all(attempt.passed for attempt in suite.attempts)
    assert all(attempt.artifact_passed for attempt in suite.attempts)
    assert all(attempt.runtime_passed for attempt in suite.attempts)
    assert all(attempt.tool_contract_passed for attempt in suite.attempts)
    assert suite.artifact_pass_rate == 1.0
    assert suite.runtime_pass_at_k == 1.0
    assert suite.tool_contract_pass_rate == 1.0
    assert suite.total_usage.input_tokens == 400
    assert suite.pricing == official_openai_pricing("gpt-5.6-terra")
    assert suite.total_estimated_cost_usd == 0.0022
    assert (output / "results.json").is_file()
    markdown = (output / "README.md").read_text(encoding="utf-8")
    assert "OpenAI Standard API rates" in markdown
    assert "Artifact pass rate" in markdown
    assert "Tool contract pass rate" in markdown
    assert "100.0%" in markdown


@pytest.mark.asyncio
async def test_expected_tool_error_does_not_mask_contract_failure(tmp_path: Path) -> None:
    cases = tmp_path / "cases"
    case_dir = cases / "recovery"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (case_dir / "case.yaml").write_text(
        """
name: recovery
description: Expected missing-path recovery.
task: Read the stale path and then edit the real file.
expected:
  tool_called: [read_file, edit_file]
  tool_status_equals:
    read_file: error
  run_status_equals: completed
""".strip(),
        encoding="utf-8",
    )
    runner = LiveEvalRunner(
        cases_dir=cases,
        output_dir=tmp_path / "output",
        model_factory=lambda: ReplayModelClient(
            [
                ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="missing",
                            name="read_file",
                            arguments={"path": "missing.py"},
                        )
                    ]
                ),
                ModelResponse(content="stopped too early"),
            ]
        ),
        model_name="gpt-5.6-terra",
        model_backend="test-replay",
        runs_per_case=1,
    )

    suite = await runner.run_all()
    attempt = suite.attempts[0]

    assert attempt.passed is False
    assert attempt.runtime_passed is True
    assert attempt.tool_contract_passed is False
    assert attempt.failure_category is FailureCategory.TOOL_CONTRACT_ERROR
