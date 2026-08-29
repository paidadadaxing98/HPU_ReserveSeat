from pathlib import Path


INSTALL_TASK = Path(__file__).parents[1] / "scripts" / "install-task.ps1"


def test_morning_task_uses_the_new_two_trigger_schedule():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert 'scripts.reservation_task_schedule' in script
    assert '$schedule.triggers' in script


def test_installer_uses_computed_triggers_and_skips_disabled_periods():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert '$schedule.triggers' in script
    assert 'if (-not $schedule.enabled)' in script
    assert 'scripts.run_scheduled_task --period' in script


def test_installer_keeps_ten_minute_default_for_booking_retries():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert '[int]$RepeatMinutes = 10' in script


def test_installer_registers_dynamic_monitor_tasks_with_full_window_runtime():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert 'SeatAssistant-Dynamic-Morning' in script
    assert 'scripts.dynamic_task_schedule' in script
    assert 'scripts.run_dynamic_monitor --period' in script
    assert '--run-for-minutes' in script


def test_installer_does_not_restore_legacy_per_period_bot_tasks():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert 'Register-ScheduledTask' in script
    assert '$botDefinitions' not in script
    assert 'SeatAssistant-Bot-Morning' in script


def test_installer_registers_daytime_bot_for_reservation_notifications():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert 'SeatAssistant-Bot-Daily' in script
    assert '$botDailyAt = "07:00"' in script
    assert '$botDailyDuration = 921' in script
    assert 'scripts.run_wecom_bot --run-for-minutes $botDailyDuration' in script
