"""Calculate unattended reservation task trigger times."""

from dataclasses import dataclass

from .domain import parse_hhmm


BOOKING_PERIODS = ("morning", "afternoon", "evening", "period04", "period05")
# The evening dynamic monitor can retain an account browser until 22:15.
# Leave a short handoff gap before the next-day morning booking uses it.
MORNING_TRIGGERS = ("22:17", "07:01")
LEGACY_FALLBACK_STARTS = {
    "period04": "10:05",
    "period05": "13:05",
}


@dataclass(frozen=True)
class BookingTaskSchedule:
    period: str
    triggers: tuple[str, ...]


def booking_task_schedule(
    period_name: str,
    accounts,
    repeat_minutes: int = 10,
    fallback_start: str | None = None,
    fallback_duration_minutes: int = 20,
) -> BookingTaskSchedule | None:
    """Return trigger times for a period, or None when no account enables it."""
    if period_name not in BOOKING_PERIODS:
        raise ValueError(f"未知预约时段：{period_name}")
    if repeat_minutes <= 0:
        raise ValueError("预约重试间隔必须大于 0 分钟")

    enabled_accounts = [
        account
        for account in accounts
        if _period_enabled(account, period_name)
    ]
    if not enabled_accounts:
        return None

    if period_name == "morning":
        return BookingTaskSchedule(period_name, MORNING_TRIGGERS)

    if period_name == "afternoon":
        latest_start = max(
            _period_endpoint(account, period_name, index=0)
            for account in enabled_accounts
        )
        return _repeating_schedule(period_name, "12:02", latest_start, repeat_minutes)

    if period_name == "evening":
        earliest_afternoon_end = min(
            _period_endpoint(account, "afternoon", index=1)
            for account in enabled_accounts
        )
        latest_evening_start = max(
            _period_endpoint(account, "evening", index=0)
            for account in enabled_accounts
        )
        return _repeating_schedule(
            period_name,
            _format_minutes(earliest_afternoon_end + 29),
            latest_evening_start,
            repeat_minutes,
        )

    start = fallback_start or LEGACY_FALLBACK_STARTS[period_name]
    return _repeating_schedule(
        period_name,
        start,
        _format_minutes(_clock_minutes(start) + fallback_duration_minutes),
        repeat_minutes,
    )


def _period_enabled(account, period_name: str) -> bool:
    periods = getattr(account, "periods", {}) or {}
    period = periods.get(period_name)
    return period is not None and bool(getattr(period, "enabled", True))


def _period_endpoint(account, period_name: str, index: int) -> int:
    periods = getattr(account, "periods", {}) or {}
    period = periods.get(period_name)
    try:
        value = period.arrival_window[index]
        return _clock_minutes(value)
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"账号的{period_name}预约时间配置无法解析") from exc


def _repeating_schedule(
    period_name: str,
    start: str,
    end: int | str,
    repeat_minutes: int,
) -> BookingTaskSchedule:
    start_minutes = _clock_minutes(start)
    end_minutes = _clock_minutes(end) if isinstance(end, str) else end
    if not 0 <= end_minutes < 24 * 60:
        raise ValueError(f"{period_name}预约任务结束时间无效")
    if end_minutes < start_minutes:
        raise ValueError(f"{period_name}预约任务时间范围无效：结束时间早于开始时间")

    values = list(range(start_minutes, end_minutes + 1, repeat_minutes))
    if values[-1] != end_minutes:
        values.append(end_minutes)
    return BookingTaskSchedule(
        period_name,
        tuple(_format_minutes(value) for value in values),
    )


def _clock_minutes(value: str) -> int:
    try:
        parsed = parse_hhmm(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"无效时间：{value}") from exc
    return parsed.hour * 60 + parsed.minute


def _format_minutes(value: int) -> str:
    if not 0 <= value < 24 * 60:
        raise ValueError(f"时间超出当天范围：{value}")
    return f"{value // 60:02d}:{value % 60:02d}"
