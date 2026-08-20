from seat_assistant.preview import choose_preview_seat, first_time_compatible_seat, layout_request_matches, normalize_room_name, preview_seat_candidates
from seat_assistant.seat_inventory import Seat


def test_room_name_normalization():
    assert normalize_room_name(" 4层 计算机类借阅区 ") == "4层计算机类借阅区"


def test_preview_chooses_preferred_free_seat():
    seats = [Seat("001", True, 10), Seat("169", True, 11), Seat("170", False, 12)]
    selected = choose_preview_seat(seats, ["170", "169"])
    assert selected.number == "169"


def test_preview_keeps_trying_when_first_free_seat_cannot_cover_requested_end():
    seats = [Seat("169", True, 11), Seat("168", True, 12), Seat("170", False, 13)]
    options = {
        "169": (["09:00"], ["09:30", "10:00"]),
        "168": (["09:00"], ["09:30", "12:00"]),
    }
    selected = first_time_compatible_seat(seats, ["169", "168", "170"], options, "09:00", "12:00")
    assert selected is not None
    assert selected.number == "168"


def test_preview_candidates_preserve_preference_then_add_other_free_seats():
    seats = [Seat("001", True, 10), Seat("169", True, 11), Seat("168", True, 12), Seat("170", False, 13)]
    assert [seat.number for seat in preview_seat_candidates(seats, ["168", "170"])] == ["168", "001", "169"]


def test_layout_request_matches_room_layout_response():
    assert layout_request_matches("https://seatlib.hpu.edu.cn/rest/v2/room/layoutByDate/34/2026-08-20", "2026-08-20")
    assert layout_request_matches("https://seatlib.hpu.edu.cn/rest/v2/room/layoutByDate/34/2026-08-19")
    assert not layout_request_matches("https://seatlib.hpu.edu.cn/rest/v2/user", "2026-08-20")
