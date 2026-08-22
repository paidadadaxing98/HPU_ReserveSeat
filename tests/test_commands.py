from seat_assistant.commands import parse_command
import pytest
import asyncio
from pathlib import Path
from seat_assistant.storage import Repository
from scripts import preview_reservation as preview_module
from scripts.preview_reservation import click_library_option, ensure_initialized_account, library_control_selectors, library_switch_needed, parse_args, refresh_login_captcha, wait_for_authenticated_page
from scripts.initialize_account import parse_args as parse_initialize_args, print_catalog


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
    assert "scripts/initialize_account.py" in text
    assert "--account account03" in text
    assert "--submit" in text
    assert "选择座位策略" in text
    assert "自动采集图书馆列表" in text
    assert "install-task.ps1 -DryRun" in text


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


def test_catalog_output_numbers_rooms_for_every_library(capsys):
    print_catalog(
        ["第一图书馆", "第二图书馆", "北校区图书馆"],
        {
            "第一图书馆": ["一层自习室", "二层自习室"],
            "第二图书馆": ["计算机类阅览区"],
            "北校区图书馆": ["北区阅览室"],
        },
    )

    output = capsys.readouterr().out
    assert "图书馆 1. 第一图书馆" in output
    assert "  2. 二层自习室" in output
    assert "图书馆 2. 第二图书馆" in output
    assert "图书馆 3. 北校区图书馆" in output
    assert "  1. 北区阅览室" in output


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


def test_library_dropdown_controls_include_element_ui_input_and_arrow_paths():
    selectors = library_control_selectors()

    assert "input.el-input__inner" in selectors
    assert ".el-select__caret" in selectors
    assert ".el-input__suffix" in selectors


def test_library_option_click_falls_back_to_dom_for_dynamic_element_ui_item():
    class Option:
        def __init__(self, text):
            self.text = text
            self.click_attempts = 0
            self.dom_clicked = False

        async def inner_text(self):
            return self.text

        async def click(self, **kwargs):
            self.click_attempts += 1
            raise TimeoutError("element is not stable")

        async def evaluate(self, script):
            self.dom_clicked = True

    class Options:
        def __init__(self):
            self.items = [Option("南校区第一图书馆"), Option("南校区第二图书馆")]

        async def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    options = Options()
    asyncio.run(click_library_option(object(), options, "南校区第二图书馆"))

    assert options.items[1].click_attempts == 1
    assert options.items[1].dom_clicked is True


def test_authenticated_wait_rejects_captcha_failure_even_on_seat_home_url():
    class Body:
        async def inner_text(self):
            return "登录页面提示：验证码有误，请重新输入"

    class Page:
        url = "https://seatlib.hpu.edu.cn/libseat/#/home"

        def locator(self, selector):
            return Body()

        async def wait_for_timeout(self, delay):
            raise AssertionError("captcha failure should be reported immediately")

    with pytest.raises(RuntimeError, match="验证码有误"):
        asyncio.run(wait_for_authenticated_page(Page(), timeout_ms=1000))


def test_captcha_refresh_reloads_login_page_to_clear_stale_failure_message():
    class Image:
        @property
        def first(self):
            return self

        async def count(self):
            return 1

        async def is_visible(self):
            return True

        async def click(self, **kwargs):
            raise AssertionError("a reload should be preferred over clicking a stale captcha")

    class Page:
        def __init__(self):
            self.reloaded = False

        def locator(self, selector):
            return Image()

        async def reload(self, wait_until):
            self.reloaded = True

        async def wait_for_timeout(self, delay):
            return None

    page = Page()
    asyncio.run(refresh_login_captcha(page))
    assert page.reloaded is True


def test_captcha_refresh_can_return_to_configured_login_url():
    class Page:
        def __init__(self):
            self.url = "https://seatlib.hpu.edu.cn/libseat/#/home"
            self.visited = ""

        async def goto(self, url, wait_until):
            self.visited = url

        async def wait_for_timeout(self, delay):
            return None

    page = Page()
    asyncio.run(refresh_login_captcha(page, "https://seatlib.hpu.edu.cn/libseat/"))
    assert page.visited == "https://seatlib.hpu.edu.cn/libseat/"


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
