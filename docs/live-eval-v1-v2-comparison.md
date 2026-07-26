# Live Eval v1/v2 Comparison: 2026-07-26

This report compares two versioned 5-case x 3-attempt real-model evaluations. It contains
aggregate, sanitized evidence only: no API key, private gateway URL, local workspace path,
or raw provider payload is included. The suite is intentionally small and is not a general
coding benchmark.

## Fixed Context

| Field | v1 | v2 |
|---|---|---|
| Model | `gpt-5.6-terra` | `gpt-5.6-terra` |
| Backend | `openai-responses-function-codex` | `openai-responses-function-codex` |
| Harness commit | `5d3a6bd` | `e03077f` |
| Cases x attempts | 5 x 3 | 5 x 3 |
| Provider errors | 0 | 0 |
| Python recorded in result | not recorded | `3.12.13` |
| Wall-clock duration | 5m 9s | 6m 11s |

Each attempt started from a clean checked-in fixture. Acceptance used file assertions,
`unittest` exit codes, tool traces, and runtime status. No LLM judge was used.

## Results

| Metric | v1 | v2 | Change |
|---|---:|---:|---:|
| Strict passes | 7/15 (46.7%) | 13/15 (86.7%) | +40.0 pp |
| Strict Pass@3 | 3/5 (60.0%) | 5/5 (100.0%) | +40.0 pp |
| Artifact correctness | 15/15 (100.0%) | 15/15 (100.0%) | unchanged |
| Runtime completion | 12/15 (80.0%) | 15/15 (100.0%) | +20.0 pp |
| Tool contract | 10/15 (66.7%) | 13/15 (86.7%) | +20.0 pp |
| Average turns | 6.67 | 5.27 | -21.0% |
| Average tool calls | 6.33 | 4.53 | -28.4% |
| Model requests | 100 | 79 | -21.0% |
| Input tokens | 78,258 | 52,772 | -32.6% |
| Output tokens | 6,530 | 4,844 | -25.8% |
| Estimated cost | $0.2890 | $0.2046 | -29.2% |

The cost estimate uses recorded OpenAI Standard rates for this model: $2.50 per million
uncached input tokens, $0.25 per million cached input tokens, $3.125 per million cache-write
tokens, and $15.00 per million output tokens. Compatible-gateway billing may differ.

## What Changed

1. The agent shell and post-run evaluator now prepend the Harness interpreter directory to
   `PATH`. Both `python` and `python3` resolve to the recorded Python 3.12 environment.
2. The v2 modification contract accepts either `edit_file` or `write_file`. Both produce a
   valid final artifact; the exact choice remains visible in the trace.
3. Artifact, runtime, tool-contract, and strict metrics are reported independently.
4. One optional no-tools finalization request can summarize after the last allowed tool
   turn. It is bounded and cannot execute another tool.
5. Non-zero Bash exit codes are structured `error` results, while preserving output and the
   exact exit code for model recovery.
6. Expected tool errors no longer override the real failure category. Missing-path recovery
   is distinguished from an unexpected tool failure.
7. Tool descriptions and the system prompt more clearly separate exact edits, full rewrites,
   testing, and final completion.

The finalization path was covered by deterministic tests but was not triggered in any of the
15 v2 attempts. It is a reliability guard, not the cause of the measured v2 improvement.

## Comparability Caveat

The v1 and v2 strict scores are versioned results, not a perfectly controlled model-only
comparison. v2 intentionally corrected an over-specific rubric that required `edit_file`
even when `write_file` produced the right tested artifact. Therefore, the +40.0 percentage
point strict change combines:

- a rubric-validity correction;
- a fixed Python execution environment;
- prompt and tool-description changes;
- ordinary sampling variance from 15 attempts.

Artifact correctness stayed at 100%, so the strongest behavior change is runtime completion:
the three v1 `list_boundary` max-turn failures became 3/3 completed v2 runs.

## Remaining Failures

Two v2 attempts completed normally and passed all artifact tests but inspected files through
`bash` instead of the dedicated `read_file` tool:

| Case | Attempt | Artifact | Runtime | Tool contract | Category |
|---|---:|---:|---:|---:|---|
| `broken_add` | 2 | pass | pass | fail | `tool_contract_error` |
| `list_boundary` | 3 | pass | pass | fail | `tool_contract_error` |

The project keeps these failures. Removing the `read_file` requirement after observing the
results would turn evaluation into score chasing. A future v3 should first decide and
document whether dedicated file reads are a real policy requirement or merely an
implementation preference, then version the rubric before collecting new samples.

## Interview Interpretation

The defensible claim is:

> After correcting an invalid exact-tool constraint and pinning the Python environment, the
> versioned strict score increased from 46.7% to 86.7%, Runtime completion increased from
> 80.0% to 100.0%, Pass@3 reached 100.0%, and estimated cost fell 29.2%. Artifact correctness
> remained 100%. Two remaining failures are isolated tool-contract deviations, not coding
> failures.

Do not claim that the model became 40 percentage points better, that this is a general
coding benchmark, or that 15 attempts establish production reliability.
