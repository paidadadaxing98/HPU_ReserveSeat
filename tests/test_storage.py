import json

from seat_assistant.storage import Repository


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
