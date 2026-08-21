import re
import random

from .seat_inventory import Seat, candidates_for_preference, choose_seat


def normalize_room_name(value: str) -> str:
    return "".join(value.split())


def selection_seed(account_id: str, day: str, period: str, start: str, end: str, scope: str = "") -> str:
    """Build a stable per-request seed for room and seat randomization."""
    return "|".join(str(item or "") for item in (account_id, day, period, start, end, scope))


def room_preference_candidates(
    rules: list[dict] | None,
    libraries: list[str],
    rooms_by_library: dict[str, list[str]],
    seed: str | int | None = None,
) -> list[dict]:
    """Expand ordered seat rules into runtime library/room candidates.

    Numeric components use the catalogs' one-based indexes. A wildcard room
    expands to all rooms in stable random order so an unavailable room can be
    skipped without changing the order for the same request.
    """
    available_libraries = [str(item).strip() for item in libraries if str(item).strip()]
    ordered_rules = sorted(
        enumerate(rules or []),
        key=lambda pair: (-_rule_precision(pair[1]), pair[0]),
    )
    candidates = []
    seen = set()
    first_error = None
    for rule_number, rule in ordered_rules:
        if not isinstance(rule, dict):
            first_error = first_error or ValueError("座位规则必须是对象")
            continue
        try:
            library = _resolve_library_value(rule, available_libraries)
        except ValueError as exc:
            first_error = first_error or exc
            continue
        rooms = [str(item).strip() for item in rooms_by_library.get(library, []) if str(item).strip()]
        if not rooms:
            first_error = first_error or ValueError(f"图书馆‘{library}’当前没有可用阅览室")
            continue
        room_index = _stored_index(rule.get("room_index"))
        room_value = str(rule.get("room", "x") or "x").strip()
        if room_value.lower() in {"", "x"}:
            room_candidates = list(rooms)
            random.Random(_candidate_seed(seed, rule_number, library)).shuffle(room_candidates)
        elif room_index is not None or room_value.isdigit():
            room_index = room_index or int(room_value)
            if not 1 <= room_index <= len(rooms):
                first_error = first_error or ValueError(
                    f"图书馆‘{library}’的阅览室编号无效：{room_index}；请输入 1-{len(rooms)}"
                )
                continue
            room_candidates = [rooms[room_index - 1]]
        else:
            room_candidates = [
                room for room in rooms
                if normalize_room_name(room) == normalize_room_name(room_value)
            ]
            if not room_candidates:
                first_error = first_error or ValueError(f"没有找到指定阅览室：{room_value}")
                continue
        seat_value = str(rule.get("seat", "x") or "x").strip()
        if seat_value.lower() in {"", "x"}:
            preference = {"mode": "random"}
        else:
            preference = {"mode": "seats", "seats": [seat_value], "strict": True}
        for room in room_candidates:
            key = (
                normalize_library_name(library),
                normalize_room_name(room),
                tuple(preference.get("seats", ())),
                preference.get("mode"),
                bool(preference.get("strict")),
            )
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "library": library,
                "room": room,
                "preference": dict(preference),
                "rule": dict(rule),
            })
    if not candidates:
        if first_error is not None:
            raise first_error
        raise ValueError("没有可用的座位规则")
    return candidates


def _rule_precision(rule: dict) -> int:
    return sum(str(rule.get(key, "x") or "x").lower() != "x" for key in ("library", "room", "seat"))


def _candidate_seed(seed: str | int | None, rule_number: int, library: str) -> str:
    return f"{seed or ''}|rule={rule_number}|library={library}"


def normalize_library_name(value: str) -> str:
    return "".join(str(value or "").split())


def _resolve_library_value(rule: dict, libraries: list[str]) -> str:
    value = str(rule.get("library", "")).strip()
    if not value:
        raise ValueError("座位规则必须指定图书馆")
    index = _stored_index(rule.get("library_index"))
    if index is not None or value.isdigit():
        index = index or int(value)
        if not 1 <= index <= len(libraries):
            raise ValueError(f"图书馆编号无效：{index}；请输入 1-{len(libraries)}")
        return libraries[index - 1]
    target = normalize_library_name(value)
    for library in libraries:
        if normalize_library_name(library) == target:
            return library
    raise ValueError(f"没有找到指定图书馆：{value}")


def _stored_index(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def room_floor(value: str) -> str:
    text = normalize_room_name(value)
    match = re.search(r"(\d+)(?:层|楼|F|f)", text)
    if match:
        return f"{match.group(1)}F"
    chinese = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7"}
    match = re.search(r"([一二三四五六七])(?:层|楼)", text)
    return f"{chinese[match.group(1)]}F" if match else ""


def choose_room_for_preference(
    room_names: list[str],
    preference: dict | None,
    fallback: str = "4层计算机类借阅区",
    seed: str | int | None = None,
    round_robin=None,
) -> str:
    preference = preference or {}
    structured = any(key in preference for key in ("library", "room")) or (
        "floor" in preference and "mode" not in preference
    )
    if structured and not str(preference.get("library", "")).strip():
        raise ValueError("位置偏好必须选择图书馆")

    requested_room = str(preference.get("room", "")).strip()
    if requested_room:
        for name in room_names:
            if normalize_room_name(name) == normalize_room_name(requested_room):
                return name
        raise ValueError(f"没有找到指定阅览室：{requested_room}")

    requested_floor = ""
    if preference.get("mode") == "floor" or structured:
        requested_floor = str(preference.get("floor", "")).strip()
    if requested_floor:
        target = requested_floor.lower().rstrip("f层楼")
        candidates = [
            name for name in room_names
            if room_floor(name).lower().rstrip("f层楼") == target
        ]
        if not candidates:
            raise ValueError(f"没有找到楼层 {requested_floor} 对应的阅览室")
        if round_robin is not None:
            selected = round_robin(requested_floor, candidates)
            if selected not in candidates:
                raise ValueError("阅览室轮询选择器返回了不在当前楼层的阅览室")
            return selected
        return candidates[0]

    if structured and not room_names:
        raise ValueError(f"图书馆 {preference.get('library')} 当前没有可用阅览室")
    if seed is not None and room_names:
        return random.Random(str(seed)).choice(list(room_names))
    if not structured and fallback and any(normalize_room_name(name) == normalize_room_name(fallback) for name in room_names):
        return next(name for name in room_names if normalize_room_name(name) == normalize_room_name(fallback))
    return room_names[0] if room_names else fallback


def choose_preview_seat(seats: list[Seat], preferred: list[str]) -> Seat:
    selected = choose_seat(seats, preferred)
    if selected is None:
        raise ValueError("目标阅览室没有明确空闲座位")
    return selected


def preview_seat_candidates(
    seats: list[Seat],
    preferred: list[str] | dict | None = None,
    preference: dict | None = None,
    seed: str | int | None = None,
) -> list[Seat]:
    """Return free seats in preference order, followed by other free seats."""
    if isinstance(preferred, dict) and preference is None:
        preference = preferred
        preferred = preference.get("seats", []) if preference.get("mode") == "seats" else []
    if preference:
        return candidates_for_preference(seats, preference, seed=seed)
    preferred = preferred or []
    available = [seat for seat in seats if seat.available is True]
    by_number = {seat.number: seat for seat in available}
    ordered = [by_number[number] for number in preferred if number in by_number]
    selected_numbers = {seat.number for seat in ordered}
    ordered.extend(seat for seat in available if seat.number not in selected_numbers)
    return ordered


def first_time_compatible_seat(
    seats: list[Seat],
    preferred: list[str],
    time_options: dict[str, tuple[list[str], list[str]]],
    start: str,
    end: str,
) -> Seat | None:
    """Pick the first free seat whose server options contain both times."""
    for seat in preview_seat_candidates(seats, preferred):
        available_start, available_end = time_options.get(seat.number, ([], []))
        if start in available_start and end in available_end:
            return seat
    return None




def layout_from_response(response: dict) -> dict:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("layout"), dict):
        detail = response.get("message") or response.get("code") or "接口没有返回座位布局"
        raise ValueError(f"无法读取座位布局：{detail}。请核对阅览室 ID、日期和登录状态。")
    return data


def layout_request_matches(url: str, day: str | None = None) -> bool:
    if "/rest/v2/room/layoutByDate/" not in url:
        return False
    return day is None or url.rstrip("/").endswith(day)
