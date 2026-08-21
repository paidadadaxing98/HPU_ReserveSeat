# Account Initialization And Scheduled Reservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe, interactive account initialization flow and connect initialized accounts to scheduled reservation execution and WeCom notifications for v0.8.

**Architecture:** Keep account preferences in `accounts.json` and runtime verification state in each account's SQLite database. Extract the browser/login/API verification boundary from the preview script into reusable modules, let initialization use that boundary without reservation actions, and let the scheduler reject accounts whose state is not `ready` before entering the booking flow.

**Tech Stack:** Python 3, dataclasses, SQLite, Playwright, argparse, pytest, Windows Task Scheduler, WeCom webhook.

---

## File Map

- Modify `seat_assistant/config.py`: default learning windows, seat preference parsing, and account selection metadata.
- Modify `seat_assistant/storage.py`: initialization state and capability persistence.
- Create `seat_assistant/initialization.py`: pure initialization state/capability helpers and preference serialization.
- Create `seat_assistant/browser_session.py`: reusable locked persistent browser context and login/API capture helpers.
- Create `scripts/initialize_account.py`: interactive initialization command; never calls reservation or cancellation.
- Modify `seat_assistant/scheduler.py`: initialization gate, result summary, and scheduled notification hook.
- Modify `seat_assistant/main.py`: scheduled loop uses the integrated scheduler path and reports account readiness.
- Modify `seat_assistant/service.py`: initialization gate for direct service runs and preference-aware reservation parameters.
- Modify `seat_assistant/reservation.py`: replace the real adapter placeholder with the reusable browser-backed booking boundary or an explicit safe result when calibration is unavailable.
- Modify `scripts/preview_reservation.py`: reuse browser/session helpers and accept persisted seat preference modes while retaining manual preview commands.
- Modify `seat_assistant/notifications.py`: initialization and scheduler summary messages with existing exception isolation.
- Modify `accounts.example.json`: v0.8 defaults and seat preference schema.
- Modify `README.md`: add the Chinese `快速开始` section and document initialization/scheduling behavior.
- Modify `产品期望.md`: update the local product notes; keep it ignored and out of Git.
- Modify tests under `tests/`: test each new state/config/scheduler/notification contract before production changes.

### Task 1: Add default windows and seat preference model

**Files:**
- Modify: `seat_assistant/config.py`
- Modify: `accounts.example.json`
- Test: `tests/test_config.py`
- Test: `tests/test_initialization.py`

- [ ] **Step 1: Write failing tests for the v0.8 defaults and preference parsing**

```python
def test_default_periods_use_the_three_learning_windows():
    settings = Settings(control_token="local-token")
    assert settings.periods["morning"].arrival_window == ("08:00", "12:00")
    assert settings.periods["afternoon"].arrival_window == ("14:30", "18:30")
    assert settings.periods["evening"].arrival_window == ("19:30", "22:00")


def test_account_settings_loads_random_floor_and_seat_preferences(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text(
        '{"accounts":[{"id":"alice","account":"1001","password":"secret",'
        '"initialization":{"seat_preference":{"mode":"floor","floor":"4"}}}]}',
        encoding="utf-8",
    )

    settings = load_account_settings("alice")

    assert settings.seat_preference == {"mode": "floor", "floor": "4"}


def test_old_preferred_seats_are_loaded_as_seat_preference(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text(
        '{"accounts":[{"id":"alice","account":"1001","password":"secret",'
        '"initialization":{"preferred_seats":["169","168"]}}]}',
        encoding="utf-8",
    )

    assert load_account_settings("alice").seat_preference == {
        "mode": "seats", "seats": ["169", "168"]
    }
```

- [ ] **Step 2: Run the focused tests and verify they fail for missing v0.8 behavior**

Run: `.\.venv\Scripts\pytest.exe tests/test_config.py tests/test_initialization.py -q`
Expected: FAIL because the new defaults and `seat_preference` contract do not exist.

- [ ] **Step 3: Implement the minimal config model**

Add a `seat_preference` field to `Settings` and `AccountSettings`, use the three confirmed default windows, parse `seat_preference.mode` values `random`, `floor`, and `seats`, and map legacy `preferred_seats` to `{"mode": "seats", "seats": [...]}`. Reject missing floor/seat values and unknown modes with Chinese validation errors. Keep `preferred_seats` populated for old callers.

- [ ] **Step 4: Run the focused tests and existing configuration tests**

Run: `.\.venv\Scripts\pytest.exe tests/test_config.py tests/test_initialization.py -q`
Expected: PASS.

- [ ] **Step 5: Commit the config contract**

```powershell
git add seat_assistant/config.py accounts.example.json tests/test_config.py tests/test_initialization.py
git commit -m "feat: 增加 v0.8 学习窗口和座位偏好配置"
```

### Task 2: Persist initialization state and capabilities

**Files:**
- Modify: `seat_assistant/storage.py`
- Create: `seat_assistant/initialization.py`
- Test: `tests/test_storage.py`
- Test: `tests/test_initialization.py`

- [ ] **Step 1: Write failing tests for state lifecycle**

```python
def test_repository_initialization_state_defaults_to_pending(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"), account_id="alice")

    assert repo.initialization_state()["status"] == "pending"
    assert repo.initialization_state()["account_id"] == "alice"


def test_repository_saves_ready_initialization_with_capabilities(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"), account_id="alice")
    repo.save_initialization_state(
        status="ready",
        login_verified=True,
        home_verified=True,
        my_reservations_verified=True,
        capabilities={"my_reservations": True, "history": True},
        message="初始化验证成功",
    )

    state = repo.initialization_state()
    assert state["status"] == "ready"
    assert state["capabilities"] == {"my_reservations": True, "history": True}
    assert state["last_verified_at"]


def test_ready_requires_all_verification_flags():
    assert initialization_status(False, True, True) == "failed"
    assert initialization_status(True, True, True) == "ready"
```

- [ ] **Step 2: Run the focused tests and verify the expected missing-table/API failures**

Run: `.\.venv\Scripts\pytest.exe tests/test_storage.py tests/test_initialization.py -q`
Expected: FAIL because initialization storage and status helpers are absent.

- [ ] **Step 3: Implement the state table and pure helpers**

Create an `account_initialization` table keyed by `account_id`, with status flags, JSON capabilities, verification timestamp, and message. Return a pending record when no row exists. Add `initialization_status` and a helper that returns the safe scheduler message for non-ready states.

- [ ] **Step 4: Run storage and initialization tests**

Run: `.\.venv\Scripts\pytest.exe tests/test_storage.py tests/test_initialization.py -q`
Expected: PASS.

- [ ] **Step 5: Commit the state layer**

```powershell
git add seat_assistant/storage.py seat_assistant/initialization.py tests/test_storage.py tests/test_initialization.py
git commit -m "feat: 保存账号初始化状态和接口能力"
```

### Task 3: Add reusable browser verification boundary

**Files:**
- Create: `seat_assistant/browser_session.py`
- Modify: `scripts/preview_reservation.py`
- Modify: `seat_assistant/initialization.py`
- Test: `tests/test_browser_session.py`

- [ ] **Step 1: Write failing unit tests for the safe verification contract**

```python
def test_initialization_verification_never_calls_reservation_actions():
    calls = []

    class FakeVerifier:
        async def verify(self):
            calls.append("verify")
            return {"home": True, "my_reservations": True, "capabilities": {"history": True}}

    result = asyncio.run(run_initialization_verification(FakeVerifier()))

    assert result["ready"] is True
    assert calls == ["verify"]
```

- [ ] **Step 2: Run the focused test and verify it fails because the reusable boundary is missing**

Run: `.\.venv\Scripts\pytest.exe tests/test_browser_session.py -q`
Expected: FAIL with an import or missing-boundary error.

- [ ] **Step 3: Extract the locked browser and login/API capture helpers**

Move only reusable behavior from `scripts/preview_reservation.py`: persistent context creation with `AccountLock`, login redirect handling, credential/captcha login, request authentication capture, and safe API header construction. Keep booking-specific DOM actions in the preview/booking layer. Define a verifier interface that opens the configured login URL, confirms the seat app home route, invokes only the “我的预约” history/current endpoints, and returns redacted capabilities.

- [ ] **Step 4: Run focused and existing auth tests**

Run: `.\.venv\Scripts\pytest.exe tests/test_browser_session.py tests/test_auth_flow.py tests/test_calibrate_script.py -q`
Expected: PASS.

- [ ] **Step 5: Commit the reusable verification boundary**

```powershell
git add seat_assistant/browser_session.py seat_assistant/initialization.py scripts/preview_reservation.py tests/test_browser_session.py
git commit -m "refactor: 抽取账号登录和接口验证边界"
```

### Task 4: Implement the interactive initialization command

**Files:**
- Create: `scripts/initialize_account.py`
- Modify: `seat_assistant/initialization.py`
- Modify: `seat_assistant/config.py`
- Test: `tests/test_initialize_account.py`

- [ ] **Step 1: Write failing tests for command parsing and no-booking safety**

```python
def test_parse_period_arguments_accepts_partial_overrides():
    assert parse_period_arguments(["morning=08:00-12:00"]) == {
        "morning": ("08:00", "12:00")
    }


def test_initialize_workflow_does_not_call_reserve_or_cancel():
    class FakeBrowserVerifier:
        async def verify(self):
            return {"ready": True, "capabilities": {"my_reservations": True}, "seat_catalog": []}

    class ForbiddenBookingAdapter:
        def reserve(self, *args):
            raise AssertionError("初始化不得预约")

        def cancel(self, *args):
            raise AssertionError("初始化不得取消预约")

    result = run_interactive_initialization(
        account_id="alice",
        verifier=FakeBrowserVerifier(),
        booking_adapter=ForbiddenBookingAdapter(),
        input_fn=iter(["", "random"]).__next__,
    )

    assert result["status"] == "ready"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `.\.venv\Scripts\pytest.exe tests/test_initialize_account.py -q`
Expected: FAIL because the command parser and workflow are absent.

- [ ] **Step 3: Implement the command and config writer**

Add argparse options `--account` (required when multiple accounts exist) and repeatable `--period`. Load only enabled account IDs. Display the three default/current windows and ask one-by-one for blank-to-keep or `HH:MM-HH:MM` replacements. Display the seat catalog returned by verification and ask for `random`, `floor`, or `seats`; collect a floor or space-separated seat list as required. Write only the selected account's `initialization` object while preserving credentials, other accounts, unknown fields, and legacy preferred seats. Save ready/failed state in the isolated repository. Never import or invoke reservation submission functions.

- [ ] **Step 4: Run command tests and CLI help**

Run: `.\.venv\Scripts\pytest.exe tests/test_initialize_account.py -q`
Run: `.\.venv\Scripts\python.exe scripts\initialize_account.py --help`
Expected: PASS and help lists `--account` and repeatable `--period`.

- [ ] **Step 5: Commit the initialization command**

```powershell
git add scripts/initialize_account.py seat_assistant/initialization.py seat_assistant/config.py tests/test_initialize_account.py
git commit -m "feat: 增加账号交互式初始化命令"
```

### Task 5: Gate scheduling and integrate WeCom scheduler notifications

**Files:**
- Modify: `seat_assistant/scheduler.py`
- Modify: `seat_assistant/service.py`
- Modify: `seat_assistant/notifications.py`
- Modify: `seat_assistant/main.py`
- Test: `tests/test_scheduler.py`
- Test: `tests/test_service.py`
- Test: `tests/test_notifications.py`

- [ ] **Step 1: Write failing tests for the readiness gate and scheduler messages**

```python
def test_run_once_skips_uninitialized_service_without_reservation_call(tmp_path):
    service, _ = make_service(tmp_path)
    service.repo.save_initialization_state(
        status="pending", login_verified=False, home_verified=False,
        my_reservations_verified=False, capabilities={}, message="请先初始化账号",
    )

    result = run_once(service, "2026-08-22")

    assert result["status"] == "skipped"
    assert "请先初始化" in result["message"]
    assert service.adapter.reserve_calls == []


def test_scheduler_notification_contains_account_id_and_skip_reason(tmp_path):
    text = render_scheduler_summary("alice", "2026-08-22", {"status": "skipped", "message": "请先初始化账号"})
    assert "alice" in text
    assert "请先初始化账号" in text
```

- [ ] **Step 2: Run scheduler/service/notification tests and verify RED**

Run: `.\.venv\Scripts\pytest.exe tests/test_scheduler.py tests/test_service.py tests/test_notifications.py -q`
Expected: FAIL because the readiness gate and summary renderer are missing.

- [ ] **Step 3: Implement the gate and notification path**

Make `run_once` check `repo.initialization_state()` before any period loop. Return and persist a clear account-level skip summary for non-ready state. Keep direct `AssistantService.reserve_period` safe by applying the same gate. Add account-aware scheduler summary rendering and a `send_scheduler_notification` wrapper that isolates webhook errors. Keep existing reservation notification behavior unchanged.

- [ ] **Step 4: Run focused and full service tests**

Run: `.\.venv\Scripts\pytest.exe tests/test_scheduler.py tests/test_service.py tests/test_notifications.py -q`
Expected: PASS.

- [ ] **Step 5: Commit scheduler integration**

```powershell
git add seat_assistant/scheduler.py seat_assistant/service.py seat_assistant/notifications.py seat_assistant/main.py tests/test_scheduler.py tests/test_service.py tests/test_notifications.py
git commit -m "feat: 调度前检查初始化并发送运行通知"
```

### Task 6: Connect the real booking boundary and persisted preferences

**Files:**
- Modify: `seat_assistant/reservation.py`
- Modify: `scripts/preview_reservation.py`
- Modify: `seat_assistant/service.py`
- Modify: `seat_assistant/seat_inventory.py`
- Test: `tests/test_reservation.py`
- Test: `tests/test_seat_inventory.py`

- [ ] **Step 1: Write failing tests for preference-driven candidate ordering and safe real adapter behavior**

```python
def test_seat_preference_orders_random_floor_and_explicit_seats():
    seats = [Seat("001", "1F", "FREE"), Seat("169", "4F", "FREE"), Seat("170", "4F", "FREE")]
    assert [seat.number for seat in candidates_for_preference(seats, {"mode": "seats", "seats": ["170", "169"]})] == ["170", "169"]
    assert all(seat.floor == "4F" for seat in candidates_for_preference(seats, {"mode": "floor", "floor": "4F"}))


def test_real_adapter_returns_uncertain_without_calibration_instead_of_submitting():
    result = PlaywrightReservation().reserve("2026-08-22", "morning", "08:30", "12:00")
    assert not result.success
    assert not result.conclusive
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.\.venv\Scripts\pytest.exe tests/test_reservation.py tests/test_seat_inventory.py -q`
Expected: FAIL because preference candidate selection and the test contract are missing.

- [ ] **Step 3: Implement preference-aware candidate selection and adapter boundary**

Parse floor metadata from layout records when available, order explicit seats first, filter floor candidates, and select a random candidate only from the current free list using an injectable random source for tests. Keep the real adapter conservative: if the reusable Playwright booking boundary is not available or calibration is incomplete, return an uncertain result and do not claim a reservation. Pass persisted settings through the service so scheduled runs use initialization preferences.

- [ ] **Step 4: Run focused and existing preview/submission tests**

Run: `.\.venv\Scripts\pytest.exe tests/test_reservation.py tests/test_seat_inventory.py tests/test_preview.py tests/test_submission.py tests/test_preview_notifications.py -q`
Expected: PASS.

- [ ] **Step 5: Commit preference and booking boundary changes**

```powershell
git add seat_assistant/reservation.py seat_assistant/service.py seat_assistant/seat_inventory.py scripts/preview_reservation.py tests/test_reservation.py tests/test_seat_inventory.py
git commit -m "feat: 让预约流程使用初始化座位偏好"
```

### Task 7: Update documentation and release v0.8

**Files:**
- Modify: `README.md`
- Modify: `产品期望.md`
- Modify: `accounts.example.json`
- Test: `tests/test_commands.py`

- [ ] **Step 1: Add a documentation smoke test for the quick-start commands**

```python
def test_readme_has_quick_start_and_account_id_commands():
    text = Path("README.md").read_text(encoding="utf-8")
    assert "## 快速开始" in text
    assert "--account account02" in text
    assert "scripts/initialize_account.py" in text
```

- [ ] **Step 2: Run the documentation test and confirm RED**

Run: `.\.venv\Scripts\pytest.exe tests/test_commands.py::test_readme_has_quick_start_and_account_id_commands -q`
Expected: FAIL until the quick-start section is added.

- [ ] **Step 3: Update both Markdown documents**

Add a Chinese `快速开始` section to `README.md` with the exact `account02` examples for login test, diagnosis, preview, debug submission, real submission, and end-time capture; explain that `--account` uses `accounts.json.id`, only enabled accounts load, and multi-account commands require the flag. Document initialization, default/custom windows, scheduler readiness, seat preference modes, WeCom notifications, and the no-booking guarantee. Update `产品期望.md` with the same v0.8 behavior and remaining risks without staging that file.

- [ ] **Step 4: Run the full verification suite**

Run: `.\.venv\Scripts\pytest.exe -q`
Run: `.\.venv\Scripts\python.exe -m compileall -q scripts seat_assistant`
Run: `.\.venv\Scripts\python.exe scripts\initialize_account.py --help`
Expected: all tests pass, compile succeeds, and help exits with code 0.

- [ ] **Step 5: Review the diff and commit the release**

```powershell
git status --short
git diff --check
git add README.md accounts.example.json seat_assistant scripts tests docs/superpowers/plans/2026-08-21-account-initialization-and-scheduled-reservation.md
git commit -m "发布 v0.8：完成账号初始化与定时预约通知"
git tag -a v0.8 -m "发布 v0.8：完成账号初始化与定时预约通知"
```

Do not stage `产品期望.md`, `.env`, `accounts.json`, browser profiles, databases, calibration captures, or unrelated pre-existing user changes.

## Plan Self-Review

- Default and custom learning windows are covered in Task 1 and Task 4.
- Interactive seat preferences and the no-booking guarantee are covered in Tasks 3, 4, and 6.
- Initialization state, capabilities, and last verification time are covered in Task 2.
- Scheduler readiness gating and WeCom notifications are covered in Task 5.
- Reuse of the existing real preview/login flow is covered in Task 3 and Task 6.
- README and local product notes are covered in Task 7, with the ignored product note explicitly excluded from staging.
- Every task includes focused failing tests, implementation, verification, and a commit.
