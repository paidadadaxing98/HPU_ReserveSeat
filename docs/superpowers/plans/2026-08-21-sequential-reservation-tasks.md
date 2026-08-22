# Sequential Reservation Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add stable local business logic for up to five sequential reservation periods per account without changing Windows task installation or the resident process.

**Architecture:** Keep the existing `reservations` table as the durable per-period result and derive task state from enabled period configuration plus local/live reservation records. Add a read-only current-reservation method to the reservation adapter, inject `now` into the scheduler, and let the service enforce quota and local safety checks.

**Tech Stack:** Python 3, dataclasses, SQLite, pytest, existing Playwright adapter.

---

### Task 1: Extend period configuration and quota limits

**Files:**
- Modify: `seat_assistant/config.py`
- Modify: `seat_assistant/initialization.py`
- Modify: `seat_assistant/notifications.py`
- Test: `tests/test_config.py`
- Test: `tests/test_initialization.py`

- [x] **Step 1: Write failing tests** for disabled `period04`/`period05`, an enabled fifth period, and a daily limit of five.
- [x] **Step 2: Run the focused tests** and confirm they fail because only three periods and a limit of three exist.
- [x] **Step 3: Implement** an `enabled` flag, two disabled default periods, five-period validation, and backward-compatible parsing. Keep `--time` with the existing three-value form compatible and accept a five-value form for the two optional periods.
- [x] **Step 4: Run focused config and initialization tests** and confirm they pass.

### Task 2: Add durable live-reservation query boundary

**Files:**
- Modify: `seat_assistant/reservation.py`
- Modify: `scripts/preview_reservation.py`
- Test: `tests/test_reservation.py`
- Test: `tests/test_preview_notifications.py`

- [x] **Step 1: Write failing tests** for a read-only adapter query delegating to an async runner and for the default runner returning current records without submitting.
- [x] **Step 2: Run those tests** and confirm the adapter has no current-reservation method.
- [x] **Step 3: Implement** `current_reservations(day)` on the adapters and a read-only Playwright runner that logs in, captures API auth, and reads the existing current-reservation endpoint.
- [x] **Step 4: Run adapter and preview tests** and confirm they pass.

### Task 3: Make service reservation checks sequential and time-aware

**Files:**
- Modify: `seat_assistant/service.py`
- Modify: `seat_assistant/submission.py` if a public record-time helper is needed
- Test: `tests/test_service.py`

- [x] **Step 1: Write failing tests** for allowing a later period after the earlier local reservation ends, blocking it before that end, and stopping on another period's uncertain result.
- [x] **Step 2: Run focused service tests** and confirm the current permanent same-day block fails the new expectations.
- [x] **Step 3: Implement** time-aware local blocking, preserve same-period idempotence, remove the permanent same-day prohibition, and raise the quota default/limit to five.
- [x] **Step 4: Run all service tests** and update only obsolete assertions whose expected behavior directly contradicts the approved serial workflow.

### Task 4: Replace one-shot scheduler flow with one-next-task flow

**Files:**
- Modify: `seat_assistant/scheduler.py`
- Modify: `seat_assistant/service.py`
- Modify: `seat_assistant/notifications.py`
- Test: `tests/test_scheduler.py`
- Test: `tests/test_local_flow.py`

- [x] **Step 1: Write failing tests** for one task per account per run, waiting on a live active record, advancing after its end, skipping disabled periods, stopping after an uncertain result, and completing only after all enabled periods reach terminal states.
- [x] **Step 2: Run scheduler tests** and confirm the existing loop incorrectly marks later periods skipped and permanently completes the day after one attempt.
- [x] **Step 3: Implement** `run_once(service, day, now=None)` and use per-period summaries with `waiting`, `reserved`, `failed`, `uncertain`, `skipped`, and terminal completion semantics. Keep `run_accounts_once` serial and unchanged in its external shape.
- [x] **Step 4: Run scheduler, local-flow, and notification tests** and confirm they pass.

### Task 5: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `产品期望.md` (local-only, ignored by Git)

- [x] **Step 1: Document** one-to-five-period configuration, repeated execution behavior, live reservation waiting, record preservation, and the fact that task installation is intentionally unchanged in this iteration.
- [x] **Step 2: Run the full pytest suite with the repository temp directory.**
- [x] **Step 3: Run `compileall` for `scripts` and `seat_assistant`.
- [x] **Step 4: Review `git diff` and verify no credentials, browser profiles, databases, or unrelated task files changed.

## v0.8.6 修订记录

- 修复定时预约入口正常完成后读取不到按时段记录的问题。
- 修复失败和结果不明确时调度器覆盖预约详情的问题。
- 本地验证：`289 passed`，编译检查和差异空白检查通过。
