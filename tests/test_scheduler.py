from datetime import datetime

from seat_assistant.config import Settings
from seat_assistant.reservation import SeatResult
from seat_assistant.scheduler import run_once
from seat_assistant.service import AssistantService
from seat_assistant.storage import Repository
from seat_assistant.scheduler import next_booking_time


class SchedulerAdapter:
    def __init__(self, raise_period=None):
        self.raise_period = raise_period
        self.reserve_calls = []

    def reserve(self, date, period, start, end):
        self.reserve_calls.append((date, period, start, end))
        if period == self.raise_period:
            raise RuntimeError("adapter unavailable")
        return SeatResult(True, "演练阅览室", period, "dry-run")

    def cancel(self, date, period):
        return SeatResult(True, message="dry-run")


def test_next_booking_time_is_today_before_opening_and_tomorrow_after():
    assert next_booking_time(datetime(2026, 8, 19, 19, 0)).date().isoformat() == "2026-08-19"
    assert next_booking_time(datetime(2026, 8, 19, 20, 0)).date().isoformat() == "2026-08-20"


def test_run_once_is_idempotent_after_a_completed_run(tmp_path):
    adapter = SchedulerAdapter()
    service = AssistantService(Settings(control_token="local-token"), Repository(str(tmp_path / "db.sqlite")), adapter)

    first = run_once(service, "2026-08-21")
    second = run_once(service, "2026-08-21")

    assert set(first) == {"morning", "afternoon", "evening"}
    assert second == first
    assert len(adapter.reserve_calls) == 3


def test_run_once_isolates_one_period_exception(tmp_path):
    adapter = SchedulerAdapter(raise_period="morning")
    service = AssistantService(Settings(control_token="local-token"), Repository(str(tmp_path / "db.sqlite")), adapter)

    result = run_once(service, "2026-08-21")

    assert result["morning"]["status"] == "uncertain"
    assert result["afternoon"]["status"] == "reserved"
    assert result["evening"]["status"] == "reserved"
    assert [call[1] for call in adapter.reserve_calls] == ["morning", "afternoon", "evening"]
