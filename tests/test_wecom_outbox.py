import json

from seat_assistant.notifications import WeComNotifier


def test_notifier_writes_bot_delivery_file_for_account(tmp_path):
    notifier = WeComNotifier(
        webhook="",
        bot_outbox_dir=tmp_path / "outbox",
        bot_user_id="PatrickStar",
    )
    card = {
        "msgtype": "template_card",
        "template_card": {"card_type": "text_notice"},
    }

    assert notifier.send_template_card(card) is True
    files = list((tmp_path / "outbox").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["user_id"] == "PatrickStar"
    assert payload["payload"] == card


def test_notifier_does_not_write_bot_delivery_file_without_user_id(tmp_path):
    notifier = WeComNotifier(webhook="", bot_outbox_dir=tmp_path / "outbox")

    assert notifier.send_template_card({"msgtype": "template_card"}) is False
    assert not (tmp_path / "outbox").exists()
