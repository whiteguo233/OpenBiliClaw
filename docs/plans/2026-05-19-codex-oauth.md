# Codex OAuth 登录支持实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an experimental Codex OAuth authentication mode for the existing OpenAI provider by importing and refreshing local Codex CLI credentials.

**Architecture:** Keep provider routing unchanged: `openai` remains the provider, `auth_mode` chooses where its bearer token comes from. `codex_auth.py` owns credentials and refresh, while `OpenAIProvider` only knows how to ask for a valid token and retry once after 401.

**Tech Stack:** Python 3.11, dataclasses, `httpx`, Typer, Rich, OpenAI Python SDK, pytest, Ruff, MyPy.

---

## Phase 0: Scope Guard

首版不实现自建 PKCE 浏览器 OAuth。`openbiliclaw login codex` 通过官方 `codex login` 产生或刷新 `~/.codex/auth.json`，然后导入本项目凭据路径。这样降低 client_id、redirect URI、OAuth 参数漂移带来的维护风险。

## Phase 1: OAuth 凭据核心模块

**新增** `src/openbiliclaw/llm/codex_auth.py`

### Task 1.1: 凭据模型和安全落盘

**Files:**
- Create: `src/openbiliclaw/llm/codex_auth.py`
- Test: `tests/test_codex_auth.py`

**Behavior:**
- `CodexCredentials(access_token, refresh_token, expires_at, account_id="")`
- `is_expired(skew_seconds=300)` 判断过期和临期。
- `save_codex_credentials()` 写入 `~/.openbiliclaw/codex_auth.json`，尽量设置 `0600`。
- `load_codex_credentials()` 读取失败或文件缺失返回 `None` / 抛出清晰错误。
- 不在日志、异常或 CLI status 中输出 token 明文。

### Task 1.2: 导入 Codex CLI 凭据

**Behavior:**
- `import_codex_credentials(source=None, destination=None)`
- 默认读取 `~/.codex/auth.json`
- 支持两类结构：
  - flat: `{ "access_token": "...", "refresh_token": "...", "expires_at": 123 }`
  - nested: `{ "tokens": { "access_token": "...", "refresh_token": "...", "expires_at": 123 } }`
- 如果缺少 `expires_at`，从 JWT access token 的 `exp` claim 解析。
- 导入后写入 OpenBiliClaw 凭据路径。

### Task 1.3: 刷新和有效 token 获取

**Behavior:**
- `refresh_codex_token(credentials, token_path=None, client=None)`
- 使用 `httpx.AsyncClient` POST `https://auth.openai.com/oauth/token`
- `get_valid_codex_token(force_refresh=False)` 临期时刷新，返回 access token。
- 使用模块级 `asyncio.Lock` 避免并发刷新。
- 刷新失败抛出 `CodexAuthError`。

### Task 1.4: Codex CLI 登录辅助

**Behavior:**
- `run_codex_cli_login()` 调用外部 `codex login`。
- `openbiliclaw login codex` 无可导入凭据时调用它，然后再次导入。
- `codex` 不存在或登录后仍无 auth 文件时给出可操作错误。

## Phase 2: Config 扩展

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_config.py`, `tests/test_api_app.py`, `tests/test_api_config_guards.py`

**Behavior:**
- `LLMProviderConfig.auth_mode: str = ""`
- `auth_mode == ""` 或 `"api_key"` 保持现有行为。
- `auth_mode == "codex_oauth"` 只对 `[llm.openai]` 有效。
- `_render_provider_section("openai", ...)` 保留 `auth_mode`。
- `/api/config` GET/PUT 能 round-trip `auth_mode`。
- 诊断：
  - 无本地 Codex 凭据时提示运行 `openbiliclaw login codex`。
  - 同时设置 `api_key` 时提示会被忽略。
  - `base_url` 指向非 OpenAI 官方 API 域名时给 blocking issue。

## Phase 3: Provider 集成

**Files:**
- Modify: `src/openbiliclaw/llm/openai_provider.py`
- Modify: `src/openbiliclaw/llm/registry.py`
- Test: `tests/test_llm_providers.py`, `tests/test_llm_registry.py`

**Behavior:**
- `OpenAIProvider.__init__` 新增 `token_provider: Callable[[bool], Awaitable[str]] | None`。
- `_request_with_retry()` 每次请求前用 `token_provider(False)` 更新 SDK client 的 `api_key`。
- 原始异常 `status_code == 401` 且有 token_provider 时调用 `token_provider(True)` 强刷并重试一次。
- 401 刷新失败时抛出清晰 `LLMProviderError`。
- registry 在 `auth_mode=codex_oauth` 时构造 `OpenAIProvider(api_key=<current token>, token_provider=get_valid_codex_token)`。

## Phase 4: CLI 命令

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`

**Commands:**

```bash
openbiliclaw login codex
openbiliclaw login codex --import
openbiliclaw login codex --source ~/.codex/auth.json
openbiliclaw login codex --status
openbiliclaw login codex --logout
```

**Behavior:**
- `--status` 显示是否已登录、账号 ID、过期时间和是否临期。
- `--logout` 只删除 OpenBiliClaw 的 `~/.openbiliclaw/codex_auth.json`。
- `--import` 只导入，不调用外部 `codex login`。
- 无 flag 默认：先尝试导入；没有可导入凭据时调用 `codex login` 再导入。

## Phase 5: 文档

**Files:**
- Modify: `config.example.toml`
- Modify: `docs/modules/llm.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`

**Behavior:**
- 文档必须明确 `codex_oauth` 是实验性/非官方路径。
- CLI 文档新增 `login codex`。
- 配置文档新增 `auth_mode` 和 `base_url` 安全约束。

## Phase 6: 验证

Run:

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
pytest tests/test_codex_auth.py tests/test_config.py tests/test_llm_providers.py tests/test_llm_registry.py tests/test_cli.py tests/test_api_config_guards.py tests/test_api_app.py
pytest
```

If full `pytest` is too slow, at minimum run all targeted tests plus `pytest tests/test_llm_module_routing_e2e.py tests/test_api_degraded_mode.py`.
