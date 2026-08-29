from pathlib import Path
import threading
import time

import pytest

from seat_assistant.commands import Command
from seat_assistant.config import AccountSettings
from seat_assistant.wecom_bot import (
    AccountRecipientResolver,
    MessageDeduplicator,
    SingleInstanceLock,
    WeComBotMessage,
    WeComBotRunner,
    WeComCommandRouter,
)


def test_deduplicator_rejects_duplicate_message_id():
    dedupe = MessageDeduplicator(max_items=8)

    assert dedupe.seen("msg-1") is False
    assert dedupe.seen("msg-1") is True


def test_deduplicator_expires_oldest_message_id():
    dedupe = MessageDeduplicator(max_items=2)

    assert dedupe.seen("msg-1") is False
    assert dedupe.seen("msg-2") is False
    assert dedupe.seen("msg-3") is False
    assert dedupe.seen("msg-1") is False


def test_runner_stops_without_bot_credentials():
    runner = WeComBotRunner(bot_id="", secret="", transport_factory=None)

    assert runner.can_start() is False


def test_single_instance_lock_rejects_second_holder(tmp_path):
    lock_path = tmp_path / "wecom-bot.lock"
    first = SingleInstanceLock(lock_path)
    second = SingleInstanceLock(lock_path)

    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()


def test_account_resolver_finds_account_by_alias_and_user_id(tmp_path):
    accounts = [
        AccountSettings(
            id="account03",
            account="1003",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "db.sqlite",
            wecom_user_id="user-a",
            wecom_aliases=("张三", "zs"),
        )
    ]
    resolver = AccountRecipientResolver(accounts)

    recipient = resolver.resolve("张三")

    assert recipient.account_id == "account03"
    assert recipient.user_id == "user-a"


def test_router_authorized_control_command_is_queued_for_account(tmp_path):
    accounts = [
        AccountSettings(
            id="account01",
            account="1001",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "db.sqlite",
            wecom_user_id="user-a",
        )
    ]
    queued = []
    replies = []
    router = WeComCommandRouter(
        AccountRecipientResolver(accounts),
        send_to_user=lambda user_id, text: True,
        reply=lambda message, text: replies.append(text) or True,
        command_submitter=lambda account_id, request_id, sender, text: queued.append(
            (account_id, request_id, sender, text)
        ) or True,
    )

    message = WeComBotMessage("msg-1", "req-1", "user-a", "今天不去了")

    assert router.handle(message) is True
    assert queued == [("account01", "req-1", "user-a", "今天不去了")]
    assert replies == ["已收到命令：今天不去了。已写入本地数据库，等待动态监控受理。"]


@pytest.mark.parametrize("text", [
    "取消上午",
    "取消下午",
    "取消晚上",
    "上午推迟到 09:20",
    "下午推迟到 15:20",
])
def test_router_queues_period_control_commands_for_dynamic_monitor(tmp_path, text):
    accounts = [
        AccountSettings(
            id="account01",
            account="1001",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "db.sqlite",
            wecom_user_id="user-a",
        )
    ]
    queued = []
    router = WeComCommandRouter(
        AccountRecipientResolver(accounts),
        send_to_user=lambda user_id, text: True,
        reply=lambda message, text: True,
        command_submitter=lambda account_id, request_id, sender, command_text: queued.append(
            (account_id, request_id, sender, command_text)
        ) or True,
    )

    assert router.handle(WeComBotMessage("msg-control", "req-control", "user-a", text)) is True
    assert queued == [("account01", "req-control", "user-a", text)]


def test_router_reports_duplicate_control_command_is_already_in_database(tmp_path):
    accounts = [
        AccountSettings(
            id="account01",
            account="1001",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "db.sqlite",
            wecom_user_id="user-a",
        )
    ]
    replies = []
    router = WeComCommandRouter(
        AccountRecipientResolver(accounts),
        send_to_user=lambda user_id, text: True,
        reply=lambda message, text: replies.append(text) or True,
        command_submitter=lambda *args: False,
    )

    assert router.handle(WeComBotMessage("msg-duplicate", "req-duplicate", "user-a", "今天不去了")) is True
    assert replies == ["该命令已收到，数据库中已有记录，未重复执行。"]


def test_router_returns_status_directly_without_queueing_command(tmp_path):
    accounts = [
        AccountSettings(
            id="account01",
            account="1001",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "db.sqlite",
            wecom_user_id="user-a",
        )
    ]
    replies = []
    router = WeComCommandRouter(
        AccountRecipientResolver(accounts),
        send_to_user=lambda user_id, text: True,
        reply=lambda message, text: replies.append(text) or True,
        command_submitter=lambda *args: (_ for _ in ()).throw(AssertionError("状态不应进入命令队列")),
        status_reader=lambda account_id: "状态（2026-08-29）：上午已预约 08:00-12:00。",
    )

    assert router.handle(WeComBotMessage("msg-status", "req-status", "user-a", "状态")) is True
    assert replies == ["状态（2026-08-29）：上午已预约 08:00-12:00。"]


def test_router_returns_help_directly_without_queueing_command(tmp_path):
    accounts = [
        AccountSettings(
            id="account01",
            account="1001",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "db.sqlite",
            wecom_user_id="user-a",
        )
    ]
    replies = []
    router = WeComCommandRouter(
        AccountRecipientResolver(accounts),
        send_to_user=lambda user_id, text: True,
        reply=lambda message, text: replies.append(text) or True,
        command_submitter=lambda *args: (_ for _ in ()).throw(AssertionError("帮助不应进入命令队列")),
    )

    assert router.handle(WeComBotMessage("msg-help", "req-help", "unknown-user", "帮助")) is True
    assert "帮助" in replies[0]
    assert "状态" in replies[0]
    assert "今天不去了" in replies[0]
    assert "下午默认到馆时间" in replies[0]
    assert "晚上默认到馆时间" in replies[0]


def test_router_rejects_control_command_from_unconfigured_sender(tmp_path):
    accounts = [
        AccountSettings(
            id="account01",
            account="1001",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "db.sqlite",
            wecom_user_id="user-a",
        )
    ]
    queued = []
    replies = []
    router = WeComCommandRouter(
        AccountRecipientResolver(accounts),
        send_to_user=lambda user_id, text: True,
        reply=lambda message, text: replies.append(text) or True,
        command_submitter=lambda *args: queued.append(args) or True,
    )

    assert router.handle(WeComBotMessage("msg-2", "req-2", "user-b", "今天不去了")) is False
    assert queued == []
    assert replies == ["没有权限执行座位控制命令。"]


def test_router_sends_push_tweet_to_resolved_user(tmp_path):
    accounts = [
        AccountSettings(
            id="account03",
            account="1003",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "db.sqlite",
            wecom_user_id="user-a",
            wecom_aliases=("老三",),
        )
    ]
    sent = []
    replies = []
    router = WeComCommandRouter(
        AccountRecipientResolver(accounts),
        send_to_user=lambda user_id, text: sent.append((user_id, text)) or True,
        reply=lambda message, text: replies.append((message.message_id, text)),
    )
    message = WeComBotMessage(message_id="msg-1", request_id="req-1", sender="sender", text="推文 account03 标题 | https://example.test/a")

    assert router.handle(message) is True

    assert sent == [("user-a", "账号：老三\n接收人：user-a\n推文：标题\n链接：https://example.test/a")]
    assert replies == [("msg-1", "已发送给 user-a")]


def test_router_replies_with_tweet_to_sender_response_url(tmp_path):
    accounts = [
        AccountSettings(
            id="account01",
            account="1001",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "db.sqlite",
            wecom_user_id="user-a",
            wecom_aliases=("老大",),
        )
    ]
    sent = []
    replies = []
    router = WeComCommandRouter(
        AccountRecipientResolver(accounts),
        send_to_user=lambda user_id, text: sent.append((user_id, text)) or True,
        reply=lambda message, text: replies.append((message.message_id, text)) or True,
    )
    message = WeComBotMessage(
        "msg-2",
        "req-2",
        "user-a",
        "推文 老大 标题 | https://example.test/a",
        response_url="https://example.test/reply",
    )

    assert router.handle(message) is True

    assert sent == []
    assert replies == [
        ("msg-2", "账号：老大\n接收人：user-a\n推文：标题\n链接：https://example.test/a")
    ]


def test_router_sends_one_template_card_when_target_is_sender(tmp_path):
    accounts = [
        AccountSettings(
            id="account01",
            account="1001",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "db.sqlite",
            wecom_user_id="user-a",
            wecom_aliases=("老大",),
        )
    ]
    cards = []
    replies = []

    class Transport:
        def send_to_user(self, user_id, text):
            raise AssertionError("不应发送第二条主动文本消息")

        def send_template_card(self, user_id, card):
            cards.append((user_id, card))
            return True

        def reply_template_card(self, message, card):
            replies.append((message.message_id, card))
            return True

        def reply(self, message, text):
            replies.append((message.message_id, text))
            return True

    router = WeComCommandRouter(AccountRecipientResolver(accounts), None, None)
    message = WeComBotMessage(
        "msg-3", "req-3", "user-a", "推文 老大 标题 | https://example.test/a",
        response_url="https://example.test/reply",
    )

    assert router.handle(message, Transport()) is True
    assert cards == []
    assert len(replies) == 1
    assert replies[0][0] == "msg-3"
    assert replies[0][1]["template_card"]["card_type"] == "text_notice"


def test_router_replies_when_push_target_is_unknown(tmp_path):
    router = WeComCommandRouter(
        AccountRecipientResolver([]),
        send_to_user=lambda user_id, text: True,
        reply=lambda message, text: replies.append(text),
    )
    replies = []
    message = WeComBotMessage(message_id="msg-1", request_id="req-1", sender="sender", text="推文 missing 标题 | https://example.test/a")

    assert router.handle(message) is False

    assert replies == ["未找到推文接收人：missing"]


def test_runner_skips_duplicate_messages_and_continues_after_disconnect():
    class DisconnectingTransport:
        def __init__(self):
            self.calls = 0

        def connect(self, bot_id, secret):
            self.calls += 1
            if self.calls == 1:
                self.messages = [
                    WeComBotMessage("msg-1", "req-1", "sender", "状态"),
                    WeComBotMessage("msg-1", "req-1", "sender", "状态"),
                ]
            else:
                self.messages = [WeComBotMessage("msg-2", "req-2", "sender", "状态")]

        def iter_messages(self):
            return iter(self.messages)

    handled = []
    transport = DisconnectingTransport()
    runner = WeComBotRunner(
        bot_id="bot",
        secret="secret",
        transport_factory=lambda: transport,
        handler=lambda message: handled.append(message.message_id),
        sleep=lambda seconds: None,
        max_reconnect_delay=4,
    )

    runner.run(max_cycles=2)

    assert handled == ["msg-1", "msg-2"]
    assert runner.reconnect_delays == [1.0]


def test_runner_stop_interrupts_a_blocking_transport():
    class BlockingTransport:
        def __init__(self):
            self.interrupted = threading.Event()

        def connect(self, bot_id, secret):
            pass

        def iter_messages(self):
            while not self.interrupted.is_set():
                time.sleep(0.01)
            return
            yield

        def interrupt(self):
            self.interrupted.set()

    transport = BlockingTransport()
    runner = WeComBotRunner(
        bot_id="bot",
        secret="secret",
        transport_factory=lambda: transport,
        sleep=lambda seconds: None,
    )
    worker = threading.Thread(target=runner.run)

    worker.start()
    time.sleep(0.03)
    runner.stop()
    worker.join(timeout=1)

    assert transport.interrupted.is_set()
    assert not worker.is_alive()
