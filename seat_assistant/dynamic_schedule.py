"""Dynamic monitor task schedule calculation."""

from dataclasses import dataclass
from datetime import date, datetime, time

from .domain import parse_hhmm, reservation_start_for_arrival
from .dynamic_compensation import compensation_window


MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True)
class DynamicTaskSchedule:
    period: str
    start: str
    end: str
    duration_minutes: int


def period_schedule(
    period,
    before_minutes: int,
    after_minutes: int,
    arrival_override: str | None = None,
) -> DynamicTaskSchedule | None:
    """Calculate one account's complete dynamic window for a period."""
    if not getattr(period, "enabled", True):
        return None
    if before_minutes < 0 or after_minutes < 0:
        raise ValueError("动态窗口扩展时间不能小于 0")
    try:
        arrival_window = tuple(parse_hhmm(value) for value in period.arrival_window)
        expected = parse_hhmm(arrival_override or period.default_arrival)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("时段到馆配置无法解析") from exc
    if len(arrival_window) != 2:
        raise ValueError("时段到馆窗口必须包含开始和结束时间")
    if _minutes(arrival_window[1]) <= _minutes(arrival_window[0]):
        raise ValueError("时段到馆窗口不能跨午夜")
    anchor_time = reservation_start_for_arrival(expected, arrival_window)
    anchor = datetime.combine(date(2000, 1, 1), anchor_time)
    window_start, window_end = compensation_window(anchor, before_minutes, after_minutes)
    if window_start.date() != anchor.date() or window_end.date() != anchor.date():
        raise ValueError("动态窗口不能跨午夜")
    start = _minutes(window_start.time())
    end = _minutes(window_end.time())
    if end <= start:
        raise ValueError("动态窗口结束时间必须晚于开始时间")
    return DynamicTaskSchedule("", _format_minutes(start), _format_minutes(end), end - start)


def aggregate_period_schedule(
    period_name: str,
    schedules: list[DynamicTaskSchedule],
) -> DynamicTaskSchedule | None:
    """Cover all account windows for one period with one task."""
    if not schedules:
        return None
    starts = [_clock_minutes(item.start) for item in schedules]
    ends = [_clock_minutes(item.end) for item in schedules]
    start = min(starts)
    end = max(ends)
    if start < 0 or end >= MINUTES_PER_DAY or end <= start:
        raise ValueError("聚合后的动态窗口无效或跨午夜")
    return DynamicTaskSchedule(period_name, _format_minutes(start), _format_minutes(end), end - start)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _clock_minutes(value: str) -> int:
    try:
        return _minutes(parse_hhmm(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"无效时间：{value}") from exc


def _format_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"
