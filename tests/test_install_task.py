from pathlib import Path


INSTALL_TASK = Path(__file__).parents[1] / "scripts" / "install-task.ps1"


def test_morning_task_has_a_next_day_fallback_trigger():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert 'FallbackAt = "07:00"' in script
    assert '$item.FallbackAt' in script


def test_installer_registers_dynamic_monitor_tasks_with_full_window_runtime():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert 'SeatAssistant-Dynamic-Morning' in script
    assert 'scripts.dynamic_task_schedule' in script
    assert 'scripts.run_dynamic_monitor --period' in script
    assert '--run-for-minutes' in script


def test_installer_no_longer_registers_independent_bot_tasks():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert 'Register-ScheduledTask' in script
    assert '$botDefinitions' not in script
    assert 'SeatAssistant-Bot-Morning' in script
