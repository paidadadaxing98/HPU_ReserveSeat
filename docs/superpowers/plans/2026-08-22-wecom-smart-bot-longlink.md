# 企业微信智能机器人长连接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local WeCom smart-bot long-connection runner that receives commands, deduplicates messages, routes push commands, and sends one-to-one tweet messages to mapped users.

**Architecture:** Add a dedicated bot runner with a small transport abstraction. The runner owns connection lifecycle, reconnect backoff, and single-instance locking. Business logic stays in focused helpers: command parsing, message dedupe, account/user resolution, and one-to-one send rendering. Existing reservation and webhook code stay intact.

**Tech Stack:** Python 3.11, standard library networking/concurrency, existing pytest setup, current `pydantic` config layer, optional WebSocket client adapter if the runtime already provides one.

---

### Task 1: Lock down bot command and routing behavior

**Files:**
- Modify: `seat_assistant/commands.py`
- Create: `tests/test_wecom_bot_commands.py`

- [ ] **Step 1: Write the failing test**

```python
from seat_assistant.commands import parse_command


def test_parse_push_command_with_target_account():
    command = parse_command("推文 account03 标题 | https://example.test/a")
    assert command.kind == "push_tweet"
    assert command.period is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wecom_bot_commands.py -q`

Expected: fail because `push_tweet` is not parsed yet.

- [ ] **Step 3: Write minimal implementation**

```python
if text.startswith("推文 "):
    payload = text.removeprefix("推文 ").strip()
    parts = [part.strip() for part in payload.split("|") if part.strip()]
    if len(parts) < 2:
        return Command("help")
    return Command("push_tweet", parts[0], "|".join(parts[1:]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wecom_bot_commands.py -q`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add seat_assistant/commands.py tests/test_wecom_bot_commands.py
git commit -m "feat: add wecom push tweet command"
```

### Task 2: Add bot transport, dedupe, and routing core

**Files:**
- Create: `seat_assistant/wecom_bot.py`
- Create: `tests/test_wecom_bot.py`

- [ ] **Step 1: Write the failing test**

```python
from seat_assistant.wecom_bot import MessageDeduplicator


def test_deduplicator_rejects_duplicate_message_id():
    dedupe = MessageDeduplicator(max_items=8)
    assert dedupe.seen("msg-1") is False
    assert dedupe.seen("msg-1") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wecom_bot.py::test_deduplicator_rejects_duplicate_message_id -q`

Expected: fail because the module does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
from collections import OrderedDict


class MessageDeduplicator:
    def __init__(self, max_items: int = 1024):
        self.max_items = max_items
        self._items = OrderedDict()

    def seen(self, message_id: str) -> bool:
        if message_id in self._items:
            return True
        self._items[message_id] = None
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wecom_bot.py::test_deduplicator_rejects_duplicate_message_id -q`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add seat_assistant/wecom_bot.py tests/test_wecom_bot.py
git commit -m "feat: add wecom bot core utilities"
```

### Task 3: Add one-to-one tweet sender and account resolution

**Files:**
- Modify: `seat_assistant/config.py`
- Modify: `seat_assistant/notifications.py`
- Create: `tests/test_wecom_bot_send.py`

- [ ] **Step 1: Write the failing test**

```python
from seat_assistant.notifications import render_tweet_push


def test_render_tweet_push_contains_target_and_link():
    text = render_tweet_push("account03", "用户A", "标题", "https://example.test/a")
    assert "account03" in text
    assert "用户A" in text
    assert "https://example.test/a" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wecom_bot_send.py -q`

Expected: fail because `render_tweet_push` is not defined.

- [ ] **Step 3: Write minimal implementation**

```python
def render_tweet_push(account_id: str, user_name: str, title: str, url: str) -> str:
    return "\n".join([
        f"账号：{account_id}",
        f"接收人：{user_name}",
        f"推文：{title}",
        f"链接：{url}",
    ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wecom_bot_send.py -q`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add seat_assistant/config.py seat_assistant/notifications.py tests/test_wecom_bot_send.py
git commit -m "feat: render one-to-one wecom tweet push"
```

### Task 4: Add long-connection runner and command loop

**Files:**
- Create: `scripts/run_wecom_bot.py`
- Create: `tests/test_run_wecom_bot.py`

- [ ] **Step 1: Write the failing test**

```python
from seat_assistant.wecom_bot import WeComBotRunner


def test_runner_stops_without_bot_credentials():
    runner = WeComBotRunner(bot_id="", secret="", transport_factory=None)
    assert runner.can_start() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_wecom_bot.py::test_runner_stops_without_bot_credentials -q`

Expected: fail because `WeComBotRunner` is not implemented yet.

- [ ] **Step 3: Write minimal implementation**

```python
class WeComBotRunner:
    def __init__(self, bot_id: str, secret: str, transport_factory):
        self.bot_id = bot_id
        self.secret = secret
        self.transport_factory = transport_factory

    def can_start(self) -> bool:
        return bool(self.bot_id and self.secret and self.transport_factory)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_run_wecom_bot.py::test_runner_stops_without_bot_credentials -q`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_wecom_bot.py tests/test_run_wecom_bot.py
git commit -m "feat: add wecom bot runner entrypoint"
```

### Task 5: Update docs and validate the whole tree

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-20-wecom-reservation-notifications-design.md`
- Modify: `docs/superpowers/specs/2026-08-21-account-initialization-and-scheduled-reservation-design.md`
- Modify: `docs/superpowers/specs/2026-08-21-sequential-reservation-tasks-design.md`
- Create or modify: any new help text referenced by the bot runner

- [ ] **Step 1: Update the user-facing docs**
- [ ] **Step 2: Run the focused tests**
- [ ] **Step 3: Run the full test suite**
- [ ] **Step 4: Run a build/compile check**
- [ ] **Step 5: Review `git status` and `git diff`**
- [ ] **Step 6: Commit the feature**

## Coverage check

This plan covers the spec requirements for long connection, reconnect/dedupe, routing, single-instance protection, one-to-one push, and docs alignment. The next implementation pass should keep the webhook notification path intact.
