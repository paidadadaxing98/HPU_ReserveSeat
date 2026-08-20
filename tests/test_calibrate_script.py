import subprocess
import sys


def test_calibrate_script_can_import_project_package():
    result = subprocess.run(
        [sys.executable, "scripts/calibrate.py", "--check-imports"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
