# Mini Coding Agent Harness

[English](README.md)

[![CI](https://github.com/cf-x/mini-coding-agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/cf-x/mini-coding-agent-harness/actions/workflows/ci.yml)

这是一个小型、可测试、可追踪的 Coding Agent Harness。项目不追求工具数量，而是
把模型决策、权限判断、工具执行、运行轨迹和回归评测拆成清晰模块，回答一个具体问题：

> 如何让最小 Coding Agent 具备可观察的执行过程、受控的失败边界和可重复的回归测试？

![确定性终端演示](docs/terminal-demo.gif)

演示内容是离线 Conformance Eval、脱敏 Trace、Replay Divergence 和不需要凭据的 Live Case
校验，不包含伪造的真实模型运行。

## 核心能力

- 异步 Agent Loop 和最大轮数控制。
- `read_file`、`write_file`、`edit_file`、`bash` 四个工具。
- Pydantic 参数校验与统一 `ToolResult`。
- Workspace 路径和符号链接越界检查。
- 工具执行前的 `allow / ask / deny` 权限决策。
- Shell 超时、进程组终止和输出截断。
- 带脱敏能力的 Append-only JSONL Trace。
- 使用历史模型响应驱动 Runtime 的离线 Replay。
- 对规范化 Tool Call 定位首个轨迹分歧。
- 十个不请求真实模型的确定性 Eval Case。
- OpenAI Responses、可选 Prompt 工具兼容模式、Anthropic 和 Replay Model Client。
- 五个带确定性验收与增量报告的 Live Coding Case。

项目明确不做多 Agent、Task DAG、MCP、Web UI、Durable Execution 或生产级 Sandbox。

## 快速开始

要求 Python 3.11 及以上版本。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
mini-harness eval evals/cases
```

运行真实 OpenAI 模型：

```bash
./scripts/macos-keychain-openai setup  # 仅首次或轮换凭据时执行
./scripts/macos-keychain-openai run run \
  --workspace ./example-workspace \
  "检查测试，修复缺陷，并运行相关测试。"
```

macOS 包装脚本把 OpenAI 风格的 `sk-` Key 和可选 Base URL 保存在登录钥匙串中，后续只
向仓库的 `.venv/bin/mini-harness` 进程注入。Linux、CI 或临时运行仍可使用
`OPENAI_API_KEY` 和可选的 `OPENAI_BASE_URL` 环境变量。不要把真实值写入仓库。

默认 `--tool-mode auto` 优先使用 Responses 原生 Function Tools。兼容网关明确拒绝原生
工具时，可以回退到 JSON Prompt 协议；`function` 可强制原生模式，`prompt` 可显式选择
兼容模式。报告会记录 Backend，不把 Prompt 模式描述成原生 Function Calling。

部分网关会把 OpenAI 订阅账号限制为 Codex Responses 客户端契约。此时需要显式使用
`--client-profile codex`；默认的 `standard` 仍保留普通 OpenAI SDK 请求头。该设置不包含
凭据，也不改变 `OPENAI_API_KEY` 的读取方式。Codex 档案使用无状态 HTTP 续接，因为部分
兼容网关只在 Responses WebSocket v2 中支持 `previous_response_id`。

文件写入和普通 Shell 命令默认需要询问。`--auto-approve` 只应在你完全控制的
Workspace 中使用。

## 架构与执行链路

```text
用户任务
  -> Runtime 请求 Model Client
  -> Model 返回文本或 Tool Call
  -> Policy Engine 在执行前做权限判断
  -> Tool Registry 校验参数并执行
  -> Tool Result 放回消息历史
  -> 继续循环或受控终止
  -> Trace 记录全链路
  -> Replay / Eval 检查当前行为
```

允许执行的工具调用形成：

```text
tool_requested -> policy_decided -> tool_started -> tool_finished
```

拒绝的调用不产生 `tool_started`，但仍产生结构化 `tool_finished`，因此每个
Tool Call 都有且只有一个 Tool Result。

## CLI

```text
mini-harness run TASK [--workspace PATH] [--config FILE] [--trace FILE]
mini-harness replay TRACE [--workspace PATH] [--output-trace FILE]
mini-harness eval [CASES_DIR] [--json]
mini-harness live-eval [CASES_DIR] [--runs 3] [--validate-only]
mini-harness trace TRACE
```

- `run` 默认使用 OpenAI Responses API，也可通过 `--provider anthropic` 使用 Anthropic。
- `replay` 复用历史模型响应，但重新执行当前 Policy 和 Tool。
- `eval` 将每个 Fixture 复制到独立临时 Workspace 后执行确定性断言。
- `live-eval` 重复运行五个真实模型任务，增量保存 JSON、Markdown、Token、耗时、
  失败分类和脱敏 Trace。
- `trace` 汇总事件数量和终止状态。

## Replay 的能力边界

Replay 不复制最终答案，而是把历史 `model_response` 依次放回正常 Runtime，
重新运行权限检查和工具执行，再比较工具名称与规范化参数。它可以指出第一次工具缺失、
多余、名称不同或参数不同。

第一版不恢复任意网络请求、时间、随机数和外部副作用，也不自动恢复旧的文件系统快照。
Eval Case 通过干净 Fixture 提供确定起点。

## Eval

十个案例覆盖：文件读取、编辑并测试、路径越界、危险命令、工具错误、未知工具、
输出截断、最大轮数、Replay 一致和 Replay 分歧。

所有判断都来自文件状态、命令退出码、工具轨迹、权限决策和运行状态，不使用
LLM-as-a-Judge。真实验证结果只会在当前 Revision 完整执行后记录：

### Live Eval

不提供 Key、也不发送网络请求时，可以先验证全部五个 Case：

```bash
mini-harness live-eval evals/live_cases --validate-only
```

凭据可用后执行预定的 5 个 Case x 3 次：

```bash
./scripts/macos-keychain-openai run live-eval evals/live_cases --runs 3
```

每次从干净 Fixture 开始，以文件断言和 `unittest` 退出码验收。`gpt-5.6-sol`、
`gpt-5.6-terra`、`gpt-5.6-luna` 的成本估算使用
[OpenAI Standard API 官方价格](https://developers.openai.com/api/docs/pricing)，结果中
会保存价格来源；兼容网关实际账单可能不同。

已于 2026-07-26 完成两组版本化的 `gpt-5.6-terra` 15 次真实运行。v1 保留原始
7/15（46.7%）严格结果，并暴露了过度限定编辑工具和 Python 环境漂移。v2 固定 Harness
解释器、增加分层指标和有界收尾轮，并允许两种受支持的文件修改工具：严格通过
13/15（86.7%），Pass@3 100%，Runtime 完成 15/15，产物正确 15/15。剩余两次失败都是
未调用专用 `read_file` 的工具契约偏差。由于 Rubric 已版本化调整，不能把严格分数变化
完全归因于模型能力。详见 [v1 脱敏分析](docs/live-eval-2026-07-26.md) 和
[v1/v2 脱敏对比](docs/live-eval-v1-v2-comparison.md)。

以后复跑时，Python 版本、macOS Keychain、临时环境变量兜底、兼容网关参数、冒烟、
5×3 正式运行和脱敏检查统一按
[本地 Live Eval 复跑手册](docs/live-eval-runbook.zh-CN.md)执行。

### 可检查证据

以下是由 [`scripts/generate_demo_artifacts.py`](scripts/generate_demo_artifacts.py)
生成的确定性 Harness 示例，不是真实模型 Benchmark：

- [成功编辑并测试的 Trace](examples/traces/successful-edit.jsonl)
- [危险命令拒绝 Trace](examples/traces/dangerous-command-denied.jsonl)
- [Replay 首次分歧](examples/replay/first-divergence.json)

<!-- VERIFIED_RESULTS_START -->

已于 2026-07-26 在 macOS arm64、Python 3.12.13 环境验证：

- Ruff Lint 与格式检查通过。
- MyPy 严格类型检查通过，共检查 41 个源码/测试文件。
- pytest：57 passed。
- 确定性 Eval：10/10 Case 通过。

<!-- VERIFIED_RESULTS_END -->

## 安全说明

Policy Engine 是风险分类器，不是操作系统沙箱。路径解析和字符串规则无法隔离 Shell
展开、解释器、子进程、挂载点、网络或未知二进制文件。不要在敏感主机上运行不可信任务。
生产版本应把工具放入容器、虚拟机、SWE-ReX 或其他专用隔离环境。

## 参考、归属与复用说明

本仓库是独立实现，没有复制以下项目的源码文件或大段实现：

- [`shareAI-lab/learn-claude-code`](https://github.com/shareAI-lab/learn-claude-code)
  （MIT）：参考其递进式 Agent Loop、工具注册和权限流程教学思路。本项目重新设计为
  Runtime、Policy、Trace、Replay、Eval 等独立模块，不包含其源码。
- [`openai/codex`](https://github.com/openai/codex)（Apache-2.0）：仅参考
  “权限决策”和“执行隔离”分层的设计思想，不依赖 Codex，也未包含其源码。
- [`laude-institute/harbor`](https://github.com/laude-institute/harbor)：作为后续
  黑盒 Agent Benchmark 和 ATIF 轨迹兼容方向，MVP 未集成。
- [`SWE-agent/SWE-ReX`](https://github.com/SWE-agent/SWE-ReX)：仅作为后续
  Sandbox Adapter 候选，当前未集成。

直接运行时依赖包括 OpenAI Python SDK、Anthropic Python SDK、Pydantic、Typer 和
PyYAML；测试与质量
检查使用 pytest、pytest-asyncio、Ruff 和 MyPy。版本约束见
[`pyproject.toml`](pyproject.toml)，各依赖仍遵循其各自许可证。

## 开发检查

```bash
ruff check .
ruff format --check .
mypy
pytest
mini-harness eval evals/cases
```

CI 在 Python 3.11 和 3.12 上运行这些命令。测试不需要 API Key，也不会发送真实模型请求。

变更要求见 [`CONTRIBUTING.md`](CONTRIBUTING.md)，安全边界和报告方式见
[`SECURITY.md`](SECURITY.md)。

## License

MIT，见 [`LICENSE`](LICENSE)。
