# Contributing

Contributions should keep the project small, observable, and testable.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Before opening a change:

```bash
ruff check .
ruff format --check .
mypy
pytest
mini-harness eval evals/cases
```

Tests and default evals must not require an API key or send live model requests.

## Change expectations

- Add a focused test for behavioral changes.
- Add or update an eval when changing policy, tool, replay, or termination behavior.
- Keep provider-specific types inside model adapters.
- Keep policy checks before side-effecting tool execution.
- Preserve one structured result for every requested tool call.
- Document new trace fields and avoid secrets or full environments in events.
- Treat shell string rules as risk classification, not sandboxing.

For a new tool, include its argument schema, handler, policy behavior, error cases, and
deterministic eval coverage.

## External code and attribution

Do not copy substantial implementations from reference repositories. Prefer public APIs
and documented formats. If a contribution adapts external code, disclose the exact source,
revision, files, and license in the pull request and add all notices required by that
license.

The project's current design references and direct dependencies are listed in the README.
