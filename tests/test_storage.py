import json
from datetime import datetime

from seat_assistant.storage import Repository


def test_repository_saves_initialization_time_in_local_time(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"))

    before = datetime.now()
    repo.save_initialization_state("ready", True, True, True, message="ok")
    after = datetime.now()

    saved = datetime.fromisoformat(repo.initialization_state()["last_verified_at"])
    assert before.replace(microsecond=0) <= saved <= after.replace(microsecond=0)


def test_repository_persists_reservation_details_and_status(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"))

    repo.save_reservation(
        "2026-08-21",
        "morning",
        "reserved",
        "09:00",
        "12:00",
        "4层计算机类借阅区",
        "169",
        "演练预约",
    )

    record = repo.get_reservation("2026-08-21", "morning")
    assert record == {
        "date": "2026-08-21",
        "period": "morning",
        "status": "reserved",
        "start": "09:00",
        "end": "12:00",
        "room": "4层计算机类借阅区",
        "seat": "169",
        "message": "演练预约",
    }


def test_repository_persists_default_override_and_command_response(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"))
    repo.set_default("morning", "09:05")
    assert repo.default_override("morning") == "09:05"

    response = {"ok": True, "message": "已处理", "data": {"status": []}}
    assert repo.record_command("req-1", "状态", response) is True
    assert repo.record_command("req-1", "状态", response) is False
    stored = repo.get_command("req-1")
    assert stored["text"] == "状态"
    assert json.loads(stored["response"]) == response


def test_repository_queues_bot_command_idempotently_and_completes_it(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"), account_id="alice")

    assert repo.enqueue_bot_command("2026-08-29", "msg-1", "user-a", "今天不去了") is True
    assert repo.enqueue_bot_command("2026-08-29", "msg-1", "user-a", "今天不去了") is False
    pending = repo.pending_bot_commands("2026-08-29")
    assert len(pending) == 1
    assert pending[0]["request_id"] == "msg-1"
    assert pending[0]["sender"] == "user-a"
    assert pending[0]["text"] == "今天不去了"

    assert repo.claim_bot_command("msg-1") is True
    assert repo.claim_bot_command("msg-1") is False
    response = {"ok": True, "message": "已取消指定预约。"}
    repo.complete_bot_command("msg-1", "completed", response)

    stored = repo.get_bot_command("msg-1")
    assert stored["status"] == "completed"
    assert json.loads(stored["response"]) == response
    assert repo.pending_bot_commands("2026-08-29") == []


def test_repository_records_scheduler_run_and_events(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"))
    assert repo.scheduler_run("2026-08-21") is None
    repo.save_scheduler_run("2026-08-21", "completed", {"morning": "reserved"})
    assert repo.scheduler_run("2026-08-21") == {
        "date": "2026-08-21",
        "status": "completed",
        "summary": {"morning": "reserved"},
    }

    repo.event("arrival", "morning", "09:05")
    assert repo.events("arrival", "morning") == ["09:05"]


def test_learned_default_ignores_delay_samples(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"))
    repo.event("delay", "morning", "10:00")
    repo.event("delay", "morning", "10:39")
    repo.event("arrival", "morning", "09:32")

    assert repo.samples("morning") == ["09:32"]
    assert repo.learned_default("morning", "09:00") == "09:32"


def test_learned_default_uses_static_fallback_without_arrival_samples(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"))
    repo.event("delay", "morning", "10:00")

    assert repo.learned_default("morning", "09:00") == "09:00"


def test_repository_tracks_successes_per_account_and_date_idempotently(tmp_path):
    alice = Repository(str(tmp_path / "alice.sqlite"), account_id="alice")
    bob = Repository(str(tmp_path / "bob.sqlite"), account_id="bob")

    assert alice.successful_booking_count("2026-08-21") == 0
    assert alice.record_successful_booking("2026-08-21", "morning") is True
    assert alice.record_successful_booking("2026-08-21", "morning") is False
    assert alice.record_successful_booking("2026-08-21", "afternoon") is True
    assert alice.successful_booking_count("2026-08-21") == 2
    assert alice.successful_booking_count("2026-08-22") == 0
    assert bob.successful_booking_count("2026-08-21") == 0


def test_repository_success_count_does_not_include_failed_or_uncertain_records(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"), account_id="alice")
    repo.save_reservation("2026-08-21", "morning", "failed", "09:00", "12:00")
    repo.save_reservation("2026-08-21", "afternoon", "uncertain", "15:00", "17:00")

    assert repo.successful_booking_count("2026-08-21") == 0


def test_repository_persists_dynamic_session_across_repository_instances(tmp_path):
    database = tmp_path / "assistant.sqlite"
    repo = Repository(str(database), account_id="alice")
    repo.save_dynamic_session(
        "2026-08-21",
        "morning",
        "2026-08-21T08:00:00",
        "2026-08-21T12:00:00",
        "2026-08-21T07:00:00",
        "2026-08-21T09:45:00",
    )
    repo.update_dynamic_session(
        "2026-08-21",
        "morning",
        action_index=1,
        last_action_at="2026-08-21T08:13:00",
        message="已完成一次动态重约",
    )

    reopened = Repository(str(database), account_id="alice")

    assert reopened.get_dynamic_session("2026-08-21", "morning") == {
        "date": "2026-08-21",
        "period": "morning",
        "anchor_start": "2026-08-21T08:00:00",
        "anchor_end": "2026-08-21T12:00:00",
        "window_start": "2026-08-21T07:00:00",
        "window_end": "2026-08-21T09:45:00",
        "status": "monitoring",
        "action_index": 1,
        "entered_at": None,
        "cancel_count": 0,
        "last_action_at": "2026-08-21T08:13:00",
        "last_checked_at": None,
        "message": "已完成一次动态重约",
    }


def test_repository_counts_dynamic_cancellations_idempotently_per_day_and_account(tmp_path):
    database = tmp_path / "assistant.sqlite"
    repo = Repository(str(database), account_id="alice")

    assert repo.record_dynamic_cancellation("2026-08-21", "morning:08:13") is True
    assert repo.record_dynamic_cancellation("2026-08-21", "morning:08:13") is False
    assert repo.record_dynamic_cancellation("2026-08-21", "morning:08:43") is True
    assert repo.dynamic_cancellation_count("2026-08-21") == 2

    other = Repository(str(database), account_id="bob")
    assert other.dynamic_cancellation_count("2026-08-21") == 0


def test_repository_detects_existing_dynamic_cancellation_by_operation_key(tmp_path):
    database = tmp_path / "assistant.sqlite"
    repo = Repository(str(database), account_id="alice")

    assert repo.has_dynamic_cancellation("2026-08-21", "morning:08:13") is False
    assert repo.record_dynamic_cancellation("2026-08-21", "morning:08:13") is True
    assert repo.has_dynamic_cancellation("2026-08-21", "morning:08:13") is True
    other = Repository(str(database), account_id="bob")
    assert other.has_dynamic_cancellation("2026-08-21", "morning:08:13") is False


def test_repository_reset_day_clears_booking_state_but_keeps_configuration(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"), account_id="alice")
    repo.save_reservation("2026-08-21", "morning", "reserved", "08:30", "12:00")
    repo.record_successful_booking("2026-08-21", "morning:one")
    repo.save_scheduler_run("2026-08-21", "waiting", {"status": "waiting"})
    repo.set_default("morning", "09:00")
    repo.event("arrival", "morning", "09:00")

    result = repo.reset_day("2026-08-21")

    assert result == {"reservations": 1, "successful_bookings": 1, "scheduler_runs": 1}
    assert repo.reservations("2026-08-21") == []
    assert repo.successful_booking_count("2026-08-21") == 0
    assert repo.scheduler_run("2026-08-21") is None
    assert repo.default_override("morning") == "09:00"
    assert repo.events("arrival", "morning") == ["09:00"]


def test_repository_round_robins_rooms_per_account_library_and_floor(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"), account_id="alice")
    rooms = ["3层自主学习空间（Ⅱ）", "4层计算机类借阅区"]

    assert repo.next_room_round_robin("南校区第二图书馆", "4F", rooms) == rooms[0]
    assert repo.next_room_round_robin("南校区第二图书馆", "4F", rooms) == rooms[1]
    assert repo.next_room_round_robin("南校区第二图书馆", "4F", rooms) == rooms[0]
