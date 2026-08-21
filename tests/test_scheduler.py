from datetime import datetime

from seat_assistant.config import Settings
from seat_assistant.reservation import SeatResult
from seat_assistant.scheduler import run_once
from seat_assistant.service import AssistantService
from seat_assistant.storage import Repository
from seat_assistant.scheduler import next_booking_time, run_accounts_once
from seat_assistant.config import AccountSettings
from seat_assistant.notifications import render_scheduler_summary


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
    assert len(adapter.reserve_calls) == 1
    assert first["afternoon"]["status"] == "skipped"
    assert first["evening"]["status"] == "skipped"


def test_run_once_isolates_one_period_exception(tmp_path):
    adapter = SchedulerAdapter(raise_period="morning")
    service = AssistantService(Settings(control_token="local-token"), Repository(str(tmp_path / "db.sqlite")), adapter)

    result = run_once(service, "2026-08-21")

    assert result["morning"]["status"] == "uncertain"
    assert result["afternoon"]["status"] == "skipped"
    assert result["evening"]["status"] == "skipped"
    assert [call[1] for call in adapter.reserve_calls] == ["morning"]


def test_run_once_stops_after_one_successful_period_when_account_allows_one_booking(tmp_path):
    adapter = SchedulerAdapter()
    settings = Settings(control_token="local-token", max_reservations_per_run=1)
    service = AssistantService(settings, Repository(str(tmp_path / "db.sqlite")), adapter)

    result = run_once(service, "2026-08-21")

    assert [call[1] for call in adapter.reserve_calls] == ["morning"]
    assert result["morning"]["status"] == "reserved"
    assert result["afternoon"]["status"] == "skipped"
    assert result["evening"]["status"] == "skipped"


def test_run_accounts_once_runs_accounts_in_order_and_keeps_results_separate(tmp_path):
    calls = []

    class AccountService:
        def __init__(self, account_id):
            self.account_id = account_id

        def run_once(self, day):
            calls.append(self.account_id)
            return {"status": "reserved", "account_id": self.account_id}

    services = [AccountService("alice"), AccountService("bob")]
    result = run_accounts_once(services, "2026-08-21", interval_seconds=0)

    assert calls == ["alice", "bob"]
    assert result == {
        "alice": {"status": "reserved", "account_id": "alice"},
        "bob": {"status": "reserved", "account_id": "bob"},
    }


def test_run_once_skips_uninitialized_configured_account_without_reservation_call(tmp_path):
    adapter = SchedulerAdapter()
    settings = Settings(account_id="alice", control_token="local-token", require_initialization=True)
    service = AssistantService(settings, Repository(str(tmp_path / "db.sqlite"), "alice"), adapter)

    result = run_once(service, "2026-08-22")

    assert result["status"] == "skipped"
    assert "请先初始化账号" in result["message"]
    assert adapter.reserve_calls == []


def test_scheduler_notification_summary_contains_account_id_and_reason():
    text = render_scheduler_summary(
        "alice", "2026-08-22", {"status": "skipped", "message": "请先初始化账号"}
    )

    assert "alice" in text
    assert "请先初始化账号" in text
