import re


_CLOCK_RE = re.compile(r"(?<!\d)(\d{1,2}):([0-5]\d)(?!\d)")
_DATE_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")
_ACTIVE_RESERVATION_STATUSES = frozenset({
    "ACTIVE",
    "BOOKED",
    "CONFIRMED",
    "IN_USE",
    "RESERVE",
    "RESERVED",
    "USING",
    "VALID",
    "已预约",
    "预约中",
    "生效",
})


def confirmation_required(submit_flag: bool, confirm_flag: bool, phrase: str) -> bool:
    if not submit_flag:
        return True
    return confirm_flag and phrase.strip() != "SUBMIT"


def reservation_matches(text: str, day: str, room: str, seat: str, start: str, end: str) -> bool:
    normalized = " ".join(text.replace("：", ":").split())
    return all(value in normalized for value in (day, room, seat, start, end))


def submission_settled(text: str) -> bool:
    return "正在玩命预约中" not in text and "玩命预约" not in text


def find_similar_reservation(
    reservations: list[dict],
    day: str,
    room: str,
    start: str,
    end: str,
    min_overlap: float = 0.75,
) -> dict | None:
    """Return an existing reservation covering most of the requested interval."""
    requested_start = _clock_minutes(start)
    requested_end = _clock_minutes(end)
    if requested_start is None or requested_end is None or requested_end <= requested_start:
        return None
    requested_duration = requested_end - requested_start
    requested_room = _normalize_room(room)
    for item in reservations or []:
        if not isinstance(item, dict) or _extract_date(item) != day:
            continue
        if not _is_active_reservation(item):
            continue
        existing_room = _extract_room(item)
        if existing_room and requested_room and not _room_matches(existing_room, requested_room):
            continue
        existing_start = _extract_time(item, ("startTime", "start_time", "start", "beginTime", "begin"))
        existing_end = _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish"))
        if existing_start is None or existing_end is None or existing_end <= existing_start:
            continue
        overlap = max(0, min(requested_end, existing_end) - max(requested_start, existing_start))
        if overlap / requested_duration >= min_overlap:
            return item
    return None


def _is_active_reservation(item: dict) -> bool:
    """Only reuse records whose API status explicitly says they are active."""
    for key in ("status", "state", "reservationStatus", "reserveStatus", "bookingStatus"):
        if key not in item:
            continue
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("code") or value.get("name") or value.get("value") or value.get("status")
        normalized = _value_text(value).strip().upper()
        return normalized in _ACTIVE_RESERVATION_STATUSES
    return False


def _extract_date(item: dict) -> str | None:
    for key in ("date", "day", "onDate", "reservationDate", "reserveDate"):
        value = _value_text(item.get(key))
        match = _DATE_RE.search(value)
        if match:
            return match.group(0)
    for key in ("startTime", "start_time", "start", "beginTime", "begin"):
        value = _value_text(item.get(key))
        match = _DATE_RE.search(value)
        if match:
            return match.group(0)
    return None


def _extract_room(item: dict) -> str:
    for key in ("roomName", "room_name", "readingRoomName", "room", "readingRoom", "location"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("title") or value.get("text")
        normalized = _normalize_room(_value_text(value))
        if normalized:
            return normalized
    return ""


def _room_matches(existing: str, requested: str) -> bool:
    """Match a room name embedded in the site's combined location field."""
    return existing == requested or requested in existing


def _extract_time(item: dict, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, (int, float)) and 0 <= value < 24 * 60:
            return int(value)
        match = _CLOCK_RE.search(_value_text(value))
        if match:
            return int(match.group(1)) * 60 + int(match.group(2))
    return None


def _clock_minutes(value: str) -> int | None:
    match = _CLOCK_RE.search(_value_text(value))
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _value_text(value) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_room(value: str) -> str:
    return "".join(_value_text(value).split())


def requested_times_available(options: list[str], requested: list[str]) -> bool:
    normalized = {normalize_time_option(value) for value in options}
    return all(normalize_time_option(value) in normalized for value in requested)


def normalize_time_option(value: str) -> str:
    normalized = "".join(value.replace("：", ":").split())
    if ":" not in normalized:
        return normalized
    hour, minute = normalized.split(":", 1)
    if hour.isdigit() and minute.isdigit():
        return f"{int(hour):02d}:{int(minute):02d}"
    return normalized


def time_options(response: dict, key: str) -> list[dict[str, str]]:
    data = response.get("data", {}) if isinstance(response, dict) else {}
    options = []
    for item in data.get(key, []) if isinstance(data, dict) else []:
        if not isinstance(item, dict) or not item.get("value"):
            continue
        options.append({"id": str(item.get("id", "")), "value": normalize_time_option(str(item["value"]))})
    return options


def time_values(response: dict, key: str) -> list[str]:
    return [item["value"] for item in time_options(response, key)]


def time_option_id(response: dict, key: str, value: str) -> str | None:
    normalized = normalize_time_option(value)
    return next((item["id"] for item in time_options(response, key) if item["value"] == normalized), None)


def time_to_minutes(value: str) -> str:
    hour, minute = map(int, normalize_time_option(value).split(":"))
    return str(hour * 60 + minute)


def end_times_request_url(seat_id: int | str, day: str, start: str, start_id: str | None = None) -> str:
    return f"rest/v2/endTimesForSeat/{seat_id}/{day}/{start_id if start_id is not None else time_to_minutes(start)}"


def end_time_response_matches_start(url: str, start_id: str) -> bool:
    """Match the native end-time response to the selected start option id."""
    from urllib.parse import parse_qs, urlsplit

    if "/rest/v2/endTimesForSeat/" not in url:
        return False
    parts = urlsplit(url)
    path_id = parts.path.rstrip("/").rsplit("/", 1)[-1]
    query_start = parse_qs(parts.query).get("start", [None])[0]
    return path_id == str(start_id) and (query_start is None or query_start == str(start_id))


def end_times_response_matches(url: str, start: str) -> bool:
    return end_time_response_matches_start(url, time_to_minutes(start))


def validate_half_hour_time(value: str) -> str:
    normalized = normalize_time_option(value)
    hour, minute = map(int, normalized.split(":"))
    if minute not in (0, 30):
        raise ValueError(f"预约时间必须按30分钟设置：{value}")
    return normalized
