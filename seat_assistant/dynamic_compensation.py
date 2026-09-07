"""Pure scheduling rules for the dynamic reservation compensation window."""

from dataclasses import dataclass
import re
from datetime import date, datetime, time, timedelta
from enum import Enum


CHECKIN_BEFORE_MINUTES = 30
CHECKIN_AFTER_MINUTES = 15
# The final cancellation starts this many minutes before the window end so a
# slow browser round still finishes before the hard cutoff.
FINALIZE_LEAD_MINUTES = 3


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


def reservation_checkpoints(
    reservation_start: datetime,
    round_index: int = 0,
    access_before_minutes: int = 6,
    reservation_first_before_minutes: int = 4,
    reservation_final_before_minutes: int = 2,
) -> dict[str, datetime]:
    """Return the three remote checks for one reservation round.

    Every round — the initial reservation and each compensation rebooking —
    is checked at start +9/+11/+13 minutes, just before its check-in window
    closes.  Checking a rebooked slot *before* it starts would declare a
    perfectly valid booking late and cancel it, so the offsets never move
    ahead of the slot.
    """
    base = reservation_start + timedelta(minutes=15)
    offsets = (
        -access_before_minutes,
        -reservation_first_before_minutes,
        -reservation_final_before_minutes,
    )
    return {
        "access": base + timedelta(minutes=offsets[0]),
        "reservation_first": base + timedelta(minutes=offsets[1]),
        "reservation_final": base + timedelta(minutes=offsets[2]),
    }


def last_action_deadline(window_end: datetime, safety_minutes: int = 1) -> datetime:
    """Return the latest time at which a dynamic action may start."""
    return window_end - timedelta(minutes=safety_minutes)


def finalize_start(window_end: datetime, lead_minutes: int = FINALIZE_LEAD_MINUTES) -> datetime:
    """Return the earliest time at which the final cutoff cancel may run."""
    return window_end - timedelta(minutes=max(1, lead_minutes))


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


def next_half_hour_start(now: datetime, not_before: datetime | None = None) -> str:
    """Return the next usable half-hour booking start for ``now``.

    An exact half-hour is still usable; any time after it advances to the
    following node.  The returned value is always explicit so later remote
    reservation matching does not depend on the site's ``current`` shortcut.
    """
    minute = now.minute
    exact_node = minute in {0, 30} and now.second == 0 and now.microsecond == 0
    if exact_node:
        rounded = now.replace(second=0, microsecond=0)
    else:
        rounded = now.replace(
            minute=30 if minute < 30 else 0,
            second=0,
            microsecond=0,
        )
        if minute >= 30:
            rounded += timedelta(hours=1)
    if not_before is not None:
        boundary = not_before.replace(second=0, microsecond=0)
        if rounded < boundary:
            rounded = boundary + timedelta(minutes=30)
    return rounded.strftime("%H:%M")


def next_late_action_index(
    now: datetime,
    anchor_start: datetime,
    action_index: int = 0,
    boundary_lead_minutes: int = 2,
    interval_minutes: int = 30,
    before_minutes: int = 30,
    after_minutes: int = 90,
) -> int:
    """Advance past the action just completed and every missed boundary."""
    points = late_action_points(
        anchor_start,
        boundary_lead_minutes,
        interval_minutes,
        before_minutes,
        after_minutes,
    )
    next_index = max(0, int(action_index) + 1)
    while next_index < len(points) and points[next_index] <= now:
        next_index += 1
    return min(next_index, len(points))


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
