# 企业微信预约结果通知 Implementation Plan

> **For agentic workers:** Execute the small tasks below in order with test-first verification.

**Goal:** Connect reservation outcomes to an optional enterprise WeCom webhook without changing booking semantics.

**Architecture:** `AssistantService` receives an injectable notification sink. It saves the reservation outcome first, then sends a rendered text message. Configuration creates a `WeComNotifier` from `SEAT_WECOM_WEBHOOK`; an empty webhook becomes a no-op notifier.

**Tech Stack:** Python 3.11, `urllib.request`, pytest, existing SQLite repository and service layer.

---

### Task 1: Configuration and notifier contract

**Files:**
- Modify: `seat_assistant/config.py`
- Modify: `seat_assistant/notifications.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`, `tests/test_notifications.py`

- [x] Write tests for loading `SEAT_WECOM_WEBHOOK`, disabled sending with an empty URL, and WeCom JSON payload rendering.
- [x] Run the focused tests and verify the new assertions fail.
- [x] Add `wecom_webhook` to `Settings`, load it from the environment, and keep `WeComNotifier.send()` returning `False` when disabled.
- [x] Run focused tests and verify they pass.

### Task 2: Notify reservation outcomes

**Files:**
- Modify: `seat_assistant/service.py`
- Modify: `seat_assistant/main.py`
- Test: `tests/test_service.py`, `tests/test_local_flow.py`

- [x] Add an in-memory notifier test double and test success, failure, and uncertain reservation messages.
- [x] Run the service tests and verify they fail before integration.
- [x] Inject an optional notifier into `AssistantService`; save the result before sending and isolate notification exceptions.
- [x] Build the service with `WeComNotifier(settings.wecom_webhook)`.
- [x] Run the complete test suite, compile check, and diff check.

### Task 3: Documentation and local verification

**Files:**
- Modify: `README.md`

- [x] Document `SEAT_WECOM_WEBHOOK`, webhook rotation, and a local dry-run verification command.
- [x] Run the complete suite and verify no secret values are present in tracked files.
