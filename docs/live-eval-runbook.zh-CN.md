# 本地 Live Eval 复跑手册

这份手册用于以后在本机重新运行 Mini Coding Agent Harness 的真实模型评测。它只记录
公开、可复用的操作流程，不记录真实 API Key、私有网关地址或本机绝对路径。

## 1. 版本口径

项目声明支持 Python 3.11 及以上版本，CI 当前验证 Python 3.11 和 3.12。为了复现
2026-07-26 的 Live Eval v2，优先使用 Python 3.12；该次运行记录的具体版本是
Python 3.12.13。

不要直接使用无法确认版本的系统 `python3`。v1 曾经因为 Agent Shell 解析到 Python 3.9，
导致 Fixture 的 `T | None` 在导入阶段失败。v2 已让 Agent Shell 和评测命令共同使用
Harness 虚拟环境的解释器，但创建虚拟环境时仍应显式选择 Python 版本。

## 2. 创建环境

在仓库根目录执行：

```bash
python3.12 --version
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

如果机器只有 Python 3.11，可以把上面的 `python3.12` 换成 `python3.11`。不要使用
尚未进入 CI 矩阵的 Python 版本生成正式对比数据。

检查 `python`、`python3` 和 CLI 是否来自同一虚拟环境：

```bash
python --version
python3 --version
command -v python
command -v python3
command -v mini-harness
python -c 'import os, sys; print(os.path.realpath(sys.executable))'
python3 -c 'import os, sys; print(os.path.realpath(sys.executable))'
```

最后两个命令应输出同一个解释器实际路径。若不是，重新执行：

```bash
deactivate 2>/dev/null || true
source .venv/bin/activate
```

## 3. 先运行无凭据检查

以下检查不访问真实模型：

```bash
ruff check .
ruff format --check .
mypy
pytest
mini-harness eval evals/cases
mini-harness live-eval evals/live_cases --validate-only
```

只有这些检查通过后，才进行付费 Live Eval。

## 4. macOS Keychain（推荐）

macOS 本地复跑默认使用登录钥匙串保存凭据。第一次在仓库根目录执行：

```bash
./scripts/macos-keychain-openai setup
```

脚本会各提示一次 API Key 和 API Base URL，输入均不回显。API Key 必须是以 `sk-`
开头的 OpenAI 风格；使用官方 OpenAI API 时，Base URL 留空。兼容网关传入完整 API
Root，具体是否以 `/v1` 结尾以网关文档为准。

确认记录是否存在：

```bash
./scripts/macos-keychain-openai status
```

`status` 只显示 `stored` 或 `missing`，不读取或打印真实值。以后运行 CLI 时使用：

```bash
./scripts/macos-keychain-openai run --help
```

`run` 从脚本所在位置定位仓库，并且只启动该仓库的 `.venv/bin/mini-harness`。Key 和
Base URL 只进入这个子进程的环境，不进入调用它的 Shell、命令参数、`.env`、TOML、
Trace、README 或评测结果。macOS 可能在首次读取或钥匙串策略变化后弹出访问授权提示；
应核对请求程序后再允许。

Key 轮换或 Endpoint 变更时只需重新运行 `setup`，后续命令不变。登录钥匙串解决的是
个人 Mac 上反复输入凭据的问题，不是服务器或团队环境的生产级 Secret Manager。CI、
容器和共享机器应使用平台提供的 Secret 注入机制。

脚本使用以下固定标识，便于在“钥匙串访问”中审计或删除：

```text
Account: mini-coding-agent-harness
Service: mini-coding-agent-harness.openai-api-key
Service: mini-coding-agent-harness.openai-base-url
```

## 5. 临时环境变量（非 macOS 兜底）

OpenAI 官方生产建议要求避免把 API Key 写进代码或公开仓库，并通过环境变量或 Secret
Manager 注入。Linux、CI 或临时运行可以继续使用仅对当前 Shell 有效的环境变量。

在 zsh 中使用无回显输入：

```zsh
read -s "OPENAI_API_KEY?OPENAI_API_KEY: "
echo
export OPENAI_API_KEY
```

在 bash 中使用：

```bash
read -rsp "OPENAI_API_KEY: " OPENAI_API_KEY
printf '\n'
export OPENAI_API_KEY
```

只检查变量是否存在，不要输出变量内容：

```bash
test -n "$OPENAI_API_KEY" && echo "OPENAI_API_KEY is set"
```

不要执行以下做法：

```text
export OPENAI_API_KEY="真实值"        # 会进入 Shell History
mini-harness ... --api-key "真实值"  # 可能进入进程参数；CLI 也不提供此参数
把真实值写入 .env / TOML / README    # 容易误提交
echo "$OPENAI_API_KEY"               # 会显示到终端或日志
```

如果 Key 曾经进入 Git、公开日志、截图或聊天记录，应立即在提供方后台撤销并重新生成。

官方参考：

- [Production best practices: API keys](https://developers.openai.com/api/docs/guides/production-best-practices#api-keys)
- [OpenAI SDKs and CLI](https://developers.openai.com/api/docs/libraries#create-and-export-an-api-key)

官方 OpenAI API 不需要设置 `OPENAI_BASE_URL`。OpenAI-compatible 网关可以交互输入：

为了避免把私有地址写进 Shell History，可以交互输入：

```zsh
read "OPENAI_BASE_URL?OPENAI_BASE_URL: "
export OPENAI_BASE_URL
```

只检查变量是否存在：

```bash
test -n "$OPENAI_BASE_URL" && echo "OPENAI_BASE_URL is set"
```

兼容参数的选择顺序：

1. 普通 OpenAI API 或标准兼容网关：`--client-profile standard`。
2. 网关明确要求 Codex Responses 客户端契约：`--client-profile codex`。
3. 优先使用 `--tool-mode function` 验证原生 Function Tools。
4. 只有网关明确拒绝原生工具时才使用 `--tool-mode prompt`，并在报告中保留对应 Backend。

不要把私有 Endpoint 写进公开文档。公开示例统一使用
`https://your-gateway.example/v1`。

## 6. 单 Case 冒烟

先复制一个 Case 到临时目录，不要一开始就运行 15 次：

```bash
smoke_cases="$(mktemp -d)"
cp -R evals/live_cases/broken_add "$smoke_cases/"

./scripts/macos-keychain-openai run live-eval "$smoke_cases" \
  --output eval-results/live-smoke \
  --runs 1 \
  --model gpt-5.6-terra \
  --tool-mode function \
  --client-profile codex
```

使用标准 Profile 时，把最后一行改成：

```bash
  --client-profile standard
```

冒烟验收：

- `Provider Error` 为 0。
- Artifact、Runtime 和 Tool Contract 分层结果可解释。
- `results.json`、`README.md` 和一份 JSONL Trace 已生成。
- 结果中没有 API Key、私有 Endpoint 或本机绝对路径。

## 7. 运行 5 x 3 正式评测

每次复测使用新目录，不覆盖 v1/v2：

```bash
./scripts/macos-keychain-openai run live-eval evals/live_cases \
  --output eval-results/live-v3-YYYYMMDD \
  --runs 3 \
  --model gpt-5.6-terra \
  --tool-mode function \
  --client-profile codex
```

正式运行前固定并记录：

- Git Commit。
- Python 版本。
- 模型名称和 Backend。
- Case 与 Rubric 版本。
- Runs per Case。
- 最大轮数、工具超时和 Policy。
- 价格来源。

不要在同一组运行中途修改 Case、Prompt 或 Rubric。发现问题后先完成当前固定版本，再创建
新版本复测。

## 8. 结果检查

```bash
jq '{
  commit: .git_commit,
  python: .python_version,
  attempts: (.attempts | length),
  strict: ([.attempts[] | select(.passed)] | length),
  artifact: ([.attempts[] | select(.artifact_passed)] | length),
  runtime: ([.attempts[] | select(.runtime_passed)] | length),
  tool_contract: ([.attempts[] | select(.tool_contract_passed)] | length),
  provider_errors: ([.attempts[] | select(.failure_category == "provider_error")] | length),
  cost: ([.attempts[].estimated_cost_usd] | add)
}' eval-results/live-v3-YYYYMMDD/results.json
```

检查 Trace 数量：

```bash
find eval-results/live-v3-YYYYMMDD/traces -type f -name '*.jsonl' | wc -l
```

5 Case x 3 Runs 应得到 15 份 Trace。

使用临时环境变量时，提交公开摘要前检查暂存差异是否包含当前 Key 或 Endpoint，但不要
打印它们：

```bash
if git diff --cached | rg --quiet -F "$OPENAI_API_KEY"; then
  echo "BLOCKED: staged diff contains API key"
else
  echo "OK: API key not found in staged diff"
fi

if test -n "$OPENAI_BASE_URL" &&
  git diff --cached | rg --quiet -F "$OPENAI_BASE_URL"; then
  echo "BLOCKED: staged diff contains private endpoint"
else
  echo "OK: private endpoint not found in staged diff"
fi
```

`eval-results/` 已被 `.gitignore` 忽略。公开仓库只提交脱敏后的聚合报告，并保留旧版本作为
基线，不提交原始私有运行数据。

## 9. 运行结束

Keychain 包装脚本使用 `exec` 启动 CLI，进程结束后凭据环境随之消失，调用它的 Shell
不需要清理。只有使用第 5 节的临时环境变量兜底时才执行：

```bash
unset OPENAI_API_KEY
unset OPENAI_BASE_URL
```

再次确认变量已清除：

```bash
test -z "${OPENAI_API_KEY:-}" && echo "OPENAI_API_KEY cleared"
test -z "${OPENAI_BASE_URL:-}" && echo "OPENAI_BASE_URL cleared"
```

## 10. 常见问题

| 现象 | 优先检查 |
|---|---|
| `401` | Key 是否有效、是否属于当前 Endpoint；不要打印 Key |
| `403` | Profile 是否与网关契约一致；只在网关明确要求时使用 `codex` |
| Function Tools 被拒绝 | 先确认 API 是否支持 Responses 原生工具，再考虑 `prompt` 模式 |
| `429` | 配额或速率限制；等待后重试，不要修改 Case 来规避 |
| `python` 与 `python3` 不一致 | 重新激活 `.venv`，检查解释器实际路径 |
| 结果目录已有数据 | 使用新的版本化目录，不覆盖旧结果 |
| CLI 因部分严格失败返回 1 | 读取分层指标和失败断言；不等于 Provider Error |
