from datetime import datetime

from seat_assistant.commands import Command
from seat_assistant.config import Settings
from seat_assistant.reservation import DryRunReservation
from seat_assistant.scheduler import run_once
from seat_assistant.service import AssistantService
from seat_assistant.storage import Repository


def test_local_flow_persists_booking_delay_and_cancellation(tmp_path):
    database = tmp_path / "assistant.sqlite"
    repo = Repository(str(database))
    service = AssistantService(Settings(control_token="local-token"), repo, DryRunReservation())

    booking = run_once(service, "2026-08-21", now=datetime(2026, 8, 20, 19, 30))
    assert booking["morning"]["status"] == "reserved"
    assert repo.get_reservation("2026-08-21", "morning")["start"] == "08:30"

    duplicate = service.reserve_period("2026-08-21", "morning")
    assert duplicate.message == "已存在预约"

    delayed = service.apply_command(Command("delay", "morning", "09:20"), "2026-08-21")
    assert delayed["ok"] is True
    assert repo.get_reservation("2026-08-21", "morning")["start"] == "09:30"

    cancelled = service.apply_command(Command("cancel", "afternoon"), "2026-08-21")
    assert cancelled["ok"] is True
    assert repo.get_reservation("2026-08-21", "afternoon") is None
