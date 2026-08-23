from datetime import datetime

from seat_assistant.commands import Command
from seat_assistant.config import Settings
from seat_assistant.reservation import SeatResult
from seat_assistant.service import AssistantService
from seat_assistant.storage import Repository


class FakeAdapter:
    def __init__(self, reserve_results=None, cancel_results=None):
        self.reserve_results = list(reserve_results or [SeatResult(True, "阅览室", "169", "ok")])
        self.cancel_results = list(cancel_results or [SeatResult(True, message="cancelled")])
        self.reserve_calls = []
        self.cancel_calls = []

    def reserve(self, date, period, start, end):
        self.reserve_calls.append((date, period, start, end))
        return self.reserve_results.pop(0)

    def cancel(self, date, period):
        self.cancel_calls.append((date, period))
        return self.cancel_results.pop(0)


def make_service(tmp_path, adapter=None):
    settings = Settings(control_token="local-token")
    repo = Repository(str(tmp_path / "assistant.sqlite"))
    return AssistantService(settings, repo, adapter or FakeAdapter()), repo


def test_reserve_period_does_not_submit_again_after_success(tmp_path):
    adapter = FakeAdapter()
    service, repo = make_service(tmp_path, adapter)

    first = service.reserve_period("2026-08-21", "morning", quota_day="2026-08-21")
    second = service.reserve_period("2026-08-21", "morning", quota_day="2026-08-21")

    assert first.success and second.success
    assert len(adapter.reserve_calls) == 1
    assert repo.get_reservation("2026-08-21", "morning")["status"] == "reserved"


def test_reserve_period_blocks_another_period_before_existing_booking_ends(tmp_path):
    adapter = FakeAdapter()
    service, repo = make_service(tmp_path, adapter)

    service.reserve_period("2026-08-21", "evening", now=datetime(2026, 8, 21, 20, 0))
    result = service.reserve_period("2026-08-21", "afternoon", now=datetime(2026, 8, 21, 20, 0))

    assert result.success is False
    assert result.conclusive is True
    assert "尚未结束" in result.message
    assert len(adapter.reserve_calls) == 1


def test_reserve_period_waits_until_previous_period_ends(tmp_path):
    adapter = FakeAdapter()
    service, repo = make_service(tmp_path, adapter)
    repo.save_reservation("2026-08-21", "morning", "reserved", "08:30", "12:00", "阅览室", "169", "ok")

    result = service.reserve_period(
        "2026-08-21",
        "afternoon",
        now=datetime(2026, 8, 21, 11, 59),
    )

    assert result.success is False
    assert result.conclusive is True
    assert "未结束" in result.message
    assert adapter.reserve_calls == []


def test_reserve_period_allows_next_period_after_previous_period_ends(tmp_path):
    adapter = FakeAdapter()
    service, repo = make_service(tmp_path, adapter)
    repo.save_reservation("2026-08-21", "morning", "reserved", "08:30", "12:00", "阅览室", "169", "ok")

    result = service.reserve_period(
        "2026-08-21",
        "afternoon",
        now=datetime(2026, 8, 21, 12, 0),
    )

    assert result.success is True
    assert [call[1] for call in adapter.reserve_calls] == ["afternoon"]


def test_reserve_period_does_not_retry_an_uncertain_same_day_booking(tmp_path):
    adapter = FakeAdapter(reserve_results=[SeatResult(False, message="提交后未确认", conclusive=False)])
    service, repo = make_service(tmp_path, adapter)

    first = service.reserve_period("2026-08-21", "evening")
    second = service.reserve_period("2026-08-21", "afternoon")

    assert first.conclusive is False
    assert second.conclusive is False
    assert "不明确" in second.message
    assert len(adapter.reserve_calls) == 1


def test_cancel_does_not_mark_reservation_cancelled_when_result_is_uncertain(tmp_path):
    adapter = FakeAdapter(cancel_results=[SeatResult(False, message="timeout", conclusive=False)])
    service, repo = make_service(tmp_path, adapter)
    service.reserve_period("2026-08-21", "morning")

    result = service.apply_command(Command("cancel", "morning"), "2026-08-21")

    assert result["ok"] is False
    assert repo.get_reservation("2026-08-21", "morning")["status"] == "reserved"


def test_delay_cancels_old_reservation_then_reserves_new_start(tmp_path):
    adapter = FakeAdapter(reserve_results=[SeatResult(True, "阅览室", "169", "ok"), SeatResult(True, "阅览室", "168", "ok")])
    service, repo = make_service(tmp_path, adapter)
    service.reserve_period("2026-08-21", "morning")

    result = service.apply_command(Command("delay", "morning", "09:20"), "2026-08-21")

    assert result["ok"] is True
    assert adapter.cancel_calls == [("2026-08-21", "morning")]
    assert adapter.reserve_calls[-1][2] == "09:30"
    assert repo.get_reservation("2026-08-21", "morning")["start"] == "09:30"


def test_delay_stops_if_old_cancellation_is_uncertain(tmp_path):
    adapter = FakeAdapter(cancel_results=[SeatResult(False, message="timeout", conclusive=False)])
    service, repo = make_service(tmp_path, adapter)
    service.reserve_period("2026-08-21", "morning")

    result = service.apply_command(Command("delay", "morning", "09:20"), "2026-08-21")

    assert result["ok"] is False
    assert len(adapter.reserve_calls) == 1
    assert repo.get_reservation("2026-08-21", "morning")["status"] == "reserved"


def test_default_change_survives_new_service_instance(tmp_path):
    service, repo = make_service(tmp_path)
    result = service.apply_command(Command("set_default", "morning", "09:05"), "2026-08-21")

    assert result["ok"] is True
    assert repo.default_override("morning") == "09:05"
    settings = Settings(control_token="local-token")
    service2 = AssistantService(settings, Repository(str(tmp_path / "assistant.sqlite")), FakeAdapter())
    service2.reserve_period("2026-08-22", "morning")
    assert service2.adapter.reserve_calls[0][2] == "09:00"


def test_record_arrival_is_persisted_without_changing_reservation(tmp_path):
    service, repo = make_service(tmp_path)
    result = service.apply_command(Command("record_arrival", "morning", "09:05"), "2026-08-21")

    assert result["ok"] is True
    assert repo.events("arrival", "morning") == ["09:05"]


class RecordingNotifier:
    def __init__(self, error=None):
        self.messages = []
        self.error = error

    def send(self, text):
        self.messages.append(text)
        if self.error:
            raise self.error
        return True


def test_reserve_period_notifies_success_after_persisting_result(tmp_path):
    notifier = RecordingNotifier()
    service, repo = make_service(tmp_path)
    service.notifier = notifier

    result = service.reserve_period("2026-08-21", "morning")

    assert result.success is True
    assert len(notifier.messages) == 1
    assert "2026-08-21" in notifier.messages[0]
    assert "阅览室" in notifier.messages[0]
    assert "**08:30 — 12:00**" in notifier.messages[0]
    assert "| 💺 **座位** | **169** |" in notifier.messages[0]
    assert repo.get_reservation("2026-08-21", "morning")["status"] == "reserved"


def test_reserve_period_notifies_uncertain_result_without_changing_it(tmp_path):
    notifier = RecordingNotifier()
    service, repo = make_service(
        tmp_path,
        FakeAdapter(reserve_results=[SeatResult(False, message="timeout", conclusive=False)]),
    )
    service.notifier = notifier

    result = service.reserve_period("2026-08-21", "morning")

    assert result.success is False
    assert len(notifier.messages) == 1
    assert "结果不明确" in notifier.messages[0]
    assert repo.get_reservation("2026-08-21", "morning")["status"] == "uncertain"


def test_notification_failure_does_not_change_reservation_result(tmp_path):
    notifier = RecordingNotifier(RuntimeError("network down"))
    service, repo = make_service(tmp_path)
    service.notifier = notifier

    result = service.reserve_period("2026-08-21", "morning")

    assert result.success is True
    assert repo.get_reservation("2026-08-21", "morning")["status"] == "reserved"


def test_new_success_counts_once_but_reusing_existing_booking_does_not(tmp_path):
    adapter = FakeAdapter()
    repo = Repository(str(tmp_path / "db.sqlite"), account_id="alice")
    service = AssistantService(Settings(control_token="local-token"), repo, adapter)

    first = service.reserve_period("2026-08-21", "morning", quota_day="2026-08-21")
    second = service.reserve_period("2026-08-21", "morning", quota_day="2026-08-21")

    assert first.success is True
    assert second.message == "已存在预约"
    assert repo.successful_booking_count("2026-08-21") == 1


def test_failed_and_uncertain_results_do_not_consume_success_quota(tmp_path):
    class FailingAdapter(FakeAdapter):
        def reserve(self, date, period, start, end):
            return SeatResult(False, message="failed")

    repo = Repository(str(tmp_path / "db.sqlite"), account_id="alice")
    service = AssistantService(Settings(control_token="local-token"), repo, FailingAdapter())

    result = service.reserve_period("2026-08-21", "morning", quota_day="2026-08-21")

    assert result.success is False
    assert repo.successful_booking_count("2026-08-21") == 0


def test_fourth_success_is_skipped_after_three_successes(tmp_path):
    adapter = FakeAdapter()
    repo = Repository(str(tmp_path / "db.sqlite"), account_id="alice")
    settings = Settings(control_token="local-token", daily_success_limit=3)
    service = AssistantService(settings, repo, adapter)
    for period in ("morning", "afternoon", "evening"):
        assert repo.record_successful_booking("2026-08-21", period) is True

    result = service.reserve_period("2026-08-22", "morning", quota_day="2026-08-21")

    assert result.success is False
    assert result.conclusive is True
    assert "成功预约次数已达到" in result.message


def test_fifth_success_is_allowed_but_sixth_is_blocked(tmp_path):
    adapter = FakeAdapter()
    repo = Repository(str(tmp_path / "db.sqlite"), account_id="alice")
    service = AssistantService(Settings(control_token="local-token", daily_success_limit=5), repo, adapter)
    for index in range(4):
        assert repo.record_successful_booking("2026-08-21", f"period{index}") is True

    fifth = service.reserve_period("2026-08-21", "period05", quota_day="2026-08-21")
    assert fifth.success is True

    sixth = service.reserve_period("2026-08-22", "morning", quota_day="2026-08-21")
    assert sixth.success is False
    assert "达到 5 次" in sixth.message


def test_reusing_existing_booking_is_allowed_when_success_quota_is_full(tmp_path):
    adapter = FakeAdapter()
    repo = Repository(str(tmp_path / "db.sqlite"), account_id="alice")
    settings = Settings(control_token="local-token", daily_success_limit=1)
    service = AssistantService(settings, repo, adapter)
    first = service.reserve_period("2026-08-21", "morning", quota_day="2026-08-21")
    assert first.success is True

    reused = service.reserve_period("2026-08-21", "morning", quota_day="2026-08-21")

    assert reused.success is True
    assert reused.message == "已存在预约"
    assert len(adapter.reserve_calls) == 1


def test_scheduler_success_quota_is_recorded_by_service_only(tmp_path):
    adapter = FakeAdapter()
    repo = Repository(str(tmp_path / "db.sqlite"), account_id="alice")
    service = AssistantService(Settings(control_token="local-token"), repo, adapter)

    result = service.reserve_period("2026-08-21", "morning", quota_day="2026-08-21")

    assert result.success is True
    assert repo.successful_booking_count("2026-08-21") == 1


def test_uninitialized_delay_does_not_cancel_existing_reservation(tmp_path):
    adapter = FakeAdapter()
    settings = Settings(account_id="alice", control_token="local-token", require_initialization=True)
    repo = Repository(str(tmp_path / "assistant.sqlite"), account_id="alice")
    service = AssistantService(settings, repo, adapter)
    repo.save_reservation("2026-08-21", "morning", "reserved", "08:30", "12:00", "阅览室", "169", "old")

    result = service.apply_command(Command("delay", "morning", "09:20"), "2026-08-21")

    assert result["ok"] is False
    assert adapter.cancel_calls == []
