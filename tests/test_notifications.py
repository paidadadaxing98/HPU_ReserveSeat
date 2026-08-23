import json
from pathlib import Path
from unittest.mock import patch

from seat_assistant.notifications import WeComNotifier, render_initialization, render_reservation, render_scheduler_summary, reservation_card, send_initialization_notification, send_reservation_notification
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
        "张三",
    )

    assert "| 🔑 **账号** | `张三` |" in text
    assert "2026-08-21" in text
    assert "**09:00 — 12:00**" in text
    assert "四层阅览室" in text
    assert "| 💺 **座位** | **169** |" in text
    assert "签到" not in text


def test_render_reservation_uses_markdown_confirmation_table():
    text = render_reservation(
        "2026-08-23",
        "afternoon",
        SeatResult(True, "7层社会科学类借阅区1", "169", "网页历史记录已验证"),
        "15:30",
        "19:00",
        "PatrickStar",
    )

    assert text.startswith("## 📌 预约成功确认")
    assert "| 🔑 **账号** | `PatrickStar` |" in text
    assert "| 📅 **日期** | **2026-08-23** (周日) |" in text
    assert "| ⏰ **时段** | **15:30 — 19:00** |" in text
    assert "| 🏛️ **阅览室** | **7层社会科学类借阅区1** |" in text
    assert "| 💺 **座位** | **169** |" in text
    assert "| ✅ **状态** | 已确认（网页历史记录已验证） |" in text


def test_reservation_card_contains_seat_and_required_template_card_fields():
    payload = reservation_card(
        "2026-08-23",
        "afternoon",
        SeatResult(True, "7层社会科学类借阅区1", "271", "网页历史记录已确认"),
        "15:30",
        "19:00",
        "PatrickStar",
    )

    card = payload["template_card"]
    assert payload["msgtype"] == "template_card"
    assert card["card_type"] == "text_notice"
    assert card["main_title"]["title"] == "预约成功确认"
    assert "emphasis_content" not in card
    assert {item["keyname"]: item["value"] for item in card["horizontal_content_list"]}["时段"] == "15:30 — 19:00"
    assert {item["keyname"]: item["value"] for item in card["horizontal_content_list"]}["座位"] == "271"
    assert card["card_action"] == {"type": 1, "url": "https://seatlib.hpu.edu.cn/libseat/#/login"}


def test_reservation_card_always_uses_the_booking_site_login_page():
    payload = reservation_card(
        "2026-08-23",
        "evening",
        SeatResult(True, "阅览室", "271", "已确认"),
        "20:00",
        "22:00",
        "PatrickStar",
        "https://login.example.test/seat",
    )

    assert payload["template_card"]["card_action"] == {
        "type": 1,
        "url": "https://seatlib.hpu.edu.cn/libseat/#/login",
    }


def test_render_reservation_marks_submitted_pending_verification_clearly():
    text = render_reservation(
        "2026-08-20",
        "手动",
        SeatResult(False, "4层计算机类借阅区", "169", "已提交，页面提示预约成功，但历史接口尚未出现匹配记录", conclusive=False),
        "09:00",
        "12:00",
    )

    assert text.splitlines()[0] == "## 📌 预约提交确认"
    assert "已提交，待核验" in text


def test_wecom_notifier_posts_markdown_payload_and_accepts_success_response():
    notifier = WeComNotifier("https://example.test/webhook")

    with patch("seat_assistant.notifications.urlopen", return_value=FakeResponse()) as opened:
        assert notifier.send("预约成功") is True

    request = opened.call_args.args[0]
    assert request.full_url == "https://example.test/webhook"
    assert json.loads(request.data.decode("utf-8")) == {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "text_notice",
            "main_title": {"title": "座位助手通知"},
            "sub_title_text": "预约成功",
            "horizontal_content_list": [],
                "card_action": {"type": 1, "url": "https://seatlib.hpu.edu.cn/libseat/#/login"},
        },
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

    payloads = [json.loads(call.args[0].data.decode("utf-8")) for call in opened.call_args_list]
    assert [payload["msgtype"] for payload in payloads] == ["template_card", "template_card"]
    assert [payload["template_card"]["sub_title_text"] for payload in payloads] == ["旧消息", "新消息"]
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

    assert send_reservation_notification(notifier, "2026-08-20", "手动", result, "15:00", "17:00", "张三") is True
    assert len(notifier.messages) == 1
    assert notifier.messages[0].splitlines()[0] == "## 📌 预约成功确认"
    assert "| 🔑 **账号** | `张三` |" in notifier.messages[0]
    assert "**15:00 — 17:00**" in notifier.messages[0]


def test_send_reservation_notification_isolates_notifier_exception():
    class BrokenNotifier:
        def send(self, text):
            raise OSError("network down")

    result = SeatResult(False, message="提交结果不明确", conclusive=False)

    assert send_reservation_notification(BrokenNotifier(), "2026-08-20", "手动", result, "15:00", "17:00") is False


def test_initialization_and_scheduler_notifications_identify_account():
    initialization = render_initialization("alice", {"status": "ready", "message": "验证成功"}, "张三")
    scheduler = render_scheduler_summary("alice", "2026-08-22", {"status": "skipped", "message": "请先初始化账号"}, "张三")

    assert initialization.startswith("## 🔧 初始化验证结果")
    assert scheduler.startswith("## 📋 定时任务结果")
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
    evening_line = next(index for index, line in enumerate(lines) if "| 晚上 | reserved，已预约 |" in line)
    period04_line = next(index for index, line in enumerate(lines) if "| 第4段 | skipped，该学习时段未启用 |" in line)
    period05_line = next(index for index, line in enumerate(lines) if "| 第5段 | skipped，该学习时段未启用 |" in line)
    assert evening_line < period04_line < period05_line


def test_send_initialization_notification_isolates_notifier_exception():
    class BrokenNotifier:
        def send(self, text):
            raise OSError("network down")

    assert send_initialization_notification(BrokenNotifier(), "alice", {"status": "failed", "message": "接口失败"}) is False
