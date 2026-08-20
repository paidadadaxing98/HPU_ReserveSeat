from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class Reservation:
    start: time
    end: time


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _time(value: int) -> time:
    return time(value // 60 % 24, value % 60)


def check_in_window(start: time) -> tuple[time, time]:
    return _time(_minutes(start) - 30), _time(_minutes(start) + 15)


def build_reservation(start: time, desired_end: time) -> Reservation:
    if _minutes(start) % 30 or _minutes(desired_end) % 30:
        raise ValueError("预约时间必须按30分钟设置")
    end_minutes = min(_minutes(desired_end), _minutes(start) + 240)
    if end_minutes <= _minutes(start):
        raise ValueError("结束时间必须晚于开始时间")
    return Reservation(start, _time(end_minutes))


def apply_delay(start: time, expected_arrival: time) -> Reservation:
    lower, upper = check_in_window(start)
    if not (_minutes(lower) <= _minutes(expected_arrival) <= _minutes(upper)):
        raise ValueError("预计到馆时间不在签到窗口内")
    return Reservation(start, _time(_minutes(start) + 240))


def reservation_start_for_arrival(expected_arrival: time, arrival_window: tuple[time, time]) -> time:
    """Choose a site's half-hour start whose check-in window covers arrival."""
    arrival_minutes = _minutes(expected_arrival)
    window_start, window_end = map(_minutes, arrival_window)
    if not window_start <= arrival_minutes <= window_end:
        raise ValueError("预计到馆时间不在到馆区间内")

    # The latest valid start is 15 minutes before arrival. Round that boundary
    # up to the next half-hour accepted by the reservation site.
    candidate_minutes = ((arrival_minutes - 15 + 29) // 30) * 30
    candidate = _time(candidate_minutes)
    lower, upper = check_in_window(candidate)
    if candidate_minutes > window_end or not (_minutes(lower) <= arrival_minutes <= _minutes(upper)):
        raise ValueError("预计到馆时间无法匹配半小时预约开始时间")
    return candidate


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()
