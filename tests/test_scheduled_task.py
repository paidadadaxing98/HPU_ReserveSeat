from datetime import datetime

from scripts.run_scheduled_task import parse_args, scheduled_target


def test_morning_trigger_books_tomorrow_morning():
    assert scheduled_target("morning", datetime(2026, 8, 21, 22, 0)) == (
        "2026-08-22", "morning"
    )


def test_afternoon_trigger_books_today_afternoon():
    assert scheduled_target("afternoon", datetime(2026, 8, 21, 12, 30)) == (
        "2026-08-21", "afternoon"
    )


def test_evening_trigger_books_today_evening():
    assert scheduled_target("evening", datetime(2026, 8, 21, 19, 30)) == (
        "2026-08-21", "evening"
    )


def test_missed_morning_trigger_outside_booking_window_is_skipped_safely():
    assert scheduled_target("morning", datetime(2026, 8, 22, 1, 0)) is None


def test_scheduled_task_supports_explicit_dry_run_switch():
    args = parse_args(["--period", "evening", "--dry-run"])

    assert args.period == "evening"
    assert args.dry_run is True
