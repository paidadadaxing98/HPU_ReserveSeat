from types import SimpleNamespace

from scripts import run_wecom_bot
from seat_assistant.notifications import WeComNotifier


def test_bot_entrypoint_treats_missing_credentials_as_disabled(monkeypatch, capsys):
    monkeypatch.setattr(
        run_wecom_bot,
        "load_settings",
        lambda: SimpleNamespace(wecom_bot_id="", wecom_bot_secret=""),
    )

    assert run_wecom_bot.main([]) == 0
    assert "已禁用" in capsys.readouterr().out


def test_disabled_bot_does_not_queue_direct_delivery(tmp_path):
    notifier = WeComNotifier(
        webhook="",
        bot_outbox_dir=tmp_path / "outbox",
        bot_user_id="PatrickStar",
        bot_enabled=False,
    )

    assert notifier.send_template_card({"msgtype": "template_card"}) is False
    assert not (tmp_path / "outbox").exists()
