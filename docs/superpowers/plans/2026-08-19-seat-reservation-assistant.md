# Seat Reservation Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Windows service that safely schedules library reservations, reports the assigned seat, and accepts phone-driven changes.

**Architecture:** The FastAPI process owns SQLite state and APScheduler jobs. Pure domain functions decide valid reservation/check-in times; an injectable reservation adapter performs Playwright interactions only after site selectors have been validated. A local token-protected mobile page and WeCom command gateway both call the same application service.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, APScheduler, SQLAlchemy, Pydantic Settings, Playwright, pytest.

---

### Task 1: Create the project and configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `seat_assistant/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write a failing configuration test**

```python
def test_settings_rejects_blank_control_token():
    with pytest.raises(ValidationError):
        Settings(control_token="")
```

- [ ] **Step 2: Run `pytest tests/test_config.py -v` and verify the test fails because `Settings` does not exist.**
- [ ] **Step 3: Implement Pydantic settings with blank reservation credentials permitted and a required control token.**
- [ ] **Step 4: Run `pytest tests/test_config.py -v` and verify it passes.**

### Task 2: Implement time and safety domain rules

**Files:**
- Create: `seat_assistant/domain.py`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Write failing tests for a 45-minute check-in window, a 4-hour maximum reservation, and rejecting a delayed arrival outside the valid window.**
- [ ] **Step 2: Run `pytest tests/test_domain.py -v` and verify the new behavior fails.**
- [ ] **Step 3: Implement `check_in_window`, `build_reservation`, and `apply_delay` as pure functions.**
- [ ] **Step 4: Run `pytest tests/test_domain.py -v` and verify all tests pass.**

### Task 3: Persist reservations, events, and learned arrivals

**Files:**
- Create: `seat_assistant/storage.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write failing tests that save one daily reservation, append a delay event, and compute a median default from samples.**
- [ ] **Step 2: Run `pytest tests/test_storage.py -v` and verify failure.**
- [ ] **Step 3: Implement a SQLite repository with parameterized queries and explicit reservation statuses.**
- [ ] **Step 4: Run `pytest tests/test_storage.py -v` and verify success.**

### Task 4: Parse phone and WeCom text commands

**Files:**
- Create: `seat_assistant/commands.py`
- Test: `tests/test_commands.py`

- [ ] **Step 1: Write failing tests for Chinese and English forms of delay, direct delay time, cancellation, and default-time change.**
- [ ] **Step 2: Run `pytest tests/test_commands.py -v` and verify failure.**
- [ ] **Step 3: Implement a constrained parser returning typed commands; unknown commands produce help rather than actions.**
- [ ] **Step 4: Run `pytest tests/test_commands.py -v` and verify success.**

### Task 5: Add reservation adapter and bounded scheduler

**Files:**
- Create: `seat_assistant/reservation.py`
- Create: `seat_assistant/service.py`
- Create: `seat_assistant/scheduler.py`
- Test: `tests/test_service.py`

- [ ] **Step 1: Write failing service tests for one reservation attempt, no repeat after success, and an ambiguous result stopping retries.**
- [ ] **Step 2: Run `pytest tests/test_service.py -v` and verify failure.**
- [ ] **Step 3: Implement a dry-run adapter plus a Playwright adapter that is disabled without selectors; implement bounded service retries.**
- [ ] **Step 4: Run `pytest tests/test_service.py -v` and verify success.**

### Task 6: Create token-protected mobile control API and page

**Files:**
- Create: `seat_assistant/api.py`
- Create: `seat_assistant/templates/index.html`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing HTTP tests for status access, rejected missing token, delay, cancellation, and default-time update.**
- [ ] **Step 2: Run `pytest tests/test_api.py -v` and verify failure.**
- [ ] **Step 3: Implement FastAPI routes and a compact responsive page that calls those routes with the configured token.**
- [ ] **Step 4: Run `pytest tests/test_api.py -v` and verify success.**

### Task 7: Add notifications and Windows installation artifacts

**Files:**
- Create: `seat_assistant/notifications.py`
- Create: `seat_assistant/main.py`
- Create: `scripts/install-task.ps1`
- Create: `README.md`
- Test: `tests/test_notifications.py`

- [ ] **Step 1: Write failing tests for notification payload rendering and disabled WeCom behavior.**
- [ ] **Step 2: Run `pytest tests/test_notifications.py -v` and verify failure.**
- [ ] **Step 3: Implement notification rendering, application startup, and a task-scheduler installation script that runs the service at logon.**
- [ ] **Step 4: Run `pytest -q` and verify the full suite passes; run `python -m compileall seat_assistant` and verify no compile errors.**
