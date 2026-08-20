from datetime import datetime, time

import pytest

from seat_assistant.domain import apply_delay, build_reservation, check_in_window, reservation_start_for_arrival


def test_check_in_window_is_30_minutes_before_and_15_after():
    assert check_in_window(time(9, 0)) == (time(8, 30), time(9, 15))


def test_build_reservation_caps_duration_at_four_hours():
    result = build_reservation(time(9, 0), time(14, 0))
    assert result.start == time(9, 0)
    assert result.end == time(13, 0)


def test_apply_delay_rejects_arrival_outside_check_in_window():
    with pytest.raises(ValueError, match="签到窗口"):
        apply_delay(time(9, 0), time(10, 0))


def test_reservation_start_for_arrival_uses_half_hour_site_time():
    assert reservation_start_for_arrival(time(9, 20), (time(8, 30), time(9, 30))) == time(9, 30)


def test_reservation_start_for_arrival_rejects_arrival_outside_window():
    with pytest.raises(ValueError, match="到馆区间"):
        reservation_start_for_arrival(time(9, 31), (time(8, 30), time(9, 30)))
