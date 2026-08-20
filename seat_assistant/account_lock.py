import os
from pathlib import Path


class AccountLock:
    """Small cross-platform exclusive lock for one account's local resources."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._reclaim_stale_lock()
        try:
            self._handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._handle, str(os.getpid()).encode("ascii"))
        except FileExistsError as exc:
            raise RuntimeError(f"账号正在被其他进程使用：{self.path.parent.name}") from exc
        return self

    def _reclaim_stale_lock(self):
        if not self.path.exists():
            return
        try:
            pid = int(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return
        if pid <= 0 or _pid_is_running(pid):
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __exit__(self, exc_type, exc_value, traceback):
        if self._handle is not None:
            os.close(self._handle)
            self._handle = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        return False


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        ctypes.windll.kernel32.CloseHandle(process)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
