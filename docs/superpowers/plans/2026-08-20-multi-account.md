# 多账号预约配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留单账号兼容性的同时，支持最多 20 个独立账号、独立会话/数据库、串行运行和“每日最多 3 次成功预约”额度。

**Architecture:** 新增账号配置模型读取 `accounts.json`，为每个账号派生隔离的 `Settings` 和运行路径。存储层增加按账号、日期记录成功预约次数的能力；调度层通过账号级服务串行执行并在成功额度耗尽后跳过。预览脚本根据 `--account` 选择账号会话目录，未指定时继续使用旧 `.browser-profile`。

**Tech Stack:** Python dataclasses、JSON 配置、SQLite、Playwright、pytest。

---

### Task 1: 账号配置与路径模型

**Files:**
- Modify: `seat_assistant/config.py`
- Modify: `.gitignore`
- Modify: `.env.example`
- Create: `accounts.example.json`
- Test: `tests/test_config.py`

- [x] **Step 1: Write failing tests** for loading 0/1/20/21 accounts, duplicate IDs/accounts, blank credentials, shared paths, and derived per-account paths.
- [x] **Step 2: Run the focused tests** and confirm they fail before the account model and loader exist.
- [x] **Step 3: Implement** `AccountSettings`, `load_accounts`, `load_account_settings`, and `MAX_ACCOUNTS=20`; preserve the existing `.env` fallback as one default account.
- [x] **Step 4: Ignore sensitive multi-account files** (`accounts.json`, `accounts/`) and add a redacted example configuration.
- [x] **Step 5: Run focused config tests** and confirm all pass.

### Task 2: 成功次数与账号隔离存储

**Files:**
- Modify: `seat_assistant/storage.py`
- Test: `tests/test_storage.py`

- [x] **Step 1: Write failing tests** for independent database paths and successful booking counting only newly successful reservations.
- [x] **Step 2: Run the focused storage tests** and confirm the new tracking method is missing.
- [x] **Step 3: Implement** a `successful_bookings` table keyed by `(date, account_id, reservation_key)`, with idempotent recording and count lookup; keep existing schemas readable.
- [x] **Step 4: Run focused storage tests** and confirm failure cases do not increment counts.

### Task 3: 每账号成功额度与串行调度

**Files:**
- Modify: `seat_assistant/service.py`
- Modify: `seat_assistant/scheduler.py`
- Modify: `seat_assistant/main.py`
- Test: `tests/test_service.py`
- Test: `tests/test_scheduler.py`

- [x] **Step 1: Write failing tests** showing success increments one account's count, failure/uncertain/reused booking does not increment, the fourth success is skipped, and accounts run in configuration order.
- [x] **Step 2: Run focused service/scheduler tests** and confirm they fail before the quota and scheduler changes.
- [x] **Step 3: Implement** account identity on `AssistantService`, enforce `daily_success_limit=3`, record only a newly verified successful result, and add `run_accounts_once` with no concurrency.
- [x] **Step 4: Keep the existing one-account `run_once` behavior** as a compatibility wrapper.
- [x] **Step 5: Run focused service/scheduler tests** and confirm all pass.

### Task 4: 预览脚本选择独立浏览器会话

**Files:**
- Modify: `scripts/preview_reservation.py`
- Modify: `scripts/diagnose_login.py`
- Test: `tests/test_auth_flow.py`

- [x] **Step 1: Write failing tests** for account ID validation and deterministic profile/database path resolution.
- [x] **Step 2: Run focused auth tests** and confirm they fail before account selection is wired in.
- [x] **Step 3: Implement** `--account`, resolve the selected account before launching Playwright, and use that account's profile path; reject unknown IDs without opening a browser.
- [x] **Step 4: Add the selected account ID to local status/diagnostic messages** without printing credentials or cookies.
- [x] **Step 5: Run focused auth tests** and confirm all pass.

### Task 5: 文档与全量验证

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `产品期望.md` (ignored local file)

- [x] **Step 1: Document** single-account compatibility, `accounts.json` format, independent session paths, 20-account limit, serial execution, and success-only quota semantics.
- [x] **Step 2: Mark the product expectation as implemented for account isolation, while keeping captcha/login automation as a separate unresolved issue.**
- [x] **Step 3: Run `pytest`, `compileall`, and `git diff --check`.**
- [x] **Step 4: Verify Git status** contains no `.env`, `accounts.json`, `accounts/`, browser profiles, databases, or `产品期望.md`.
