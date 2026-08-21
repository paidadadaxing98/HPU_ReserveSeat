import asyncio

import pytest

from seat_assistant.config import Settings, load_account_settings
from seat_assistant.initialization import (
    initialization_status,
    parse_period_arguments,
    seat_preference_from_input,
    location_preference_from_input,
    choose_library_from_input,
    location_preference_from_payload,
    initialization_summary,
)
from seat_assistant.storage import Repository


def test_default_periods_use_the_three_learning_windows():
    settings = Settings(control_token="local-token")

    assert settings.periods["morning"].arrival_window == ("08:00", "12:00")
    assert settings.periods["afternoon"].arrival_window == ("14:30", "18:30")
    assert settings.periods["evening"].arrival_window == ("19:30", "22:00")


def test_account_settings_loads_floor_preference(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text(
        '{"accounts":[{"id":"alice","account":"1001","password":"secret",'
        '"initialization":{"seat_preference":{"mode":"floor","floor":"4"}}}]}',
        encoding="utf-8",
    )

    settings = load_account_settings("alice")

    assert settings.seat_preference == {"mode": "floor", "floor": "4"}


def test_old_preferred_seats_are_loaded_as_seat_preference(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text(
        '{"accounts":[{"id":"alice","account":"1001","password":"secret",'
        '"initialization":{"preferred_seats":["169","168"]}}]}',
        encoding="utf-8",
    )

    assert load_account_settings("alice").seat_preference == {
        "mode": "seats", "seats": ["169", "168"]
    }


def test_repository_initialization_state_defaults_to_pending(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"), account_id="alice")

    assert repo.initialization_state()["status"] == "pending"
    assert repo.initialization_state()["account_id"] == "alice"


def test_repository_saves_ready_initialization_with_capabilities(tmp_path):
    repo = Repository(str(tmp_path / "assistant.sqlite"), account_id="alice")
    repo.save_initialization_state(
        status="ready",
        login_verified=True,
        home_verified=True,
        my_reservations_verified=True,
        capabilities={"my_reservations": True, "history": True},
        message="初始化验证成功",
    )

    state = repo.initialization_state()
    assert state["status"] == "ready"
    assert state["capabilities"] == {"my_reservations": True, "history": True}
    assert state["last_verified_at"]


def test_ready_requires_all_verification_flags():
    assert initialization_status(False, True, True) == "failed"
    assert initialization_status(True, True, True) == "ready"


def test_parse_period_arguments_accepts_partial_overrides():
    assert parse_period_arguments(["morning=08:00-12:00"]) == {
        "morning": ("08:00", "12:00")
    }


def test_seat_preference_input_supports_random_floor_and_seat_list():
    assert seat_preference_from_input("random") == {"mode": "random"}
    assert seat_preference_from_input("floor", "4F") == {"mode": "floor", "floor": "4F"}
    assert seat_preference_from_input("seats", "169 168") == {
        "mode": "seats", "seats": ["169", "168"]
    }


def test_location_preference_requires_library_and_allows_empty_scope():
    assert location_preference_from_input("老图") == {
        "library": "老图", "floor": "", "room": ""
    }
    assert location_preference_from_input("新图", "4F", "4层计算机类阅览室") == {
        "library": "新图", "floor": "4F", "room": "4层计算机类阅览室"
    }

    with pytest.raises(ValueError, match="图书馆"):
        location_preference_from_input("")


def test_choose_library_from_input_requires_an_available_library():
    assert choose_library_from_input(["老图", "新图"], "新图") == "新图"
    with pytest.raises(ValueError, match="图书馆"):
        choose_library_from_input(["老图", "新图"], "")
    with pytest.raises(ValueError, match="未找到"):
        choose_library_from_input(["老图", "新图"], "北图")


def test_location_preference_from_payload_is_the_robot_boundary():
    assert location_preference_from_payload({
        "library": "新图",
        "floor": "4F",
        "room": "计算机类阅览室",
    }) == {
        "library": "新图",
        "floor": "4F",
        "room": "计算机类阅览室",
    }
    with pytest.raises(ValueError, match="图书馆"):
        location_preference_from_payload({"floor": "4F"})


def test_initialization_summary_contains_location_windows_and_seat_mode():
    summary = initialization_summary(
        "account02",
        {"library": "新图", "floor": "4F", "room": ""},
        {"mode": "random"},
        {"morning": ("08:00", "12:00")},
    )

    assert "account02" in summary
    assert "新图 / 4F / 自动分配阅览室" in summary
    assert "morning=08:00-12:00" in summary
    assert "随机空闲座位" in summary
