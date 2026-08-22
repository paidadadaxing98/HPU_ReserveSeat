import json
from pathlib import Path
from unittest.mock import patch

from seat_assistant.notifications import WeComNotifier, render_initialization, render_reservation, render_scheduler_summary, send_initialization_notification, send_reservation_notification
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


def test_render_reservation_marks_submitted_pending_verification_clearly():
    text = render_reservation(
        "2026-08-20",
        "手动",
        SeatResult(False, "4层计算机类借阅区", "169", "已提交，页面提示预约成功，但历史接口尚未出现匹配记录", conclusive=False),
        "09:00",
        "12:00",
    )

    assert text.splitlines()[0] == "2026-08-20 手动预约已提交，待核验"


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


def test_wecom_notifier_queues_message_when_network_fails(tmp_path):
    notifier = WeComNotifier("https://example.test/webhook", outbox_path=tmp_path / "outbox.jsonl")

    with patch("seat_assistant.notifications.urlopen", side_effect=OSError("network down")):
        assert notifier.send("预约成功") is False

    assert (tmp_path / "outbox.jsonl").read_text(encoding="utf-8").strip()


def test_wecom_notifier_flushes_queued_messages_before_sending_new_one(tmp_path):
    notifier = WeComNotifier("https://example.test/webhook", outbox_path=tmp_path / "outbox.jsonl")
    (tmp_path / "outbox.jsonl").write_text(
        json.dumps({"text": "旧消息", "queued_at": "2026-08-22T12:00:00"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"errcode": 0, "errmsg": "ok"}'

    with patch("seat_assistant.notifications.urlopen", return_value=Response()) as opened:
        assert notifier.send("新消息") is True

    payloads = [json.loads(call.args[0].data.decode("utf-8"))["text"]["content"] for call in opened.call_args_list]
    assert payloads == ["旧消息", "新消息"]
    assert not (tmp_path / "outbox.jsonl").exists()


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


def test_initialization_and_scheduler_notifications_identify_account():
    initialization = render_initialization("alice", {"status": "ready", "message": "验证成功"}, "张三")
    scheduler = render_scheduler_summary("alice", "2026-08-22", {"status": "skipped", "message": "请先初始化账号"}, "张三")

    assert "张三" in initialization
    assert "初始化" in initialization
    assert "张三" in scheduler
    assert "请先初始化账号" in scheduler


def test_render_scheduler_summary_places_optional_periods_after_evening():
    text = render_scheduler_summary(
        "alice",
        "2026-08-22",
        {
            "status": "completed",
            "morning": {"status": "pending", "message": "等待该时段触发"},
            "afternoon": {"status": "reserved", "message": "已预约"},
            "evening": {"status": "reserved", "message": "已预约"},
            "period04": {"status": "skipped", "message": "该学习时段未启用"},
            "period05": {"status": "skipped", "message": "该学习时段未启用"},
        },
        "张三",
    )

    lines = text.splitlines()
    assert lines.index("晚上：reserved，已预约") < lines.index("第4段：skipped，该学习时段未启用")
    assert lines.index("第4段：skipped，该学习时段未启用") < lines.index("第5段：skipped，该学习时段未启用")


def test_send_initialization_notification_isolates_notifier_exception():
    class BrokenNotifier:
        def send(self, text):
            raise OSError("network down")

    assert send_initialization_notification(BrokenNotifier(), "alice", {"status": "failed", "message": "接口失败"}) is False
