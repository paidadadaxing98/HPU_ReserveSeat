from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Seat:
    number: str
    available: bool | None
    seat_id: int | None = None


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
    seats = []
    for item in layout.values():
        if item.get("type") != "seat" or not item.get("name"):
            continue
        status = str(item.get("status", "")).upper()
        seats.append(Seat(
            str(item["name"]),
            status == "FREE" and item.get("enabled", True) is True,
            item.get("id"),
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


def _state(class_name: str) -> bool | None:
    if any(marker in class_name for marker in BUSY_MARKERS):
        return False
    if any(marker in class_name for marker in FREE_MARKERS):
        return True
    return None
