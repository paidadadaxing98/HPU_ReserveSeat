from pathlib import Path


INSTALL_TASK = Path(__file__).parents[1] / "scripts" / "install-task.ps1"


def test_morning_task_has_a_next_day_fallback_trigger():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert 'FallbackAt = "07:00"' in script
    assert '$item.FallbackAt' in script


def test_installer_registers_hidden_time_limited_bot_tasks():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert 'SeatAssistant-Bot-Morning' in script
    assert 'SeatAssistant-Bot-Morning-Fallback' in script
    assert 'SeatAssistant-Bot-Afternoon' in script
    assert 'SeatAssistant-Bot-Evening' in script
    assert 'scripts.run_wecom_bot --run-for-minutes' in script
    assert '-RestartCount 5' in script
    assert '-RestartInterval (New-TimeSpan -Minutes 1)' in script
