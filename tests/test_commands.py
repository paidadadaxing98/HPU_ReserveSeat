from seat_assistant.commands import parse_command
from pathlib import Path


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
