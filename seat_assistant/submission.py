def confirmation_required(submit_flag: bool, phrase: str) -> bool:
    return not submit_flag or phrase.strip() != "SUBMIT"


def reservation_matches(text: str, day: str, room: str, seat: str, start: str, end: str) -> bool:
    normalized = " ".join(text.replace("：", ":").split())
    return all(value in normalized for value in (day, room, seat, start, end))


def submission_settled(text: str) -> bool:
    return "正在玩命预约中" not in text and "玩命预约" not in text


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
