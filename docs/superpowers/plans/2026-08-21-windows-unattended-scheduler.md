# Windows Unattended Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reliable, hidden Windows triggers that wake the computer and run one reservation period without a resident Python process or foreground browser.

**Architecture:** Keep business decisions in the existing scheduler and add a `target_period` boundary so each trigger can process only its own period. Add a one-shot Python entry point that maps the trigger to today/tomorrow, then add three hidden Windows Scheduled Tasks with wake and missed-run settings.

**Tech Stack:** Python 3.11, Playwright, SQLite, PowerShell ScheduledTasks module, pytest.

---

### Task 1: Lock trigger and browser behavior with tests

**Files:**
- Create: `tests/test_scheduled_task.py`
- Create: `tests/test_browser_session.py`
- Modify: `tests/test_scheduler.py`

- [x] Test morning maps to tomorrow, afternoon/evening map to today, and an expired trigger safely skips.
- [x] Test a target-period run does not jump to a later period.
- [x] Test the browser lifecycle accepts and forwards `headless=True`.

### Task 2: Implement one-shot scheduling boundaries

**Files:**
- Create: `scripts/run_scheduled_task.py`
- Modify: `seat_assistant/scheduler.py`
- Modify: `seat_assistant/service.py`

- [x] Add `target_period` to the service and scheduler entry points.
- [x] Add safe trigger-to-date mapping and a one-shot command that exits after one run.
- [x] Preserve per-account serial execution and existing notification behavior.

### Task 3: Make the browser invisible for scheduled runs

**Files:**
- Modify: `seat_assistant/browser_session.py`
- Modify: `scripts/preview_reservation.py`

- [x] Pass `headless=True` from scheduled reservation and current-reservation readers.
- [x] Keep interactive diagnostics and initialization visible by default.

### Task 4: Install Windows task triggers

**Files:**
- Modify: `scripts/install-task.ps1`

- [x] Remove the old logon-start resident task.
- [x] Register morning, afternoon, and evening hidden tasks with wake, missed-run, battery, execution-limit, and no-concurrency settings.
- [x] Support configurable times and an uninstall switch.

### Task 5: Document and verify

**Files:**
- Modify: `README.md`
- Modify: `产品期望.md` (local-only)
- Modify: `.gitignore`
- Create: `docs/superpowers/specs/2026-08-21-windows-unattended-scheduler-design.md`

- [x] Document the default times, sleep/lock/logout boundaries, logs, install, override, and uninstall commands.
- [x] Ignore local scheduled-task logs.
- [x] Run focused tests, PowerShell parsing, full tests, compileall, and diff checks.
