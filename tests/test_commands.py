from seat_assistant.commands import parse_command
import pytest
import asyncio
from pathlib import Path
from seat_assistant.storage import Repository
from scripts import preview_reservation as preview_module
from scripts.preview_reservation import ensure_initialized_account, library_switch_needed, parse_args
from scripts.initialize_account import parse_args as parse_initialize_args


def test_parse_direct_delay():
    command = parse_command("上午推迟到 09:20")
    assert command.kind == "delay"
    assert command.period == "morning"
    assert command.at == "09:20"


def test_parse_ask_delay():
    command = parse_command("下午推迟")
    assert (command.kind, command.period, command.at) == ("ask_delay", "afternoon", None)


def test_parse_default_change_and_cancel():
    assert parse_command("以后上午默认 09:05").kind == "set_default"
    assert parse_command("今天不去了").kind == "cancel_day"


def test_parse_invalid_clock_as_help_instead_of_creating_an_action():
    assert parse_command("上午推迟到 25:00").kind == "help"


def test_parse_arrival_record_command():
    command = parse_command("记录上午到馆 09:05")
    assert (command.kind, command.period, command.at) == ("record_arrival", "morning", "09:05")


def test_readme_has_quick_start_and_account_id_commands():
    text = Path("README.md").read_text(encoding="utf-8")

    assert "## 快速开始" in text
    assert "--account account02" in text
    assert "scripts/initialize_account.py" in text
    assert "新账号完整链路" in text
    assert "--account account03" in text
    assert "--submit" in text
    assert "座位偏好分为三条交互分支" in text
    assert "自动展开图书馆下拉框" in text


def test_preview_command_can_use_initialized_room_preference():
    args = parse_args([
        "--account", "account03",
        "--date", "2026-08-22",
        "--start", "08:30",
        "--end", "12:00",
        "--submit",
    ])

    assert args.room == ""


def test_initialize_command_accepts_seat_rules_and_three_time_windows():
    args = parse_initialize_args([
        "--account", "account03",
        "--seat", "2-x-x",
        "--seat", "2-9-109",
        "--time", "10:00-12:00", "x", "19:00-21:00",
    ])

    assert args.seat == ["2-x-x", "2-9-109"]
    assert args.time == ["10:00-12:00", "x", "19:00-21:00"]


def test_direct_reservation_requires_ready_initialization_for_accounts_file(tmp_path):
    settings = type("Settings", (), {"require_initialization": True, "account_id": "alice"})()
    repository = Repository(str(tmp_path / "alice.sqlite"), "alice")

    with pytest.raises(ValueError, match="请先初始化账号"):
        ensure_initialized_account(settings, repository)

    repository.save_initialization_state("ready", True, True, True, {"my_reservations": True}, "ok")
    ensure_initialized_account(settings, repository)


def test_multi_library_fallback_switches_back_to_the_actual_current_library():
    current = "南校区第一图书馆"
    switches = []
    for target in ("南校区第一图书馆", "南校区第二图书馆", "南校区第一图书馆"):
        if library_switch_needed(current, target):
            switches.append(target)
            current = target

    assert switches == ["南校区第二图书馆", "南校区第一图书馆"]


def test_runtime_catalog_collection_keeps_other_libraries_when_one_fails(monkeypatch):
    calls = []

    async def fake_select_library(page, library):
        calls.append(library)
        if library == "南校区第一图书馆":
            raise RuntimeError("第一图书馆暂时不可读")

    async def fake_visible_room_names(page):
        return ["4层计算机类借阅区"]

    monkeypatch.setattr(preview_module, "select_library", fake_select_library)
    monkeypatch.setattr(preview_module, "visible_room_names", fake_visible_room_names)

    class FakePage:
        async def wait_for_timeout(self, delay):
            return None

    rooms, errors = asyncio.run(preview_module.collect_rooms_by_library(
        FakePage(), ["南校区第一图书馆", "南校区第二图书馆"]
    ))

    assert calls == ["南校区第一图书馆", "南校区第二图书馆"]
    assert rooms == {
        "南校区第一图书馆": [],
        "南校区第二图书馆": ["4层计算机类借阅区"],
    }
    assert "南校区第一图书馆" in errors
