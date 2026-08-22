import asyncio
from types import SimpleNamespace

from seat_assistant.reservation import SeatResult
from scripts.preview_reservation import close_success_dialog, close_time_dialog, daily_reservation_details, fetch_post_submit_reservations, fetch_user_reservations, fetch_user_reservations_with_capabilities, pause_for_manual_interaction, reservation_summary, reservation_verification_delay, reservation_verification_status, send_preview_notification, submission_notice


def test_preview_notification_uses_manual_booking_context():
    class RecordingNotifier:
        def __init__(self):
            self.messages = []

        def send(self, text):
            self.messages.append(text)
            return True

    notifier = RecordingNotifier()
    args = SimpleNamespace(date="2026-08-20", start="15:00", end="17:00")
    args.account_label = "张三"
    result = SeatResult(True, "4层计算机类借阅区", "169", "网页核验成功")

    assert send_preview_notification(notifier, args, result) is True
    assert notifier.messages[0].splitlines()[0] == "账号：张三"
    assert "手动预约成功" in notifier.messages[0]
    assert "阅览室" in notifier.messages[0]
    assert "时间：" in notifier.messages[0]
    assert "说明：" in notifier.messages[0]
    assert "169" not in notifier.messages[0]


def test_reservation_summary_reads_live_api_location_and_time_fields():
    summary = reservation_summary({
        "location": "南校区第二图书馆4层4层计算机类 借阅区，座位号169",
        "begin": "15:00",
        "end": "17:00",
    })

    assert summary == "南校区第二图书馆4层4层计算机类 借阅区，座位 169，15:00-17:00"


def test_fetch_user_reservations_reads_paginated_history_endpoint():
    first_page = [{"id": index} for index in range(100)]
    second_page = [{"id": 100}]

    class Page:
        def __init__(self):
            self.endpoints = []

        async def evaluate(self, script, payload):
            self.endpoints.append(payload["endpoint"])
            if "/history/1/100?" in payload["endpoint"]:
                return {"status": 200, "body": {"code": 0, "data": {"reservations": first_page, "count": "101"}}}
            if "/history/2/100?" in payload["endpoint"]:
                return {"status": 200, "body": {"code": 0, "data": {"reservations": second_page, "count": "101"}}}
            if "/user/reservations?" in payload["endpoint"]:
                return {"status": 200, "body": {"code": 0, "data": []}}
            raise AssertionError(f"unexpected endpoint: {payload['endpoint']}")

        def is_closed(self):
            return False

    page = Page()
    reservations = asyncio.run(fetch_user_reservations(page, {"headers": {"authorization": "x"}, "token": "token"}))

    assert len(reservations) == 101
    assert page.endpoints == [
        "/rest/v2/history/1/100?token=token",
        "/rest/v2/history/2/100?token=token",
        "/rest/v2/user/reservations?token=token",
    ]


def test_fetch_user_reservations_unwraps_nested_history_payload():
    record = {
        "date": "2026-08-20",
        "begin": "20:00",
        "end": "21:00",
        "loc": "4层计算机类借阅区，座位号169",
        "stat": "RESERVE",
    }

    class Page:
        def __init__(self):
            self.endpoints = []

        async def evaluate(self, script, payload):
            self.endpoints.append(payload["endpoint"])
            return {"status": 200, "body": {"code": 0, "data": {"data": {"records": [record]}, "totalCount": 1}}}

        def is_closed(self):
            return False

    page = Page()
    assert asyncio.run(fetch_user_reservations(page, {"headers": {"authorization": "x"}, "token": "token"})) == [record]


def test_fetch_user_reservations_uses_total_count_even_when_page_is_short():
    records = [
        {"date": "2026-08-20", "begin": "20:00", "end": "21:00", "stat": "RESERVE"},
        {"date": "2026-08-20", "begin": "15:00", "end": "17:00", "stat": "CANCEL"},
    ]

    class Page:
        def __init__(self):
            self.endpoints = []

        async def evaluate(self, script, payload):
            self.endpoints.append(payload["endpoint"])
            if "/user/reservations?" in payload["endpoint"]:
                return {"status": 200, "body": {"code": 0, "data": []}}
            page_number = payload["endpoint"].split("/history/", 1)[1].split("/", 1)[0]
            index = int(page_number) - 1
            return {"status": 200, "body": {"code": 0, "data": {"records": [records[index]], "totalCount": 2}}}

        def is_closed(self):
            return False

    page = Page()
    assert asyncio.run(fetch_user_reservations(page, {"headers": {"authorization": "x"}, "token": "token"})) == records
    assert page.endpoints == [
        "/rest/v2/history/1/100?token=token",
        "/rest/v2/history/2/100?token=token",
        "/rest/v2/user/reservations?token=token",
    ]


def test_fetch_user_reservations_merges_current_reservation_fallback():
    cancelled = {"date": "2026-08-20", "begin": "15:00", "end": "17:00", "stat": "CANCEL"}
    reserved = {"date": "2026-08-20", "begin": "20:00", "end": "21:00", "stat": "RESERVE"}

    class Page:
        def __init__(self):
            self.endpoints = []

        async def evaluate(self, script, payload):
            endpoint = payload["endpoint"]
            self.endpoints.append(endpoint)
            if "/history/1/100?" in endpoint:
                return {"status": 200, "body": {"code": 0, "data": {"records": [cancelled], "totalCount": 1}}}
            if "/user/reservations?" in endpoint:
                return {"status": 200, "body": {"code": 0, "data": [reserved]}}
            raise AssertionError(f"unexpected endpoint: {endpoint}")

        def is_closed(self):
            return False

    page = Page()
    assert asyncio.run(fetch_user_reservations(page, {"headers": {"authorization": "x"}, "token": "token"})) == [cancelled, reserved]
    assert any("/user/reservations?" in endpoint for endpoint in page.endpoints)


def test_fetch_user_reservations_falls_back_to_current_endpoint():
    record = {
        "date": "2026-08-20",
        "begin": "20:00",
        "end": "21:00",
        "loc": "4层计算机类借阅区，座位号169",
        "stat": "RESERVE",
    }

    class Page:
        def __init__(self):
            self.endpoints = []

        async def evaluate(self, script, payload):
            endpoint = payload["endpoint"]
            self.endpoints.append(endpoint)
            if "/history/" in endpoint:
                return {"status": 200, "body": {"code": 0, "data": []}}
            if endpoint == "/rest/v2/user/reservations?token=token":
                return {"status": 200, "body": {"code": 0, "data": [record]}}
            raise AssertionError(f"unexpected endpoint: {endpoint}")

        def is_closed(self):
            return False

    page = Page()
    assert asyncio.run(fetch_user_reservations(page, {"headers": {"authorization": "x"}, "token": "token"})) == [record]
    assert page.endpoints == [
        "/rest/v2/history/1/100?token=token",
        "/rest/v2/user/reservations?token=token",
    ]


def test_fetch_user_reservations_reports_each_interface_capability():
    record = {"date": "2026-08-20", "begin": "20:00", "end": "21:00", "stat": "RESERVE"}

    class Page:
        async def evaluate(self, script, payload):
            endpoint = payload["endpoint"]
            if "/history/" in endpoint:
                return {"status": 404, "body": {"code": 404, "message": "history unavailable"}}
            return {"status": 200, "body": {"code": 0, "data": [record]}}

        def is_closed(self):
            return False

    records, capabilities = asyncio.run(fetch_user_reservations_with_capabilities(
        Page(), {"headers": {"authorization": "x"}, "token": "token"}
    ))

    assert records == [record]
    assert capabilities == {
        "history": False,
        "current_reservations": True,
        "my_reservations": False,
    }


def test_unattended_reservation_never_waits_for_manual_input(monkeypatch):
    def fail_input(_):
        raise AssertionError("unattended flow must not call input")

    monkeypatch.setattr("builtins.input", fail_input)
    pause_for_manual_interaction("should not pause", interactive=False)


def test_scheduled_reservation_disables_interaction_and_script_quota_recording(monkeypatch, tmp_path):
    import scripts.preview_reservation as preview
    from seat_assistant.storage import Repository

    captured = []

    async def fake_main(args):
        captured.append(args)
        Repository(str(tmp_path / "account.sqlite"), args.account).save_reservation(
            args.date, args.reservation_key, "reserved", args.start, args.end, "阅览室", "169", "已核验"
        )

    monkeypatch.setattr(preview, "main", fake_main)
    settings = SimpleNamespace(account_id="alice", db_path=str(tmp_path / "account.sqlite"))

    result = asyncio.run(preview.run_scheduled_reservation(settings, "2026-08-21", "morning", "08:30", "12:00"))

    assert result.success is True
    assert captured[0].interactive is False
    assert captured[0].record_success_quota is False
    assert captured[0].reservation_key == "morning"
    assert Repository(str(tmp_path / "account.sqlite"), "alice").successful_booking_count("2026-08-21") == 0


def test_post_submit_poll_reads_only_current_reservations_endpoint():
    record = {"date": "2026-08-20", "begin": "09:00", "end": "12:00", "stat": "RESERVE"}

    class Page:
        def __init__(self):
            self.endpoints = []

        async def evaluate(self, script, payload):
            self.endpoints.append(payload["endpoint"])
            return {"status": 200, "body": {"code": 0, "data": [record]}}

    page = Page()
    assert asyncio.run(fetch_post_submit_reservations(page, {"headers": {"authorization": "x"}, "token": "token"})) == [record]
    assert page.endpoints == ["/rest/v2/user/reservations?token=token"]


def test_fetch_user_reservations_falls_back_when_history_endpoint_fails():
    record = {"date": "2026-08-20", "begin": "20:00", "end": "21:00", "stat": "RESERVE"}

    class Page:
        async def evaluate(self, script, payload):
            endpoint = payload["endpoint"]
            if "/history/" in endpoint:
                return {"status": 404, "body": {"code": 404, "message": "not found"}}
            return {"status": 200, "body": {"code": 0, "data": [record]}}

        def is_closed(self):
            return False

    assert asyncio.run(fetch_user_reservations(Page(), {"headers": {"authorization": "x"}, "token": "token"})) == [record]


def test_fetch_user_reservations_falls_back_to_current_endpoint_when_history_is_unavailable():
    record = {
        "date": "2026-08-20",
        "begin": "20:00",
        "end": "21:00",
        "loc": "4层计算机类借阅区，座位号169",
        "stat": "RESERVE",
    }

    class Page:
        def __init__(self):
            self.endpoints = []

        async def evaluate(self, script, payload):
            endpoint = payload["endpoint"]
            self.endpoints.append(endpoint)
            if "/history/" in endpoint:
                return {"status": 200, "body": {"code": 12, "message": "登录失败"}}
            return {"status": 200, "body": {"code": 0, "data": [record]}}

        def is_closed(self):
            return False

    page = Page()
    assert asyncio.run(fetch_user_reservations(page, {"headers": {"authorization": "x"}, "token": "token"})) == [record]
    assert page.endpoints == [
        "/rest/v2/history/1/100?token=token",
        "/rest/v2/user/reservations?token=token",
    ]


def test_reservation_verification_uses_history_record_before_page_text():
    status, record, message = reservation_verification_status(
        [{
            "date": "2026-08-20",
            "loc": "南校区第二图书馆4层4层计算机类借阅区，座位号169",
            "begin": "15:00",
            "end": "17:00",
            "stat": "RESERVE",
        }],
        "预约成功页面没有完整展示条目",
        "2026-08-20",
        "4层计算机类借阅区",
        "169",
        "15:00",
        "17:00",
    )

    assert status == "success"
    assert record["stat"] == "RESERVE"
    assert "确认" in message
    assert "当天全部预约" not in message


def test_reservation_verification_reports_explicit_failure_and_unknown_separately():
    failed = reservation_verification_status([], "提示：当天已有预约，只能预约一个时间段", "2026-08-20", "阅览室", "169", "15:00", "17:00")
    unknown = reservation_verification_status([], "预约成功", "2026-08-20", "阅览室", "169", "15:00", "17:00")

    assert failed[0] == "failed"
    assert failed[2].startswith("当天已有预约")
    assert unknown[0] == "uncertain"
    assert "当天全部预约" not in unknown[2]


def test_reservation_verification_reports_all_active_reservations_for_the_day():
    status, record, message = reservation_verification_status(
        [{
            "date": "2026-08-20",
            "loc": "南校区第二图书馆4层4层计算机类借阅区，座位号169",
            "begin": "20:00",
            "end": "21:00",
            "stat": "RESERVE",
        }],
        "提示：当天已有预约，只能预约一个时间段",
        "2026-08-20",
        "4层计算机类借阅区",
        "168",
        "15:30",
        "17:00",
    )

    assert status == "failed"
    assert record is None
    assert message == "当天已有预约"


def test_submission_notice_captures_success_and_failure_popups():
    assert submission_notice("预约成功\n请到馆签到") == ("success", "预约成功")
    assert submission_notice("提示：当天已有预约，只能预约一个时间段") == ("failed", "当天已有预约")
    assert submission_notice("正在玩命预约中") == ("", "")


def test_reservation_verification_keeps_success_popup_as_evidence_when_history_is_delayed():
    status, record, message = reservation_verification_status(
        [],
        "我的预约页面为空",
        "2026-08-20",
        "4层计算机类借阅区",
        "169",
        "15:00",
        "17:00",
        submission_signal=("success", "预约成功"),
    )

    assert status == "pending"
    assert record is None
    assert "页面提示预约成功" in message
    assert "当天全部预约" not in message


def test_reservation_verification_uses_unique_time_match_when_location_fields_missing():
    status, record, message = reservation_verification_status(
        [{"date": "2026-08-20", "begin": "09:00", "end": "12:00", "stat": "RESERVE"}],
        "预约成功",
        "2026-08-20",
        "4层计算机类借阅区",
        "169",
        "09:00",
        "12:00",
        submission_signal=("success", "预约成功"),
    )

    assert status == "success"
    assert record["begin"] == "09:00"
    assert "时间匹配" in message
    assert "当天全部预约" not in message


def test_reservation_verification_reports_submitted_pending_when_success_has_no_record():
    status, record, message = reservation_verification_status(
        [],
        "预约成功",
        "2026-08-20",
        "4层计算机类借阅区",
        "169",
        "09:00",
        "12:00",
        submission_signal=("success", "预约成功"),
    )

    assert status == "pending"
    assert record is None
    assert "已提交" in message
    assert "当天全部预约" not in message


def test_success_page_keeps_pending_state_when_only_an_old_active_record_exists():
    status, record, message = reservation_verification_status(
        [{"date": "2026-08-20", "begin": "20:00", "end": "21:00", "stat": "RESERVE"}],
        "预约成功",
        "2026-08-20",
        "4层计算机类借阅区",
        "169",
        "09:00",
        "12:00",
        submission_signal=("success", "预约成功"),
    )

    assert status == "pending"
    assert record is None
    assert "已提交" in message
    assert "20:00-21:00" not in message


def test_success_page_does_not_reuse_a_pre_submit_incomplete_record():
    record = {"id": "old", "date": "2026-08-20", "begin": "09:00", "end": "12:00", "stat": "RESERVE"}
    status, matched, message = reservation_verification_status(
        [record],
        "预约成功",
        "2026-08-20",
        "4层计算机类借阅区",
        "169",
        "09:00",
        "12:00",
        submission_signal=("success", "预约成功"),
        pre_submit_reservations=[record],
    )

    assert status == "pending"
    assert matched is None
    assert "已提交" in message


def test_reservation_verification_delay_uses_conservative_backoff():
    assert [reservation_verification_delay(index) for index in range(5)] == [2000, 3000, 5000, 5000, 5000]


def test_daily_reservation_details_reports_all_records_and_statuses():
    details = daily_reservation_details([
        {"date": "2026-08-20", "loc": "阅览室，座位号169", "begin": "20:00", "end": "21:00", "stat": "RESERVE"},
        {"date": "2026-08-20", "loc": "阅览室，座位号168", "begin": "15:00", "end": "17:00", "stat": "CANCEL"},
    ], "2026-08-20")

    assert "RESERVE" in details
    assert "CANCEL" in details
    assert "20:00-21:00" in details
    assert "15:00-17:00" in details


def test_close_success_dialog_clicks_header_close_button_and_waits_hidden():
    class Button:
        def __init__(self, dialog):
            self.dialog = dialog
            self.clicked = False

        @property
        def last(self):
            return self

        def filter(self, **kwargs):
            return self

        def nth(self, index):
            return self

        async def is_visible(self):
            return True

        async def click(self):
            self.clicked = True
            self.dialog.hidden = True

        async def count(self):
            return 1

    class Dialog:
        def __init__(self):
            self.hidden = False
            self.close_button = Button(self)

        @property
        def first(self):
            return self

        @property
        def last(self):
            return self

        def filter(self, **kwargs):
            return self

        def locator(self, selector):
            return self.close_button

        async def wait_for(self, state, timeout):
            if state == "hidden" and not self.hidden:
                raise TimeoutError("still visible")

        async def get_by_role(self, *args, **kwargs):
            raise AssertionError("button fallback should not be used")

    class Page:
        def __init__(self):
            self.dialog = Dialog()

        def locator(self, selector):
            return self.dialog

    page = Page()

    assert asyncio.run(close_success_dialog(page)) is True
    assert page.dialog.close_button.clicked is True


def test_close_success_dialog_uses_global_confirm_button_when_dialog_is_custom():
    class Marker:
        def __init__(self):
            self.hidden = False

        @property
        def first(self):
            return self

        @property
        def last(self):
            return self

        def filter(self, **kwargs):
            return self

        def nth(self, index):
            return self

        async def count(self):
            return 1

        async def is_visible(self):
            return not self.hidden

        async def wait_for(self, state, timeout):
            if state == "hidden" and not self.hidden:
                raise TimeoutError("still visible")

    class Button:
        def __init__(self, marker):
            self.marker = marker
            self.clicked = False

        @property
        def last(self):
            return self

        def nth(self, index):
            return self

        async def count(self):
            return 1

        async def is_visible(self):
            return True

        async def click(self):
            self.clicked = True
            self.marker.hidden = True

    class Page:
        def __init__(self):
            self.marker = Marker()
            self.button = Button(self.marker)

        def locator(self, selector):
            class NoDialog:
                @property
                def first(self):
                    return self

                def filter(self, **kwargs):
                    return self

                async def wait_for(self, state, timeout):
                    raise TimeoutError("custom success view")

            return NoDialog()

        def get_by_text(self, pattern):
            return self.marker

        def get_by_role(self, role, name):
            return self.button

    page = Page()

    assert asyncio.run(close_success_dialog(page)) is True
    assert page.button.clicked is True


def test_close_success_dialog_waits_for_outer_blocking_wrapper_to_hide():
    class Root:
        def __init__(self):
            self.hidden = False
            self.close_button = None

        @property
        def first(self):
            return self

        @property
        def last(self):
            return self

        def nth(self, index):
            return self

        async def count(self):
            return 1

        async def is_visible(self):
            return not self.hidden

        def locator(self, selector):
            return self.close_button

        async def wait_for(self, state, timeout):
            if state == "hidden" and not self.hidden:
                raise TimeoutError("outer success wrapper is still visible")

    class Marker:
        def __init__(self, root):
            self.root = root
            self.hidden = False

        @property
        def first(self):
            return self

        @property
        def last(self):
            return self

        def nth(self, index):
            return self

        def filter(self, **kwargs):
            return self

        def locator(self, selector):
            return self.root

        async def count(self):
            return 1

        async def is_visible(self):
            return not self.hidden

        async def wait_for(self, state, timeout):
            if state == "hidden" and not self.hidden:
                raise TimeoutError("success marker is still visible")

    class Button:
        def __init__(self, marker):
            self.marker = marker
            self.clicked = False

        @property
        def first(self):
            return self

        @property
        def last(self):
            return self

        def nth(self, index):
            return self

        async def count(self):
            return 1

        async def is_visible(self):
            return True

        async def click(self):
            self.clicked = True
            # The inner success text disappears, but the outer wrapper remains
            # and must be cleared before navigation continues.
            self.marker.hidden = True

    class WrongPageButton:
        def __init__(self):
            self.clicked = False

        async def count(self):
            return 1

        def nth(self, index):
            return self

        async def is_visible(self):
            return True

        async def click(self):
            self.clicked = True

    class NoDialog:
        @property
        def first(self):
            return self

        @property
        def last(self):
            return self

        def filter(self, **kwargs):
            return self

        async def wait_for(self, state, timeout):
            raise TimeoutError("custom success view")

    class Keyboard:
        def __init__(self, root):
            self.root = root

        async def press(self, key):
            if key == "Escape":
                self.root.hidden = True

    class Page:
        def __init__(self):
            self.root = Root()
            self.marker = Marker(self.root)
            self.button = Button(self.marker)
            self.root.close_button = self.button
            self.wrong_page_button = WrongPageButton()
            self.keyboard = Keyboard(self.root)

        def locator(self, selector):
            if "data-seat-assistant-success-root" in selector:
                return self.root
            return NoDialog()

        def get_by_text(self, pattern):
            return self.marker

        def get_by_role(self, role, name):
            return self.wrong_page_button

        async def evaluate(self, script, payload=None):
            if "data-seat-assistant-success-root" in script and "setAttribute" in script:
                return {"found": True}
            return {"visible": not self.root.hidden}

    page = Page()

    assert asyncio.run(close_success_dialog(page)) is True
    assert page.button.clicked is True
    assert page.wrong_page_button.clicked is False
    assert page.root.hidden is True


def test_close_success_dialog_uses_escape_instead_of_page_button_when_root_has_no_control():
    class EmptyActions:
        async def count(self):
            return 0

    class Root:
        def __init__(self):
            self.hidden = False

        def locator(self, selector):
            return EmptyActions()

        async def count(self):
            return 1

        async def is_visible(self):
            return not self.hidden

        async def wait_for(self, state, timeout):
            if state == "hidden" and not self.hidden:
                raise TimeoutError("success wrapper is still visible")

    class Marker:
        def __init__(self):
            self.hidden = False

        @property
        def first(self):
            return self

        @property
        def last(self):
            return self

        def nth(self, index):
            return self

        def filter(self, **kwargs):
            return self

        async def count(self):
            return 1

        async def is_visible(self):
            return not self.hidden

        async def wait_for(self, state, timeout):
            if state == "hidden" and not self.hidden:
                raise TimeoutError("success text is still visible")

    class WrongPageButton:
        def __init__(self):
            self.clicked = False

        async def count(self):
            return 1

        def nth(self, index):
            return self

        async def is_visible(self):
            return True

        async def click(self):
            self.clicked = True

    class NoDialog:
        @property
        def first(self):
            return self

        def filter(self, **kwargs):
            return self

        async def wait_for(self, state, timeout):
            raise TimeoutError("custom success view")

    class Keyboard:
        def __init__(self, root, marker):
            self.root = root
            self.marker = marker
            self.pressed = []

        async def press(self, key):
            self.pressed.append(key)
            if key == "Escape":
                self.root.hidden = True
                self.marker.hidden = True

    class Page:
        def __init__(self):
            self.root = Root()
            self.marker = Marker()
            self.wrong_page_button = WrongPageButton()
            self.keyboard = Keyboard(self.root, self.marker)

        def locator(self, selector):
            if "data-seat-assistant-success-root" in selector:
                return self.root
            return NoDialog()

        def get_by_text(self, pattern):
            return self.marker

        def get_by_role(self, role, name):
            return self.wrong_page_button

        async def evaluate(self, script, payload=None):
            return {"found": True}

    page = Page()

    assert asyncio.run(close_success_dialog(page)) is True
    assert page.keyboard.pressed == ["Escape"]
    assert page.wrong_page_button.clicked is False


def test_close_time_dialog_waits_for_mask_to_disappear():
    class Mask:
        def __init__(self):
            self.hidden = False

        async def wait_for(self, state, timeout):
            if state == "hidden" and not self.hidden:
                raise TimeoutError("mask still visible")

        async def count(self):
            return 1

    class Button:
        def __init__(self, mask):
            self.mask = mask
            self.clicked = False

        @property
        def last(self):
            return self

        async def click(self):
            self.clicked = True
            self.mask.hidden = True

        async def count(self):
            return 1

    class Dialogs:
        def __init__(self, mask):
            self.mask = mask
            self.button = Button(mask)

        def locator(self, selector):
            return self.button

    class Page:
        def __init__(self):
            self.mask = Mask()
            self.dialogs = Dialogs(self.mask)
            self.keyboard = SimpleNamespace(press=lambda key: (_ for _ in ()).throw(AssertionError("escape fallback should not be used")))

        def locator(self, selector):
            if selector == ".el-dialog:visible":
                return self.dialogs
            if selector == ".reserve-time-Mask:visible":
                return self.mask
            raise AssertionError(selector)

        async def wait_for_timeout(self, milliseconds):
            return None

    page = Page()

    assert asyncio.run(close_time_dialog(page)) is None
    assert page.dialogs.button.clicked is True
    assert page.mask.hidden is True
