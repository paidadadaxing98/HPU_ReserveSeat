from seat_assistant.seat_inventory import Seat, available_seats, candidates_for_preference, choose_seat, normalize_seat_number, seats_from_layout, seats_from_snapshot


def test_extracts_number_and_state_from_candidates():
    candidates = [
        {"tag": "DIV", "text": "169", "className": "seat-desk seat-left seat-free", "data": {}},
        {"tag": "DIV", "text": "170", "className": "seat-desk seat-left seat-reserved", "data": {}},
        {"tag": "DIV", "text": "171", "className": "seat-desk seat-left", "data": {}},
    ]
    seats = seats_from_snapshot(candidates)
    assert seats == [Seat("169", True), Seat("170", False), Seat("171", None)]


def test_unknown_states_are_not_available():
    seats = [Seat("169", None), Seat("170", True), Seat("171", False)]
    assert available_seats(seats) == [Seat("170", True)]


def test_choose_seat_prefers_configured_order_then_room_fallback():
    seats = [Seat("170", True), Seat("169", True), Seat("171", True)]
    assert choose_seat(seats, ["169", "999"]).number == "169"


def test_seat_numbers_match_by_number_even_when_page_pads_with_zeroes():
    seats = [Seat("001", True), Seat("023", True), Seat("085", True)]

    assert normalize_seat_number("23") == "23"
    assert normalize_seat_number("023") == "23"
    assert choose_seat(seats, ["23"]).number == "023"
    assert [seat.number for seat in candidates_for_preference(
        seats,
        {"mode": "seats", "seats": ["23"], "strict": True},
    )] == ["023"]


def test_random_seat_candidates_are_stable_for_the_same_time_seed():
    seats = [Seat("169", True), Seat("168", True), Seat("170", True)]

    first = candidates_for_preference(seats, {"mode": "random"}, seed="2026-08-21:morning")
    second = candidates_for_preference(seats, {"mode": "random"}, seed="2026-08-21:morning")

    assert first == second
    assert {seat.number for seat in first} == {"168", "169", "170"}


def test_strict_specific_seat_does_not_fall_back_inside_the_same_rule():
    seats = [Seat("169", True, 169), Seat("170", True, 170)]

    assert candidates_for_preference(
        seats,
        {"mode": "seats", "seats": ["168"], "strict": True},
    ) == []


def test_layout_response_uses_server_status_and_seat_id():
    layout = {"id": 28, "name": "阅览室", "layout": {
        "1": {"type": "seat", "id": 10, "name": "028", "status": "FREE", "enabled": True},
        "2": {"type": "seat", "id": 11, "name": "029", "status": "IN_USE", "enabled": True},
        "3": {"type": "seat", "id": 12, "name": "030", "status": "AWAY", "enabled": True},
        "4": {"type": "seat", "id": 13, "name": "031", "status": "FREE", "enabled": False},
    }}
    assert seats_from_layout(layout) == [Seat("028", True, 10), Seat("029", False, 11), Seat("030", False, 12), Seat("031", False, 13)]
