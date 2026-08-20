import pytest
from seat_assistant.submission import validate_half_hour_time


def test_only_half_hour_times_are_allowed():
    assert validate_half_hour_time("15:00") == "15:00"
    assert validate_half_hour_time("15:30") == "15:30"
    with pytest.raises(ValueError, match="30分钟"):
        validate_half_hour_time("15:10")


def test_time_validation_zero_pads_single_digit_hour():
    assert validate_half_hour_time("9:00") == "09:00"
    assert validate_half_hour_time("9：30") == "09:30"
