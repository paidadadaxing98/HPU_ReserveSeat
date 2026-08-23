from types import SimpleNamespace

from scripts.run_wecom_bot import build_runner, parse_args
from seat_assistant.wecom_bot import OfficialSdkTransport


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


def test_run_wecom_bot_accepts_a_bounded_runtime():
    args = parse_args(["--run-for-minutes", "45"])

    assert args.run_for_minutes == 45
