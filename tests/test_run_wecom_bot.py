from types import SimpleNamespace

from scripts.run_wecom_bot import build_runner, parse_args
from seat_assistant.wecom_bot import OfficialSdkTransport
from seat_assistant.config import AccountSettings
from seat_assistant.wecom_bot import WeComBotMessage


def test_build_runner_uses_official_sdk_transport():
    settings = SimpleNamespace(
        wecom_bot_id="bot-id",
        wecom_bot_secret="bot-secret",
        wecom_bot_ws_url="wss://example.test",
        wecom_bot_default_user="",
    )

    runner = build_runner(settings=settings, accounts=[])

    assert runner.can_start() is True
    assert isinstance(runner.transport_factory(), OfficialSdkTransport)


def test_build_runner_does_not_connect_during_construction():
    settings = SimpleNamespace(
        wecom_bot_id="bot-id",
        wecom_bot_secret="bot-secret",
        wecom_bot_ws_url="wss://example.test",
        wecom_bot_default_user="",
    )

    runner = build_runner(settings=settings, accounts=[])

    assert runner.reconnect_delays == []


def test_build_runner_routes_control_command_to_account_database(tmp_path):
    settings = SimpleNamespace(
        wecom_bot_id="bot-id",
        wecom_bot_secret="bot-secret",
        wecom_bot_ws_url="wss://example.test",
        wecom_bot_default_user="",
        wecom_bot_outbox_dir=str(tmp_path / "outbox"),
    )
    accounts = [
        AccountSettings(
            id="account01",
            account="1001",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "seat.sqlite",
            wecom_user_id="user-a",
        )
    ]
    runner = build_runner(settings=settings, accounts=accounts)
    transport = type("Transport", (), {"reply": lambda self, message, text: True})()

    assert runner.handler(WeComBotMessage("msg-1", "req-1", "user-a", "今天不去了"), transport) is True

    from seat_assistant.storage import Repository
    stored = Repository(str(tmp_path / "seat.sqlite"), "account01").get_bot_command("req-1")
    assert stored["day"]
    assert stored["text"] == "今天不去了"


def test_build_runner_returns_local_status_without_dynamic_monitor(tmp_path):
    settings = SimpleNamespace(
        wecom_bot_id="bot-id",
        wecom_bot_secret="bot-secret",
        wecom_bot_ws_url="wss://example.test",
        wecom_bot_default_user="",
        wecom_bot_outbox_dir=str(tmp_path / "outbox"),
    )
    accounts = [
        AccountSettings(
            id="account01",
            account="1001",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "seat.sqlite",
            wecom_user_id="user-a",
        )
    ]
    from seat_assistant.storage import Repository
    repository = Repository(str(tmp_path / "seat.sqlite"), "account01")
    repository.save_reservation("2026-08-29", "morning", "reserved", "08:00", "12:00", "阅览室", "163")
    runner = build_runner(settings=settings, accounts=accounts)
    replies = []
    transport = type("Transport", (), {"reply": lambda self, message, text: replies.append(text) or True})()

    assert runner.handler(WeComBotMessage("msg-status", "req-status", "user-a", "状态"), transport) is True
    assert replies == ["当前状态（2026-08-29）：\n上午：已预约，08:00-12:00，阅览室，座位163。"]


def test_build_runner_writes_default_arrival_time_directly_to_account_database(tmp_path):
    settings = SimpleNamespace(
        wecom_bot_id="bot-id",
        wecom_bot_secret="bot-secret",
        wecom_bot_ws_url="wss://example.test",
        wecom_bot_default_user="",
        wecom_bot_outbox_dir=str(tmp_path / "outbox"),
    )
    accounts = [
        AccountSettings(
            id="account01",
            account="1001",
            password="secret",
            profile_path=tmp_path / "profile",
            db_path=tmp_path / "seat.sqlite",
            wecom_user_id="user-a",
        )
    ]
    runner = build_runner(settings=settings, accounts=accounts)
    replies = []
    transport = type("Transport", (), {"reply": lambda self, message, text: replies.append(text) or True})()

    for message_id, request_id, text in (
        ("msg-default-morning", "req-default-morning", "以后上午默认到馆时间为 09:05"),
        ("msg-default-afternoon", "req-default-afternoon", "以后下午默认到馆时间为 14:05"),
        ("msg-default-evening", "req-default-evening", "以后晚上默认到馆时间为 19:05"),
    ):
        assert runner.handler(
            WeComBotMessage(message_id, request_id, "user-a", text),
            transport,
        ) is True

    from seat_assistant.storage import Repository
    repository = Repository(str(tmp_path / "seat.sqlite"), "account01")
    assert repository.default_override("morning") == "09:05"
    assert repository.default_override("afternoon") == "14:05"
    assert repository.default_override("evening") == "19:05"
    assert repository.events("default_override", "morning") == ["09:05"]
    assert repository.events("default_override", "afternoon") == ["14:05"]
    assert repository.events("default_override", "evening") == ["19:05"]
    assert replies == [
        "已将上午默认到馆时间改为 09:05，已写入本地数据库。",
        "已将下午默认到馆时间改为 14:05，已写入本地数据库。",
        "已将晚上默认到馆时间改为 19:05，已写入本地数据库。",
    ]


def test_run_wecom_bot_accepts_a_bounded_runtime():
    args = parse_args(["--run-for-minutes", "45"])

    assert args.run_for_minutes == 45
