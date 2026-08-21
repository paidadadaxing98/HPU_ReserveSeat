from dataclasses import dataclass
import re
import random


@dataclass(frozen=True)
class Seat:
    number: str
    available: bool | None
    seat_id: int | None = None
    floor: str = ""


FREE_MARKERS = ("seat-free", "free-seat", "is-free", "available")
BUSY_MARKERS = ("seat-reserved", "seat-busy", "occupied", "unavailable", "disabled")


def seats_from_snapshot(candidates: list[dict]) -> list[Seat]:
    by_number: dict[str, Seat] = {}
    for item in candidates:
        text = str(item.get("text", "")).strip()
        class_name = str(item.get("className", "")).lower()
        if not re.fullmatch(r"\d{1,5}", text) or "seat" not in class_name:
            continue
        state = _state(class_name)
        current = by_number.get(text)
        if current is None or (current.available is None and state is not None):
            by_number[text] = Seat(text, state)
    return sorted(by_number.values(), key=lambda seat: int(seat.number))


def seats_from_layout(layout_response: dict) -> list[Seat]:
    """Convert `/rest/v2/room/layoutByDate` data into safe seat records."""
    layout = layout_response.get("layout", {})
    floor = _floor_from_text(layout_response.get("name", ""))
    seats = []
    for item in layout.values():
        if item.get("type") != "seat" or not item.get("name"):
            continue
        status = str(item.get("status", "")).upper()
        seats.append(Seat(
            str(item["name"]),
            status == "FREE" and item.get("enabled", True) is True,
            item.get("id"),
            str(item.get("floor") or item.get("floorName") or item.get("floor_name") or floor).strip(),
        ))
    return sorted(seats, key=lambda seat: int(seat.number) if seat.number.isdigit() else seat.number)


def available_seats(seats: list[Seat]) -> list[Seat]:
    return [seat for seat in seats if seat.available is True]


def choose_seat(seats: list[Seat], preferred: list[str]) -> Seat | None:
    available = available_seats(seats)
    by_number = {seat.number: seat for seat in available}
    for number in preferred:
        if number in by_number:
            return by_number[number]
    return available[0] if available else None


def candidates_for_preference(
    seats: list[Seat],
    preference: dict | None,
    random_source=None,
    seed: str | int | None = None,
) -> list[Seat]:
    """Return free candidates according to persisted initialization preference."""
    available = available_seats(seats)
    preference = preference or {}
    mode = str(preference.get("mode", "")).strip().lower()
    if mode == "floor":
        floor = str(preference.get("floor", "")).strip()
        available = [seat for seat in available if _floor_matches(seat.floor, floor)]
    if mode == "seats":
        by_number = {seat.number: seat for seat in available}
        ordered = [by_number[number] for number in preference.get("seats", []) if number in by_number]
        if preference.get("strict"):
            return ordered
        selected = {seat.number for seat in ordered}
        ordered.extend(seat for seat in available if seat.number not in selected)
        return ordered
    if mode == "random":
        result = list(available)
        source = random_source
        if source is None and seed is not None:
            source = random.Random(str(seed))
        (source or random).shuffle(result)
        return result
    return available


def _floor_matches(actual: str, requested: str) -> bool:
    actual = re.sub(r"\s+", "", str(actual or "")).lower()
    requested = re.sub(r"\s+", "", str(requested or "")).lower()
    if not actual or not requested:
        return False
    return actual == requested or actual.rstrip("f层楼") == requested.rstrip("f层楼")


def _floor_from_text(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    match = re.search(r"(\d+)\s*(?:层|楼|F|f)", text)
    if match:
        return f"{match.group(1)}F"
    chinese = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7"}
    match = re.search(r"([一二三四五六七])(?:层|楼)", text)
    return f"{chinese[match.group(1)]}F" if match else ""


def _state(class_name: str) -> bool | None:
    if any(marker in class_name for marker in BUSY_MARKERS):
        return False
    if any(marker in class_name for marker in FREE_MARKERS):
        return True
    return None
