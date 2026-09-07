import re
from datetime import date, datetime


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
_IN_USE_RESERVATION_STATUSES = frozenset({
    "AWAY",
    "CHECK_IN",
    "IN_USE",
    "USING",
    "暂离",
    "签到成功",
    "使用中",
    "履约中",
})
_COMPLETED_RESERVATION_STATUSES = frozenset({"COMPLETE", "COMPLETED", "履约完成", "已履约"})
# The site reports an unattended reservation as MISS; STOP is a booking the
# site terminated (temporary leave exceeded its limit).  Temporary leave
# itself is AWAY and stays an in-use state so the leave guard still applies.
_MISSED_RESERVATION_STATUSES = frozenset({"MISS", "MISSED", "STOP", "失约", "已失约", "终止"})
_CANCELLED_RESERVATION_STATUSES = frozenset({"CANCEL", "CANCELLED", "CANCELED", "已取消"})
_LOCAL_RETRY_BLOCKING_STATUSES = frozenset({"reserved", "pending", "uncertain"})


def confirmation_required(submit_flag: bool, confirm_flag: bool, phrase: str) -> bool:
    if not submit_flag:
        return True
    return confirm_flag and phrase.strip() != "SUBMIT"


def local_reservation_blocks_retry(record: dict | None) -> bool:
    """Keep a later process from repeating an inconclusive real submission."""
    return bool(record and str(record.get("status", "")).strip().lower() in _LOCAL_RETRY_BLOCKING_STATUSES)


def reservation_matches(text: str, day: str, room: str, seat: str, start: str, end: str) -> bool:
    normalized = " ".join(text.replace("：", ":").split())
    return all(value in normalized for value in (day, room, seat, start, end))


def find_matching_reservation(
    reservations: list[dict],
    day: str,
    room: str,
    seat: str,
    start: str,
    end: str,
    excluded: list[dict] | None = None,
) -> dict | None:
    """Find an active history record that confirms one submitted booking."""
    requested_start = _clock_minutes(start)
    requested_end = _clock_minutes(end)
    if requested_start is None or requested_end is None or requested_end <= requested_start:
        return None
    requested_room = _normalize_room(room)
    requested_seat = _normalize_seat(seat)
    excluded_keys = {_record_identity(item) for item in (excluded or []) if isinstance(item, dict)}
    for item in reservations or []:
        if not isinstance(item, dict) or _extract_date(item) != day or not _is_active_reservation(item):
            continue
        if _record_identity(item) in excluded_keys:
            continue
        existing_room = _extract_room(item)
        if existing_room and requested_room and not _room_matches(existing_room, requested_room):
            continue
        if not _seats_match(_extract_seat(item), requested_seat):
            continue
        existing_start = _extract_time(item, ("startTime", "start_time", "start", "beginTime", "begin"))
        existing_end = _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish"))
        if existing_start == requested_start and existing_end == requested_end:
            return item
    return None


def find_cancelable_reservation(
    reservations: list[dict],
    day: str,
    expected: dict,
) -> dict | None:
    """Find the one live booking safe to cancel.

    Monitoring may use a time-only fallback when the site changes its
    location presentation. Cancellation cannot use that fallback because a
    different seat can have the same interval. A saved seat is therefore
    required, and CHECK_IN remains a live state while the booking is in use.
    """
    expected_start = _clock_minutes(expected.get("start", ""))
    expected_end = _clock_minutes(expected.get("end", ""))
    expected_seat = _normalize_seat(_value_text(expected.get("seat", "")))
    expected_room = _normalize_room(expected.get("room", ""))
    if expected_end is None or not expected_seat:
        return None
    matches = []
    for item in _unique_matching_records(reservations):
        if not isinstance(item, dict) or _extract_date(item) != day:
            continue
        if reservation_state(item) not in {"reserved", "in_use"}:
            continue
        actual_start = _extract_time(item, ("startTime", "start_time", "start", "beginTime", "begin"))
        actual_end = _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish"))
        if actual_end != expected_end or (expected_start is not None and actual_start != expected_start):
            continue
        if not _seats_match(_extract_seat(item), expected_seat):
            continue
        # Seat number identifies the booking. The room text is a display
        # field and can change between the history and current endpoints.
        matches.append(item)
    return matches[0] if len(matches) == 1 else None


def find_confirmed_reservation(
    reservations: list[dict],
    day: str,
    expected: dict,
) -> dict | None:
    """Confirm a submitted booking without selecting another seat.

    This is used after reserve requests and during reconciliation. It may
    accept a unique time match when the user's endpoint omitted location
    fields, but it rejects a visible different seat. Destructive operations
    must use ``find_cancelable_reservation`` instead.
    """
    expected_start = _clock_minutes(expected.get("start", ""))
    expected_end = _clock_minutes(expected.get("end", ""))
    expected_seat = _normalize_seat(_value_text(expected.get("seat", "")))
    expected_room = _normalize_room(expected.get("room", ""))
    if expected_end is None:
        return None
    strict = find_cancelable_reservation(reservations, day, expected)
    if strict is not None:
        return strict

    matches = []
    for item in _unique_matching_records(reservations):
        if not isinstance(item, dict) or _extract_date(item) != day:
            continue
        if reservation_state(item) not in {"reserved", "in_use"}:
            continue
        actual_start = _extract_time(item, ("startTime", "start_time", "start", "beginTime", "begin"))
        actual_end = _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish"))
        if actual_end != expected_end or (expected_start is not None and actual_start != expected_start):
            continue
        actual_seat = _extract_seat(item)
        actual_room = _extract_room(item)
        if expected_seat and actual_seat and not _seats_match(actual_seat, expected_seat):
            continue
        if not expected_seat and expected_room and actual_room and not _room_matches(actual_room, expected_room):
            continue
        matches.append(item)
    return matches[0] if len(matches) == 1 else None


def find_reservation_by_day_and_time(
    reservations: list[dict],
    day: str,
    start: str,
    end: str,
    excluded: list[dict] | None = None,
) -> dict | None:
    """Find one active record with exact times but incomplete location fields.

    This is deliberately narrower than the normal verifier.  It is only safe
    as a post-submit fallback when the site omitted room or seat data, and
    exactly one active record has the submitted date and time.
    """
    requested_start = _clock_minutes(start)
    requested_end = _clock_minutes(end)
    if requested_start is None or requested_end is None or requested_end <= requested_start:
        return None
    excluded_keys = {_record_identity(item) for item in (excluded or []) if isinstance(item, dict)}
    matches = []
    for item in reservations or []:
        if not isinstance(item, dict) or _extract_date(item) != day or not _is_active_reservation(item):
            continue
        if _record_identity(item) in excluded_keys:
            continue
        existing_start = _extract_time(item, ("startTime", "start_time", "start", "beginTime", "begin"))
        existing_end = _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish"))
        if existing_start != requested_start or existing_end != requested_end:
            continue
        matches.append(item)
    if len(matches) != 1:
        return None
    match = matches[0]
    return match if not (_extract_room(match) and _extract_seat(match)) else None


def find_reservation_by_day_and_end(
    reservations: list[dict],
    day: str,
    end: str,
    excluded: list[dict] | None = None,
) -> dict | None:
    """Find the single new active record when the site resolves a current start."""
    requested_end = _clock_minutes(end)
    if requested_end is None:
        return None
    excluded_keys = {_record_identity(item) for item in (excluded or []) if isinstance(item, dict)}
    matches = []
    for item in reservations or []:
        if not isinstance(item, dict) or _extract_date(item) != day or not _is_active_reservation(item):
            continue
        if _record_identity(item) in excluded_keys:
            continue
        existing_end = _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish"))
        if existing_end == requested_end:
            matches.append(item)
    return matches[0] if len(matches) == 1 else None


def _record_identity(item: dict) -> str:
    identifier = _record_identifier(item)
    if identifier:
        return f"id:{identifier}"
    return "|".join(
        _value_text(item.get(key))
        for key in ("date", "begin", "end", "loc", "location", "seatNumber", "seatNo")
    )


def _record_identifier(item: dict) -> str:
    for key in ("id", "reservationId", "reserveId", "recordId"):
        value = item.get(key)
        if value not in (None, ""):
            return _value_text(value)
    return ""


def active_reservations_for_day(reservations: list[dict], day: str) -> list[dict]:
    """Return all active reservations for a day, regardless of requested time."""
    return [
        item for item in reservations or []
        if isinstance(item, dict) and _extract_date(item) == day and _is_active_reservation(item)
    ]


def blocking_active_reservations_for_day(
    reservations: list[dict], day: str, now: datetime
) -> list[dict]:
    """Return active records that still block the next serial reservation."""
    blocking = []
    for item in active_reservations_for_day(reservations, day):
        end_minutes = _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish"))
        if end_minutes is None:
            blocking.append(item)
            continue
        try:
            end_at = datetime.combine(
                datetime.fromisoformat(day).date(),
                _minutes_to_time(end_minutes),
            )
        except ValueError:
            blocking.append(item)
            continue
        if now < end_at:
            blocking.append(item)
    return blocking


def blocking_cancelable_reservations_for_day(
    reservations: list[dict], day: str, now: datetime
) -> list[dict]:
    """Return unfinished reservations that the site can still cancel.

    ``CHECK_IN`` is intentionally excluded from the scheduler's active-seat
    set, but it remains cancellable while the reservation is in progress.
    """
    blocking = []
    in_use_statuses = {status.upper() for status in _IN_USE_RESERVATION_STATUSES}
    for item in reservations or []:
        if not isinstance(item, dict) or _extract_date(item) != day:
            continue
        status = _reservation_status_value(item).strip().upper()
        if status not in _ACTIVE_RESERVATION_STATUSES and status not in in_use_statuses:
            continue
        end_minutes = _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish"))
        if end_minutes is None:
            blocking.append(item)
            continue
        try:
            end_at = datetime.combine(
                datetime.fromisoformat(day).date(),
                _minutes_to_time(end_minutes),
            )
        except ValueError:
            blocking.append(item)
            continue
        if now < end_at:
            blocking.append(item)
    return blocking


def active_reservation_interval(item: dict) -> tuple[int | None, int | None]:
    """Return the active record's start/end minutes for scheduler safety checks."""
    if not isinstance(item, dict) or not _is_active_reservation(item):
        return None, None
    return (
        _extract_time(item, ("startTime", "start_time", "start", "beginTime", "begin")),
        _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish")),
    )


def reservation_state(item: dict) -> str:
    """Normalize the site's reservation state without reusing seat-layout states."""
    value = _reservation_status_value(item)
    normalized = value.strip().upper()
    if normalized in {status.upper() for status in _IN_USE_RESERVATION_STATUSES}:
        return "in_use"
    if normalized in {status.upper() for status in _COMPLETED_RESERVATION_STATUSES}:
        return "completed"
    if normalized in {status.upper() for status in _MISSED_RESERVATION_STATUSES}:
        return "missed"
    if normalized in {status.upper() for status in _CANCELLED_RESERVATION_STATUSES}:
        return "cancelled"
    if normalized in _ACTIVE_RESERVATION_STATUSES:
        return "reserved"
    return "unknown"


def find_reservation_record(records: list[dict], day: str, expected: dict) -> dict | None:
    """Return the unique history row matching one local reservation."""
    records = _unique_matching_records(records)
    matches = [
        item for item in records or []
        if isinstance(item, dict)
        and _extract_date(item) == day
        and _reservation_matches_expected(item, expected)
    ]
    if len(matches) == 1:
        return matches[0]
    live_matches = [
        item for item in matches
        if reservation_state(item) in {"reserved", "in_use"}
    ]
    if len(live_matches) == 1:
        return live_matches[0]

    # A reloaded page can return a different room/seat presentation from the
    # one saved locally. The reservation endpoints are scoped to this
    # account, so a unique active row at the exact interval is our booking
    # even when the seat number is rendered differently; the monitor syncs
    # that display drift back to the local row instead of losing track of a
    # live reservation.
    expected_start = _clock_minutes(expected.get("start", ""))
    expected_end = _clock_minutes(expected.get("end", ""))
    interval_matches = []
    for item in records or []:
        if not isinstance(item, dict) or _extract_date(item) != day:
            continue
        if reservation_state(item) not in {"reserved", "in_use"}:
            continue
        actual_start = _extract_time(item, ("startTime", "start_time", "start", "beginTime", "begin"))
        actual_end = _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish"))
        if expected_end is None or actual_end != expected_end:
            continue
        if expected_start is not None and actual_start != expected_start:
            continue
        interval_matches.append(item)
    return interval_matches[0] if len(interval_matches) == 1 else None


def _unique_matching_records(records: list[dict] | None) -> list[dict]:
    """Collapse duplicate views of one site reservation before matching.

    The monitor may combine the paginated history response with the current
    reservations response.  Those responses can describe the same booking
    with different optional fields, so exact JSON de-duplication is not enough.
    """
    unique = []
    positions = {}
    for item in records or []:
        if not isinstance(item, dict):
            continue
        key = _matching_record_key(item)
        if key in positions:
            index = positions[key]
            first = unique[index]
            if reservation_state(first) != reservation_state(item):
                unique[index] = _merge_record_views(first, item)
            continue
        positions[key] = len(unique)
        unique.append(item)
    return unique


def _matching_record_key(item: dict) -> tuple:
    identifier = _record_identifier(item)
    if identifier:
        return ("id", identifier)
    return (
        "interval",
        _extract_date(item),
        _extract_time(item, ("startTime", "start_time", "start", "beginTime", "begin")),
        _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish")),
        _extract_room(item),
        _extract_seat(item),
    )


def _merge_record_views(first: dict, second: dict) -> dict:
    """Merge duplicate API views, keeping the most current business state."""
    first_state = reservation_state(first)
    second_state = reservation_state(second)
    priority = {
        "unknown": 0,
        "cancelled": 1,
        "missed": 2,
        "completed": 3,
        "reserved": 4,
        "in_use": 5,
    }
    preferred, other = (
        (second, first)
        if priority.get(second_state, 0) > priority.get(first_state, 0)
        else (first, second)
    )
    merged = dict(preferred)
    for key, value in other.items():
        if key in {"stat", "status", "state", "reservationStatus", "reserveStatus", "bookingStatus"}:
            continue
        if merged.get(key) in (None, "") and value not in (None, ""):
            merged[key] = value
    return merged


def find_reservation_state(records: list[dict], day: str, expected: dict) -> str | None:
    """Find the state of one user's reservation record by date/time/location.

    This remains separate from ``active_reservations_for_day``, which is used
    for seat-availability safety and must continue treating only ``RESERVE``
    as an active history reservation.
    """
    record = find_reservation_record(records, day, expected)
    return reservation_state(record) if record is not None else None


def day_reservations(reservations: list[dict], day: str) -> list[dict]:
    """Return every history record for a day, including inactive statuses."""
    return [
        item for item in reservations or []
        if isinstance(item, dict) and _extract_date(item) == day
    ]


def history_page_records(body: dict) -> tuple[list[dict], int | None]:
    """Extract history rows and the optional total from the site's page payload.

    The history endpoint has returned both ``data.records`` and an additional
    ``data.data.records`` wrapper across deployments.  Keep the transport
    parser tolerant of those wrappers while only accepting known row keys.
    """
    if not isinstance(body, dict):
        return [], None

    queue = [body.get("data", body)]
    seen: set[int] = set()
    total = None
    while queue:
        node = queue.pop(0)
        if isinstance(node, list):
            rows = [item for item in node if isinstance(item, dict)]
            if rows and len(rows) == len(node):
                return rows, total
            queue.extend(item for item in node if isinstance(item, (dict, list)))
            continue
        if not isinstance(node, dict) or id(node) in seen:
            continue
        seen.add(id(node))

        if total is None:
            for key in ("count", "total", "totalCount", "recordsTotal", "rowCount"):
                if key not in node or node[key] is None:
                    continue
                try:
                    total = int(node[key])
                except (TypeError, ValueError):
                    pass
                if total is not None:
                    break

        if _looks_like_reservation_record(node):
            return [node], total

        for key in (
            "records", "reservations", "rows", "list", "items", "content",
            "data", "result", "pageData", "dataList", "history", "reservationList",
        ):
            value = node.get(key)
            if isinstance(value, (dict, list)):
                queue.append(value)
    return [], total


def _looks_like_reservation_record(value: dict) -> bool:
    return any(key in value for key in (
        "date", "day", "onDate", "reservationDate", "reserveDate",
        "begin", "beginTime", "start", "startTime", "end", "endTime",
        "loc", "location", "seatNumber", "seatNo", "stat", "status",
    ))


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
    if "stat" in item:
        value = item.get("stat")
        if isinstance(value, dict):
            value = value.get("code") or value.get("name") or value.get("value") or value.get("status")
        return _value_text(value).strip().upper() == "RESERVE"
    for key in ("stat", "status", "state", "reservationStatus", "reserveStatus", "bookingStatus"):
        if key not in item:
            continue
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("code") or value.get("name") or value.get("value") or value.get("status")
        normalized = _value_text(value).strip().upper()
        return normalized in _ACTIVE_RESERVATION_STATUSES
    return False


def _reservation_status_value(item: dict) -> str:
    for key in ("stat", "status", "state", "reservationStatus", "reserveStatus", "bookingStatus"):
        if key not in item:
            continue
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("code") or value.get("name") or value.get("value") or value.get("status")
        return _value_text(value)
    return ""


def _reservation_matches_expected(item: dict, expected: dict) -> bool:
    expected_start = _clock_minutes(expected.get("start", ""))
    expected_end = _clock_minutes(expected.get("end", ""))
    actual_start = _extract_time(item, ("startTime", "start_time", "start", "beginTime", "begin"))
    actual_end = _extract_time(item, ("endTime", "end_time", "end", "finishTime", "finish"))
    if expected_end is not None and actual_end != expected_end:
        return False
    if expected_start is not None and actual_start is not None and actual_start != expected_start:
        return False
    expected_room = _normalize_room(expected.get("room", ""))
    actual_room = _extract_room(item)
    expected_seat = _normalize_seat(_value_text(expected.get("seat", "")))
    actual_seat = _extract_seat(item)
    if expected_seat:
        # Seat is the stable identity across the site's different location
        # presentations. An explicit seat mismatch must remain a mismatch.
        if actual_seat and not _seats_match(actual_seat, expected_seat):
            return False
    elif expected_room and actual_room and not _room_matches(actual_room, expected_room):
        return False
    return expected_end is not None and actual_end is not None


def _extract_date(item: dict) -> str | None:
    for key in ("date", "day", "onDate", "reservationDate", "reserveDate"):
        value = _value_text(item.get(key))
        match = _DATE_RE.search(value)
        if match:
            return _normalize_date(match.group(0))
    for key in ("startTime", "start_time", "start", "beginTime", "begin"):
        value = _value_text(item.get(key))
        match = _DATE_RE.search(value)
        if match:
            return _normalize_date(match.group(0))
    return None


def _normalize_date(value: str) -> str:
    year, month, day = (int(part) for part in value.split("-"))
    return date(year, month, day).isoformat()


def _extract_room(item: dict) -> str:
    for key in ("roomName", "room_name", "readingRoomName", "room", "readingRoom", "location", "loc"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("title") or value.get("text")
        normalized = _normalize_room(_value_text(value))
        if normalized:
            return normalized
    return ""


def _extract_seat(item: dict) -> str:
    for key in ("seatNumber", "seatNo", "seat", "seatName", "number"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("number") or value.get("name") or value.get("text")
        normalized = _normalize_seat(_value_text(value))
        if normalized:
            return normalized
    for key in ("location", "loc"):
        text = _value_text(item.get(key))
        match = re.search(r"(?:座位号|座位|seat)\s*([0-9A-Za-z-]+)", text, re.IGNORECASE)
        if match:
            return _normalize_seat(match.group(1))
        match = re.search(r"([0-9]+)\s*号\s*$", text)
        if match:
            return _normalize_seat(match.group(1))
    return ""


def _normalize_seat(value: str) -> str:
    return "".join(_value_text(value).split()).upper()


def _seats_match(existing: str, requested: str) -> bool:
    if not existing or not requested:
        return False
    if existing == requested:
        return True
    return existing.isdigit() and requested.isdigit() and int(existing) == int(requested)


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


def _minutes_to_time(value: int):
    return datetime.strptime(f"{value // 60:02d}:{value % 60:02d}", "%H:%M").time()


def _value_text(value) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_room(value: str) -> str:
    return "".join(_value_text(value).split())


def requested_times_available(options: list[str], requested: list[str]) -> bool:
    normalized = {normalize_time_option(value) for value in options}
    return all(normalize_time_option(value) in normalized for value in requested)


def normalize_time_option(value: str) -> str:
    normalized = "".join(value.replace("：", ":").split())
    if normalized.lower() in {"now", "current"} or normalized in {"当前", "现在"}:
        return "现在"
    if ":" not in normalized:
        return normalized
    hour, minute = normalized.split(":", 1)
    if hour.isdigit() and minute.isdigit():
        return f"{int(hour):02d}:{int(minute):02d}"
    return normalized


def time_options(response: dict, key: str) -> list[dict[str, str]]:
    options = []
    for item in _nested_option_values(response, key):
        if not isinstance(item, dict) or not item.get("value"):
            continue
        options.append({"id": str(item.get("id", "")), "value": normalize_time_option(str(item["value"]))})
    return options


def _nested_option_values(value, key: str) -> list:
    """Find an option array through the site's occasionally nested data wrappers."""
    if isinstance(value, dict):
        direct = value.get(key)
        if isinstance(direct, list):
            return direct
        for child in value.values():
            found = _nested_option_values(child, key)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _nested_option_values(child, key)
            if found:
                return found
    return []


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
    if normalized == "现在":
        return normalized
    hour, minute = map(int, normalized.split(":"))
    if minute not in (0, 30):
        raise ValueError(f"预约时间必须按30分钟设置：{value}")
    return normalized
