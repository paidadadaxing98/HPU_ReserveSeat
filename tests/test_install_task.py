from pathlib import Path


INSTALL_TASK = Path(__file__).parents[1] / "scripts" / "install-task.ps1"


def test_morning_task_has_a_next_day_fallback_trigger():
    script = INSTALL_TASK.read_text(encoding="utf-8")

    assert 'FallbackAt = "07:00"' in script
    assert '$item.FallbackAt' in script
