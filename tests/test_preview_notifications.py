import asyncio
from types import SimpleNamespace

from seat_assistant.reservation import SeatResult
from scripts.preview_reservation import close_success_dialog, reservation_summary, send_preview_notification


def test_preview_notification_uses_manual_booking_context():
    class RecordingNotifier:
        def __init__(self):
            self.messages = []

        def send(self, text):
            self.messages.append(text)
            return True

    notifier = RecordingNotifier()
    args = SimpleNamespace(date="2026-08-20", start="15:00", end="17:00")
    result = SeatResult(True, "4层计算机类借阅区", "169", "网页核验成功")

    assert send_preview_notification(notifier, args, result) is True
    assert "手动预约成功" in notifier.messages[0]
    assert "座位：169" in notifier.messages[0]


def test_reservation_summary_reads_live_api_location_and_time_fields():
    summary = reservation_summary({
        "location": "南校区第二图书馆4层4层计算机类 借阅区，座位号169",
        "begin": "15:00",
        "end": "17:00",
    })

    assert summary == "南校区第二图书馆4层4层计算机类 借阅区，座位 169，15:00-17:00"


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
