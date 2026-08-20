from datetime import datetime
import pytest

from seat_assistant.booking_window import validate_booking_date, validate_next_day_booking


def test_only_next_day_is_allowed_after_opening_time():
    now = datetime(2026, 8, 19, 19, 30)
    assert validate_next_day_booking("2026-08-20", now) == "2026-08-20"
    with pytest.raises(ValueError, match="只能预约次日"):
        validate_next_day_booking("2026-08-21", now)


def test_next_day_is_not_open_before_1930():
    with pytest.raises(ValueError, match="19:30"):
        validate_next_day_booking("2026-08-20", datetime(2026, 8, 19, 19, 29))


def test_same_day_booking_is_allowed_during_assignment_hours():
    assert validate_booking_date("2026-08-20", datetime(2026, 8, 20, 7, 0)) == "2026-08-20"
    assert validate_booking_date("2026-08-20", datetime(2026, 8, 20, 22, 30)) == "2026-08-20"


def test_same_day_booking_is_rejected_outside_assignment_hours():
    with pytest.raises(ValueError, match="7:00"):
        validate_booking_date("2026-08-20", datetime(2026, 8, 20, 6, 59))
    with pytest.raises(ValueError, match="22:30"):
        validate_booking_date("2026-08-20", datetime(2026, 8, 20, 22, 31))


def test_next_day_booking_opens_at_1930_and_closes_at_2230():
    assert validate_booking_date("2026-08-21", datetime(2026, 8, 20, 19, 30)) == "2026-08-21"
    with pytest.raises(ValueError, match="19:30"):
        validate_booking_date("2026-08-21", datetime(2026, 8, 20, 19, 29))
    with pytest.raises(ValueError, match="22:30"):
        validate_booking_date("2026-08-21", datetime(2026, 8, 20, 22, 31))


def test_future_dates_are_rejected():
    with pytest.raises(ValueError, match="当天或次日"):
        validate_booking_date("2026-08-22", datetime(2026, 8, 20, 20, 0))
