import pytest

from seat_assistant.account_lock import AccountLock


def test_account_lock_blocks_second_process_and_releases_after_context(tmp_path):
    lock_path = tmp_path / "account.lock"
    with AccountLock(lock_path):
        with pytest.raises(RuntimeError, match="账号正在被其他进程使用"):
            with AccountLock(lock_path):
                pass
        assert lock_path.exists()
    assert not lock_path.exists()


def test_account_lock_creates_parent_directory(tmp_path):
    lock_path = tmp_path / "nested" / "account.lock"
    with AccountLock(lock_path):
        assert lock_path.exists()


def test_account_lock_reclaims_stale_pid_lock(tmp_path):
    lock_path = tmp_path / "account.lock"
    lock_path.write_text("4294967295", encoding="ascii")

    with AccountLock(lock_path):
        assert lock_path.read_text(encoding="ascii") != "4294967295"
