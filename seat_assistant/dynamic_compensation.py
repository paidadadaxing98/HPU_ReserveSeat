"""Pure scheduling rules for the dynamic reservation compensation window."""

from dataclasses import dataclass
import re
from datetime import date, datetime, time, timedelta
from enum import Enum


CHECKIN_BEFORE_MINUTES = 30
CHECKIN_AFTER_MINUTES = 15


class DecisionKind(str, Enum):
    WAIT = "wait"
    ENTERED = "entered"
    EARLY_RESCHEDULE = "early_reschedule"
    RESCHEDULE = "reschedule"
    CANCEL_UNENTERED = "cancel_unentered"
    AWAY_WAIT = "away_wait"
    AWAY_WAIT_COMPLETION = "away_wait_completion"
    AWAY_CANCEL = "away_cancel"
    AWAY_HOLD = "away_hold"


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str = ""


def parse_away_interval(
    day: str,
    away_begin,
    away_end,
) -> tuple[datetime, None] | None:
    """Parse an open temporary-leave record; a populated end means returned."""
    if away_end not in (None, ""):
        return None
    begin = _parse_event_datetime(day, away_begin)
    return (begin, None) if begin is not None else None


def temporary_leave_limit_minutes(away_begin: datetime) -> int:
    """Return the site's ordinary or meal-period temporary-leave limit."""
    minutes = away_begin.hour * 60 + away_begin.minute
    if 11 * 60 <= minutes < 12 * 60 + 30:
        return 90
    if 17 * 60 <= minutes < 18 * 60 + 30:
        return 90
    return 30


def temporary_leave_decision(
    now: datetime,
    away_begin: datetime,
    reservation_end: datetime,
    lead_minutes: int = 2,
) -> Decision:
    """Decide whether an open leave can wait or needs pre-expiry cancellation."""
    deadline = away_begin + timedelta(minutes=temporary_leave_limit_minutes(away_begin))
    if now >= reservation_end or deadline >= reservation_end:
        return Decision(DecisionKind.AWAY_WAIT_COMPLETION, "暂离截止时间不早于预约结束时间，等待履约完成")
    trigger = deadline - timedelta(minutes=lead_minutes)
    if now >= trigger:
        return Decision(DecisionKind.AWAY_CANCEL, "暂离即将超过允许时限，提前取消预约")
    return Decision(DecisionKind.AWAY_WAIT, "暂离尚未接近允许时限")


def _parse_event_datetime(day: str, value) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    date_match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    try:
        event_day = (
            date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))
            if date_match
            else date.fromisoformat(day)
        )
    except ValueError:
        return None
    time_match = re.search(r"(?<!\d)(\d{1,2}):([0-5]\d)(?::([0-5]\d))?", text)
    if not time_match:
        return None
    try:
        event_time = time(
            int(time_match.group(1)),
            int(time_match.group(2)),
            int(time_match.group(3) or 0),
        )
    except ValueError:
        return None
    return datetime.combine(event_day, event_time)


def checkin_bounds(anchor_start: datetime) -> tuple[datetime, datetime]:
    return (
        anchor_start - timedelta(minutes=CHECKIN_BEFORE_MINUTES),
        anchor_start + timedelta(minutes=CHECKIN_AFTER_MINUTES),
    )


def is_in_checkin_window(entry_at: datetime, anchor_start: datetime) -> bool:
    start, end = checkin_bounds(anchor_start)
    return start <= entry_at < end


def compensation_window(
    anchor_start: datetime,
    before_minutes: int = 30,
    after_minutes: int = 90,
) -> tuple[datetime, datetime]:
    checkin_start, checkin_end = checkin_bounds(anchor_start)
    return (
        checkin_start - timedelta(minutes=before_minutes),
        checkin_end + timedelta(minutes=after_minutes),
    )


def late_action_points(
    anchor_start: datetime,
    boundary_lead_minutes: int = 2,
    interval_minutes: int = 30,
    before_minutes: int = 30,
    after_minutes: int = 90,
) -> list[datetime]:
    """Return reschedule points and the final cancellation point."""
    _, checkin_end = checkin_bounds(anchor_start)
    _, compensation_end = compensation_window(anchor_start, before_minutes, after_minutes)
    first = checkin_end - timedelta(minutes=boundary_lead_minutes)
    final = compensation_end - timedelta(minutes=boundary_lead_minutes)
    points = []
    current = first
    while current <= final:
        points.append(current)
        current += timedelta(minutes=interval_minutes)
    return points


def evaluate(
    now: datetime,
    anchor_start: datetime,
    entry_at: datetime | None = None,
    action_index: int = 0,
    boundary_lead_minutes: int = 2,
    interval_minutes: int = 30,
    before_minutes: int = 30,
    after_minutes: int = 90,
) -> Decision:
    window_start, window_end = compensation_window(anchor_start, before_minutes, after_minutes)
    if now < window_start:
        return Decision(DecisionKind.WAIT, "动态补偿窗口尚未开始")
    if entry_at is not None:
        checkin_start, _ = checkin_bounds(anchor_start)
        if entry_at < checkin_start:
            return Decision(DecisionKind.EARLY_RESCHEDULE, "检测到早于签到窗口的入馆记录")
        return Decision(DecisionKind.ENTERED, "检测到入馆记录")

    points = late_action_points(
        anchor_start,
        boundary_lead_minutes,
        interval_minutes,
        before_minutes,
        after_minutes,
    )
    if not points or now >= points[-1]:
        return Decision(DecisionKind.CANCEL_UNENTERED, "动态补偿窗口即将结束，仍未检测到入馆")
    if action_index < len(points) - 1 and now >= points[action_index]:
        return Decision(DecisionKind.RESCHEDULE, "签到窗口已失效，按当前时间补偿预约")
    if now >= window_end:
        return Decision(DecisionKind.CANCEL_UNENTERED, "动态补偿窗口已结束，仍未检测到入馆")
    return Decision(DecisionKind.WAIT, "继续等待入馆记录")


def next_poll_delay(
    now: datetime,
    anchor_start: datetime,
    action_index: int = 0,
    normal_poll_seconds: int = 180,
    boundary_poll_seconds: int = 120,
    boundary_lead_minutes: int = 2,
    interval_minutes: int = 30,
    before_minutes: int = 30,
    after_minutes: int = 90,
) -> timedelta:
    window_start, window_end = compensation_window(anchor_start, before_minutes, after_minutes)
    if now < window_start:
        return window_start - now
    if now >= window_end:
        return timedelta(0)
    points = late_action_points(
        anchor_start,
        boundary_lead_minutes,
        interval_minutes,
        before_minutes,
        after_minutes,
    )
    future_points = [point for point in points if point > now]
    until_boundary = min(
        (point - now for point in future_points),
        default=timedelta(seconds=normal_poll_seconds),
    )
    regular = timedelta(seconds=normal_poll_seconds)
    boundary = timedelta(seconds=boundary_poll_seconds)
    return min(until_boundary, boundary if until_boundary <= regular else regular)
