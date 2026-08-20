from datetime import datetime, timedelta


def validate_next_day_booking(day: str, now: datetime) -> str:
    expected = (now.date() + timedelta(days=1)).isoformat()
    if day != expected:
        raise ValueError(f"只能预约次日：今天是 {now.date().isoformat()}，可预约日期为 {expected}。")
    return validate_booking_date(day, now)


def validate_booking_date(day: str, now: datetime) -> str:
    """Validate a same-day or next-day booking against the site's windows."""
    today = now.date()
    requested = datetime.strptime(day, "%Y-%m-%d").date()
    if requested == today:
        if not (7, 0) <= (now.hour, now.minute) <= (22, 30):
            raise ValueError("当天预约只在 7:00 至 22:30 之间开放。")
        return day

    tomorrow = today + timedelta(days=1)
    if requested == tomorrow:
        if not (19, 30) <= (now.hour, now.minute) <= (22, 30):
            raise ValueError("次日预约要在前一天 19:30 至 22:30 之间开放。")
        return day

    raise ValueError(f"只能预约当天或次日：今天是 {today.isoformat()}，请求日期为 {day}。")
