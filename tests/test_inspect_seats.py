import subprocess
import sys


def test_inspection_reports_unknown_states_without_submitting():
    result = subprocess.run([sys.executable, "scripts/inspect_seats.py"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "接口明确空闲" in result.stdout
    assert "不会自动选座或提交预约" not in result.stdout
