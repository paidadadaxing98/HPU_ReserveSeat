from seat_assistant.initialization import parse_seat_rule
from seat_assistant.preview import (
    choose_preview_seat,
    first_time_compatible_seat,
    layout_request_matches,
    normalize_room_name,
    preview_seat_candidates,
    room_preference_candidates,
)
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


def test_seat_rules_resolve_library_room_and_seat_in_precision_order():
    candidates = room_preference_candidates(
        [parse_seat_rule("2-x-x"), parse_seat_rule("2-2-109")],
        ["南校区第一图书馆", "南校区第二图书馆"],
        {"南校区第二图书馆": ["4层工程技术类借阅区", "5层工程技术类借阅区"]},
        seed="alice|2026-08-22|morning|08:30|12:00",
    )

    assert candidates[0]["library"] == "南校区第二图书馆"
    assert candidates[0]["room"] == "5层工程技术类借阅区"
    assert candidates[0]["preference"] == {"mode": "seats", "seats": ["109"], "strict": True}
    assert {candidate["room"] for candidate in candidates[1:]} == {
        "4层工程技术类借阅区",
        "5层工程技术类借阅区",
    }
    assert all(candidate["preference"] == {"mode": "random"} for candidate in candidates[1:])


def test_seat_rules_accept_resolved_human_names_and_numeric_room_index():
    candidates = room_preference_candidates(
        [{
            "library": "南校区第二图书馆",
            "room": "",
            "seat": "",
            "library_index": 2,
            "room_index": None,
        }, {
            "library": "南校区第二图书馆",
            "room": "5层工程技术类借阅区",
            "seat": "109",
            "library_index": 2,
            "room_index": 2,
        }],
        ["南校区第一图书馆", "南校区第二图书馆"],
        {"南校区第二图书馆": ["4层工程技术类借阅区", "5层工程技术类借阅区"]},
        seed="fixed",
    )

    assert candidates[0]["room"] == "5层工程技术类借阅区"
    assert candidates[0]["preference"] == {"mode": "seats", "seats": ["109"], "strict": True}


def test_seat_rules_use_saved_catalog_indexes_when_room_names_change():
    candidates = room_preference_candidates(
        [{
            "library": "旧的第二图书馆名称",
            "room": "旧的阅览室名称",
            "seat": "x",
            "library_index": 2,
            "room_index": 2,
        }],
        ["南校区第一图书馆", "南校区第二图书馆"],
        {"南校区第二图书馆": ["新的一号阅览室", "新的一号目标阅览室"]},
        seed="fixed",
    )

    assert candidates[0]["library"] == "南校区第二图书馆"
    assert candidates[0]["room"] == "新的一号目标阅览室"


def test_seat_rules_report_when_no_referenced_library_or_room_exists():
    try:
        room_preference_candidates(
            [parse_seat_rule("2-9-x")],
            ["南校区第一图书馆"],
            {"南校区第一图书馆": ["4层自习室"]},
            seed="fixed",
        )
    except ValueError as exc:
        assert "图书馆编号" in str(exc)
    else:
        raise AssertionError("missing library must be rejected")


def test_seat_rules_skip_stale_rule_and_fallback_to_next_rule():
    candidates = room_preference_candidates(
        [parse_seat_rule("2-9-109"), parse_seat_rule("1-x-x")],
        ["南校区第一图书馆"],
        {"南校区第一图书馆": ["4层自习室"]},
        seed="fixed",
    )

    assert len(candidates) == 1
    assert candidates[0]["library"] == "南校区第一图书馆"
