import pytest

from seat_assistant.preview import choose_room_for_preference, room_floor
from seat_assistant.seat_inventory import Seat, seats_from_layout


def test_room_floor_and_floor_preference_choose_a_room_on_requested_floor():
    rooms = ["3层自主学习空间（Ⅱ）", "4层计算机类借阅区", "4层普通阅览区", "5层自习区"]

    assert room_floor("4层计算机类借阅区") == "4F"
    assert choose_room_for_preference(rooms, {"mode": "floor", "floor": "4F"}) == "4层计算机类借阅区"


def test_library_only_preference_uses_a_stable_seeded_room_choice():
    rooms = ["1层综合阅览区", "4层计算机类借阅区", "5层自习区"]
    preference = {"library": "南校区第一图书馆", "floor": "", "room": ""}

    first = choose_room_for_preference(rooms, preference, seed="2026-08-21:morning")
    second = choose_room_for_preference(rooms, preference, seed="2026-08-21:morning")

    assert first == second
    assert first in rooms


def test_floor_without_room_delegates_to_persistent_round_robin():
    rooms = ["3层自主学习空间（Ⅱ）", "4层计算机类借阅区", "4层普通阅览区", "5层自习区"]
    calls = []

    def round_robin(floor, candidates):
        calls.append((floor, candidates))
        return candidates[1]

    selected = choose_room_for_preference(
        rooms,
        {"library": "南校区第二图书馆", "floor": "4F", "room": ""},
        round_robin=round_robin,
    )

    assert selected == "4层普通阅览区"
    assert calls == [("4F", ["4层计算机类借阅区", "4层普通阅览区"])]


def test_room_preference_requires_a_library():
    with pytest.raises(ValueError, match="图书馆"):
        choose_room_for_preference(["4层计算机类借阅区"], {"floor": "4F", "room": ""})


def test_structured_location_does_not_fall_back_to_another_room_when_catalog_is_empty():
    with pytest.raises(ValueError, match="阅览室"):
        choose_room_for_preference(
            [],
            {"library": "南校区第一图书馆", "floor": "", "room": ""},
            seed="2026-08-21:morning",
        )


def test_layout_seats_inherit_floor_from_room_name():
    layout = {
        "id": 34,
        "name": "4层计算机类借阅区",
        "layout": {"1": {"id": 169, "name": "169", "type": "seat", "status": "FREE", "enabled": True}},
    }

    assert seats_from_layout(layout)[0].floor == "4F"
