from pathlib import Path

import pytest
from typer.testing import CliRunner

from mini_harness.cli import app
from mini_harness.evals.runner import EvalRunner

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
    for command in ("run", "replay", "eval", "trace"):
        assert command in result.output
