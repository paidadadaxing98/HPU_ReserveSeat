from pathlib import Path

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
