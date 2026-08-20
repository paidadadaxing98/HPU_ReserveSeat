from .seat_inventory import Seat, choose_seat


def normalize_room_name(value: str) -> str:
    return "".join(value.split())


def choose_preview_seat(seats: list[Seat], preferred: list[str]) -> Seat:
    selected = choose_seat(seats, preferred)
    if selected is None:
        raise ValueError("目标阅览室没有明确空闲座位")
    return selected


def preview_seat_candidates(seats: list[Seat], preferred: list[str]) -> list[Seat]:
    """Return free seats in preference order, followed by other free seats."""
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
