# Local Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local assistant's scheduling, command handling, persistence, and HTTP contract reliable enough to become the backend boundary for a future Enterprise WeChat mini program.

**Architecture:** Keep the Playwright reservation adapter behind an injectable interface and use the existing dry-run adapter for local verification. The application service owns domain validation and reservation state transitions; SQLite stores reservations, command idempotency records, events, and persistent default overrides. A versioned standard-library HTTP API exposes status and command submission locally, while the future cloud gateway can call the same contract without receiving campus credentials.

**Tech Stack:** Python 3.12, standard-library `http.server`, SQLite, dataclasses, pytest.

---

### Task 1: Define persistent statuses and command records

**Files:**
- Modify: `seat_assistant/storage.py`
- Test: `tests/test_storage.py`

- [x] **Step 1: Write failing tests** for reservation status updates, persistent default overrides, command idempotency, and event retrieval.
- [x] **Step 2: Run `pytest tests/test_storage.py -q` and verify the new tests fail because the schema/API is missing.
- [x] **Step 3: Implement additive SQLite tables and repository methods**: `save_reservation`, `get_reservation`, `reservations`, `set_default`, `default_override`, `record_command`, `get_command`, and `events`. Existing databases must be upgraded with `CREATE TABLE IF NOT EXISTS` only.
- [x] **Step 4: Run the storage tests and verify they pass.

### Task 2: Make command parsing and service transitions safe

**Files:**
- Modify: `seat_assistant/commands.py`
- Modify: `seat_assistant/service.py`
- Modify: `seat_assistant/reservation.py`
- Test: `tests/test_service.py`
- Test: `tests/test_commands.py`

- [x] **Step 1: Write failing tests** for invalid times, duplicate reservation prevention, failed/uncertain cancellation, safe delay replacement, cancellation status, and manual arrival recording.
- [x] **Step 2: Run the focused tests and verify expected failures.
- [x] **Step 3: Add strict `HH:MM` normalization, `record_arrival`, explicit result handling, and a single service result shape. A successful reservation is never repeated for the same date and period; an uncertain result is never retried automatically. A delay only replaces a known reservation after cancellation succeeds conclusively.
- [x] **Step 4: Add deterministic dry-run behavior for reserve/cancel and keep the Playwright adapter returning an explicit `uncertain` result.
- [x] **Step 5: Run the focused tests and verify they pass.

### Task 3: Make the daily scheduler idempotent and recoverable

**Files:**
- Modify: `seat_assistant/scheduler.py`
- Modify: `seat_assistant/main.py`
- Test: `tests/test_scheduler.py`

- [x] **Step 1: Write failing tests** for running once after 19:30, starting after process restart, skipping an already-reserved period, and isolating one period's failure from the others.
- [x] **Step 2: Run scheduler tests and verify expected failures.
- [x] **Step 3: Implement `run_once` with per-period exception isolation and a repository-backed run marker; make the loop check a time window rather than requiring the exact minute.
- [x] **Step 4: Run scheduler tests and verify they pass.

### Task 4: Freeze a versioned local HTTP contract

**Files:**
- Modify: `seat_assistant/api.py`
- Test: `tests/test_api.py`
- Modify: `README.md`

- [x] **Step 1: Write failing HTTP tests** for `GET /api/v1/health`, authenticated `GET /api/v1/status`, authenticated `POST /api/v1/commands` with a request id, duplicate request id replay, invalid command responses, and missing-token rejection.
- [x] **Step 2: Run API tests and verify expected failures.
- [x] **Step 3: Implement the versioned JSON routes while retaining the existing mobile page as a compatibility shell. Return stable fields: `ok`, `request_id`, `message`, `data`, and `error`; do not put the control token in response bodies.
- [x] **Step 4: Document the request/response examples and the future mini-program boundary in README.
- [x] **Step 5: Run API tests and verify they pass.

### Task 5: Add local integration verification and update run instructions

**Files:**
- Modify: `seat_assistant/config.py`
- Modify: `seat_assistant/main.py`
- Modify: `.env.example`
- Test: `tests/test_local_flow.py`
- Modify: `README.md`

- [x] **Step 1: Write a failing end-to-end local test** that starts with an empty database, runs the next-day booking, reads status, submits a duplicate command, delays a period, and cancels another period.
- [x] **Step 2: Run the integration test and verify it fails for the incomplete flow.
- [x] **Step 3: Wire the service, repository, scheduler, and API with one shared instance and configurable localhost defaults; ensure dry-run is explicit and the service never silently switches to real booking.
- [x] **Step 4: Run the complete test suite and compile check.
- [x] **Step 5: Update README with the stable local API, current completion boundary, and exact stop point before cloud/mini-program work.

### Verification gate

- [x] Run `..\\.venv\\Scripts\\python.exe -m pytest -q` and record the exact count: `65 passed`.
- [x] Run `..\\.venv\\Scripts\\python.exe -m compileall -q seat_assistant scripts`.
- [x] Run a local dry-run command through the HTTP API and confirm the persisted status survives a new process/repository instance.
- [x] Do not claim real website reservation success; the Playwright adapter remains blocked until the site's end-time request contract is revalidated.
