from datetime import datetime

from scripts.run_scheduled_task import scheduled_target


def test_morning_trigger_books_tomorrow_morning():
    assert scheduled_target("morning", datetime(2026, 8, 21, 19, 35)) == (
        "2026-08-22", "morning"
    )


def test_afternoon_trigger_books_today_afternoon():
    assert scheduled_target("afternoon", datetime(2026, 8, 21, 12, 30)) == (
        "2026-08-21", "afternoon"
    )


def test_evening_trigger_books_today_evening():
    assert scheduled_target("evening", datetime(2026, 8, 21, 19, 10)) == (
        "2026-08-21", "evening"
    )


def test_missed_morning_trigger_outside_booking_window_is_skipped_safely():
    assert scheduled_target("morning", datetime(2026, 8, 22, 1, 0)) is None
