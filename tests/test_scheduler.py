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
    def __init__(self, raise_period=None, current=None):
        self.raise_period = raise_period
        self.reserve_calls = []
        self.current = list(current or [])

    def reserve(self, date, period, start, end):
        self.reserve_calls.append((date, period, start, end))
        if period == self.raise_period:
            raise RuntimeError("adapter unavailable")
        return SeatResult(True, "演练阅览室", period, "dry-run")

    def cancel(self, date, period):
        return SeatResult(True, message="dry-run")

    def current_reservations(self, day):
        return list(self.current)


def test_next_booking_time_is_today_before_opening_and_tomorrow_after():
    assert next_booking_time(datetime(2026, 8, 19, 19, 0)).date().isoformat() == "2026-08-19"
    assert next_booking_time(datetime(2026, 8, 19, 20, 0)).date().isoformat() == "2026-08-20"


def test_run_once_is_idempotent_after_a_completed_run(tmp_path):
    adapter = SchedulerAdapter()
    settings = Settings(control_token="local-token")
    settings.periods["afternoon"].enabled = False
    settings.periods["evening"].enabled = False
    service = AssistantService(settings, Repository(str(tmp_path / "db.sqlite")), adapter)

    first = run_once(service, "2026-08-21", now=datetime(2026, 8, 20, 19, 30))
    second = run_once(service, "2026-08-21", now=datetime(2026, 8, 20, 19, 30))

    assert set(first) == {"status", "account_id", "message", "morning", "afternoon", "evening", "period04", "period05"}
    assert second == first
    assert len(adapter.reserve_calls) == 1
    assert first["afternoon"]["status"] == "skipped"
    assert first["evening"]["status"] == "skipped"
    assert first["period04"]["status"] == "skipped"
    assert first["period05"]["status"] == "skipped"


def test_run_once_isolates_one_period_exception(tmp_path):
    adapter = SchedulerAdapter(raise_period="morning")
    service = AssistantService(Settings(control_token="local-token"), Repository(str(tmp_path / "db.sqlite")), adapter)

    result = run_once(service, "2026-08-21", now=datetime(2026, 8, 20, 19, 30))

    assert result["morning"]["status"] == "uncertain"
    assert result["afternoon"]["status"] == "pending"
    assert result["evening"]["status"] == "pending"
    assert [call[1] for call in adapter.reserve_calls] == ["morning"]


def test_run_once_stops_after_one_successful_period_when_account_allows_one_booking(tmp_path):
    adapter = SchedulerAdapter()
    settings = Settings(control_token="local-token", max_reservations_per_run=1)
    service = AssistantService(settings, Repository(str(tmp_path / "db.sqlite")), adapter)

    result = run_once(service, "2026-08-21", now=datetime(2026, 8, 20, 19, 30))

    assert [call[1] for call in adapter.reserve_calls] == ["morning"]
    assert result["morning"]["status"] == "reserved"
    assert result["afternoon"]["status"] == "pending"
    assert result["evening"]["status"] == "pending"


def test_run_once_waits_for_live_reservation_before_submitting_next_period(tmp_path):
    adapter = SchedulerAdapter(current=[{
        "date": "2026-08-21", "begin": "08:30", "end": "12:00", "stat": "RESERVE",
    }])
    service = AssistantService(Settings(control_token="local-token"), Repository(str(tmp_path / "db.sqlite")), adapter)

    result = run_once(service, "2026-08-21", now=datetime(2026, 8, 21, 10, 0))

    assert result["status"] == "waiting"
    assert adapter.reserve_calls == []
    assert "前一个预约" in result["message"]


def test_run_once_advances_to_next_period_after_previous_reservation_ends(tmp_path):
    adapter = SchedulerAdapter()
    repo = Repository(str(tmp_path / "db.sqlite"))
    service = AssistantService(Settings(control_token="local-token"), repo, adapter)
    repo.save_reservation("2026-08-21", "morning", "reserved", "08:30", "12:00", "阅览室", "169", "ok")

    result = run_once(service, "2026-08-21", now=datetime(2026, 8, 21, 12, 0))

    assert [call[1] for call in adapter.reserve_calls] == ["afternoon"]
    assert result["afternoon"]["status"] == "reserved"


def test_run_once_does_not_schedule_disabled_periods(tmp_path):
    adapter = SchedulerAdapter()
    settings = Settings(control_token="local-token")
    settings.periods["afternoon"].enabled = False
    service = AssistantService(settings, Repository(str(tmp_path / "db.sqlite")), adapter)

    result = run_once(service, "2026-08-21", now=datetime(2026, 8, 21, 10, 0))

    assert result["afternoon"]["status"] == "skipped"
    assert [call[1] for call in adapter.reserve_calls] == ["morning"]


def test_run_once_stops_when_live_reservation_has_no_end_time(tmp_path):
    adapter = SchedulerAdapter(current=[{
        "date": "2026-08-21", "begin": "08:30", "stat": "RESERVE",
    }])
    service = AssistantService(Settings(control_token="local-token"), Repository(str(tmp_path / "db.sqlite")), adapter)

    result = run_once(service, "2026-08-21", now=datetime(2026, 8, 21, 10, 0))

    assert result["status"] == "uncertain"
    assert adapter.reserve_calls == []


def test_run_once_marks_expired_pending_period_missed_then_books_next_period(tmp_path):
    adapter = SchedulerAdapter()
    service = AssistantService(Settings(control_token="local-token"), Repository(str(tmp_path / "db.sqlite")), adapter)

    result = run_once(service, "2026-08-21", now=datetime(2026, 8, 21, 12, 0))

    assert result["morning"]["status"] == "missed"
    assert result["afternoon"]["status"] == "reserved"
    assert [call[1] for call in adapter.reserve_calls] == ["afternoon"]


def test_run_once_advances_after_a_conclusive_failed_period(tmp_path):
    adapter = SchedulerAdapter()
    repo = Repository(str(tmp_path / "db.sqlite"))
    service = AssistantService(Settings(control_token="local-token"), repo, adapter)
    repo.save_reservation("2026-08-21", "morning", "failed", "08:30", "12:00", message="座位已被占用")

    result = run_once(service, "2026-08-21", now=datetime(2026, 8, 21, 12, 0))

    assert result["morning"]["status"] == "failed"
    assert result["afternoon"]["status"] == "reserved"
    assert [call[1] for call in adapter.reserve_calls] == ["afternoon"]


def test_run_once_preserves_failed_period_details_saved_by_service(tmp_path):
    class FailingAdapter(SchedulerAdapter):
        def reserve(self, date, period, start, end):
            self.reserve_calls.append((date, period, start, end))
            return SeatResult(False, "阅览室", "169", "座位已被占用", conclusive=True)

    repo = Repository(str(tmp_path / "db.sqlite"))
    service = AssistantService(Settings(control_token="local-token"), repo, FailingAdapter())

    run_once(service, "2026-08-21", now=datetime(2026, 8, 20, 19, 30))

    record = repo.get_reservation("2026-08-21", "morning")
    assert record["status"] == "failed"
    assert (record["start"], record["end"], record["room"], record["seat"]) == (
        "08:30", "12:00", "阅览室", "169"
    )


def test_run_once_preserves_uncertain_period_details_saved_by_service(tmp_path):
    class UncertainAdapter(SchedulerAdapter):
        def reserve(self, date, period, start, end):
            self.reserve_calls.append((date, period, start, end))
            return SeatResult(False, "阅览室", "169", "提交结果不明确", conclusive=False)

    repo = Repository(str(tmp_path / "db.sqlite"))
    service = AssistantService(Settings(control_token="local-token"), repo, UncertainAdapter())

    run_once(service, "2026-08-21", now=datetime(2026, 8, 20, 19, 30))

    record = repo.get_reservation("2026-08-21", "morning")
    assert record["status"] == "uncertain"
    assert (record["start"], record["end"], record["room"], record["seat"]) == (
        "08:30", "12:00", "阅览室", "169"
    )


def test_run_once_is_not_completed_when_any_enabled_period_failed(tmp_path):
    adapter = SchedulerAdapter()
    settings = Settings(control_token="local-token")
    settings.periods["evening"].enabled = False
    repo = Repository(str(tmp_path / "db.sqlite"))
    service = AssistantService(settings, repo, adapter)
    repo.save_reservation("2026-08-21", "morning", "failed", "08:30", "12:00", message="座位已被占用")
    repo.save_reservation("2026-08-21", "afternoon", "reserved", "15:00", "18:30", "阅览室", "169", "ok")

    result = run_once(service, "2026-08-21", now=datetime(2026, 8, 21, 19, 0))

    assert result["status"] == "failed"
    assert "全部启用时段未全部预约成功" in result["message"]


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
