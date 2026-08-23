# 企业微信官方 SDK 适配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-written WeCom WebSocket transport with the official `wecom-aibot-sdk` while preserving existing command parsing, account routing, deduplication, and one-to-one sending.

**Architecture:** Keep `WeComBotRunner`, `WeComCommandRouter`, `AccountRecipientResolver`, and the internal `WeComBotMessage` contract. Add an SDK adapter that owns an async `WSClient`, converts SDK callback frames to internal messages, and exposes synchronous-compatible send/reply methods to the existing router through an async bridge. The SDK owns authentication, heartbeat, and reconnect behavior; the project retains single-instance protection and message deduplication.

**Tech Stack:** Python 3.11+, `wecom-aibot-sdk==1.0.8`, `websockets`, `httpx`, `pytest`, existing project configuration.

---

### Task 1: Pin and document the official SDK dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_wecom_sdk_dependency.py`

- [ ] **Step 1: Write the failing dependency contract test**

```python
def test_official_wecom_sdk_is_importable():
    from wecom_aibot_sdk import WSClient

    assert WSClient is not None
```

- [ ] **Step 2: Run the focused test**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_wecom_sdk_dependency.py -q`
Expected: PASS after the package is installed; fail in a clean environment until the project dependency is declared.

- [ ] **Step 3: Declare the exact SDK dependency**

Add `wecom-aibot-sdk>=1.0.8,<2` to the project dependencies. Keep the already-required lower-level packages because the project and SDK import them directly.

- [ ] **Step 4: Add configuration and usage documentation**

Document `SEAT_WECOM_BOT_ID`, `SEAT_WECOM_BOT_SECRET`, account `wecom_user_id`, and the command format. State that credentials are required only for live mode and that the test path uses fakes.

- [ ] **Step 5: Run the focused and configuration tests**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_wecom_sdk_dependency.py tests/test_config.py -q`
Expected: PASS.

### Task 2: Define the SDK-to-project message adapter contract

**Files:**
- Modify: `seat_assistant/wecom_bot.py`
- Create: `tests/test_wecom_official_sdk_adapter.py`

- [ ] **Step 1: Write failing frame-conversion tests**

```python
def test_sdk_text_frame_maps_to_internal_message():
    frame = {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-1"},
        "body": {
            "msgid": "msg-1",
            "msgtype": "text",
            "from": {"userid": "sender-a"},
            "chatid": "sender-a",
            "chattype": "single",
            "text": {"content": "推文 account01 标题 | https://example.test/a"},
        },
    }
    message = sdk_frame_to_message(frame)
    assert message.message_id == "msg-1"
    assert message.sender == "sender-a"
    assert message.text.startswith("推文 account01")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_wecom_official_sdk_adapter.py -q`
Expected: FAIL because the adapter conversion function does not exist.

- [ ] **Step 3: Implement conversion and explicit message filtering**

Convert only `aibot_msg_callback` frames with `body.msgtype == "text"`; derive the message ID from `msgid` or `msg_id`, and use the request ID only as a last-resort stable identifier. Ignore malformed frames without raising into the SDK event loop.

- [ ] **Step 4: Add tests for malformed and non-text frames**

Assert they return `None` and do not invoke the business handler.

- [ ] **Step 5: Run focused tests**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_wecom_official_sdk_adapter.py -q`
Expected: PASS.

### Task 3: Implement asynchronous official SDK transport

**Files:**
- Modify: `seat_assistant/wecom_bot.py`
- Modify: `tests/test_wecom_bot_send.py`
- Modify: `tests/test_wecom_bot.py`

- [ ] **Step 1: Write failing transport tests with a fake SDK client**

```python
async def test_sdk_transport_sends_markdown_to_user():
    client = FakeSdkClient()
    transport = OfficialSdkTransport(client)
    assert transport.send_to_user("user-a", "内容") is True
    assert client.sent == [("user-a", {"msgtype": "markdown", "markdown": {"content": "内容"}})]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_wecom_bot_send.py -q`
Expected: FAIL because `OfficialSdkTransport` does not exist.

- [ ] **Step 3: Implement the adapter**

`OfficialSdkTransport` must:

```python
send_to_user(user_id, text) -> bool
reply(message, text) -> bool
start() -> None
stop() -> None
```

Use a dedicated event-loop thread so the existing synchronous router remains unchanged. `send_to_user` calls `WSClient.send_message(user_id, {"msgtype": "markdown", "markdown": {"content": text}})`. `reply` calls `WSClient.reply(frame, {"msgtype": "text", "text": {"content": text}})` using the original SDK frame retained by the internal message adapter. Convert SDK exceptions to `False` and log them.

- [ ] **Step 4: Test current-message reply and failure behavior**

Assert reply uses the original request frame and that SDK exceptions return `False` without stopping the runner.

- [ ] **Step 5: Run bot-focused tests**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_wecom_bot.py tests/test_wecom_bot_send.py tests/test_wecom_official_sdk_adapter.py -q`
Expected: PASS.

### Task 4: Wire the official SDK client into the service entrypoint

**Files:**
- Modify: `scripts/run_wecom_bot.py`
- Modify: `seat_assistant/wecom_bot.py`
- Create: `tests/test_run_wecom_bot.py`

- [ ] **Step 1: Write failing construction tests**

Assert `build_runner()` constructs `WSClient` with bot credentials and returns a runner that can start, while missing credentials still exits before network connection.

- [ ] **Step 2: Implement SDK-backed runner lifecycle**

Register `message.text` once, keep the existing deduplicator and router, call `OfficialSdkTransport.start()` from `run`, and call `stop()` in `finally`. Do not retain the old manual `aibot_subscribe`, `ping`, or reconnect loops in the production path.

- [ ] **Step 3: Preserve single-instance and scheduled-task behavior**

Keep the existing lock file and `scripts.run_scheduled_task` subprocess invocation unchanged. A missing SDK or missing bot credentials must affect only the bot process and must not interrupt reservations.

- [ ] **Step 4: Run entrypoint tests**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_run_wecom_bot.py tests/test_scheduled_task.py -q`
Expected: PASS.

### Task 5: Verification and operational documentation

**Files:**
- Modify: `README.md`
- Modify: `.env.example`

- [ ] **Step 1: Add dry-run verification instructions**

Document import verification, fake-client tests, configuration checks, and the live command. Explicitly state that live verification requires a real Bot ID/Secret and a mapped `wecom_user_id`; do not print secrets.

- [ ] **Step 2: Run syntax and focused tests**

Run: `\.venv\Scripts\python.exe -m compileall seat_assistant scripts`
Run: `\.venv\Scripts\python.exe -m pytest tests/test_wecom_bot.py tests/test_wecom_bot_send.py tests/test_wecom_official_sdk_adapter.py tests/test_run_wecom_bot.py -q`
Expected: compile succeeds and focused tests pass.

- [ ] **Step 3: Run the full suite**

Run: `\.venv\Scripts\python.exe -m pytest -q`
Expected: all existing and new tests pass.

- [ ] **Step 4: Review changes**

Run: `git diff --check` and `git status --short`. Confirm `.env`, `accounts.json`, secrets, and webhook URLs are not included in changed files.
