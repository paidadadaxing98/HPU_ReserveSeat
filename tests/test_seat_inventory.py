from seat_assistant.seat_inventory import Seat, available_seats, choose_seat, seats_from_layout, seats_from_snapshot


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


def test_layout_response_uses_server_status_and_seat_id():
    layout = {"id": 28, "name": "阅览室", "layout": {
        "1": {"type": "seat", "id": 10, "name": "028", "status": "FREE", "enabled": True},
        "2": {"type": "seat", "id": 11, "name": "029", "status": "IN_USE", "enabled": True},
        "3": {"type": "seat", "id": 12, "name": "030", "status": "AWAY", "enabled": True},
        "4": {"type": "seat", "id": 13, "name": "031", "status": "FREE", "enabled": False},
    }}
    assert seats_from_layout(layout) == [Seat("028", True, 10), Seat("029", False, 11), Seat("030", False, 12), Seat("031", False, 13)]
