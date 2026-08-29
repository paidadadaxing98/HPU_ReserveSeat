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


def test_run_wecom_bot_accepts_a_bounded_runtime():
    args = parse_args(["--run-for-minutes", "45"])

    assert args.run_for_minutes == 45
