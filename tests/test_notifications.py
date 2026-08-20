import json
from unittest.mock import patch

from seat_assistant.notifications import WeComNotifier, render_reservation, send_reservation_notification
from seat_assistant.reservation import SeatResult


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b'{"errcode": 0, "errmsg": "ok"}'


def test_render_reservation_includes_booking_details_and_checkin_rule():
    text = render_reservation(
        "2026-08-21",
        "morning",
        SeatResult(True, "四层阅览室", "169", "ok"),
        "09:00",
        "12:00",
    )

    assert "2026-08-21" in text
    assert "上午" in text
    assert "09:00 - 12:00" in text
    assert "四层阅览室" in text
    assert "169" in text
    assert "签到" in text


def test_wecom_notifier_posts_text_payload_and_accepts_success_response():
    notifier = WeComNotifier("https://example.test/webhook")

    with patch("seat_assistant.notifications.urlopen", return_value=FakeResponse()) as opened:
        assert notifier.send("预约成功") is True

    request = opened.call_args.args[0]
    assert request.full_url == "https://example.test/webhook"
    assert json.loads(request.data.decode("utf-8")) == {
        "msgtype": "text",
        "text": {"content": "预约成功"},
    }


def test_wecom_notifier_rejects_api_error_even_when_http_succeeds():
    class ErrorResponse(FakeResponse):
        def read(self):
            return b'{"errcode": 93000, "errmsg": "invalid webhook"}'

    notifier = WeComNotifier("https://example.test/webhook")

    with patch("seat_assistant.notifications.urlopen", return_value=ErrorResponse()):
        assert notifier.send("预约成功") is False


def test_send_reservation_notification_renders_and_sends_manual_booking():
    class RecordingNotifier:
        def __init__(self):
            self.messages = []

        def send(self, text):
            self.messages.append(text)
            return True

    notifier = RecordingNotifier()
    result = SeatResult(True, "4层计算机类借阅区", "169", "网页核验成功")

    assert send_reservation_notification(notifier, "2026-08-20", "手动", result, "15:00", "17:00") is True
    assert len(notifier.messages) == 1
    assert "手动预约成功" in notifier.messages[0]
    assert "15:00 - 17:00" in notifier.messages[0]


def test_send_reservation_notification_isolates_notifier_exception():
    class BrokenNotifier:
        def send(self, text):
            raise OSError("network down")

    result = SeatResult(False, message="提交结果不明确", conclusive=False)

    assert send_reservation_notification(BrokenNotifier(), "2026-08-20", "手动", result, "15:00", "17:00") is False
