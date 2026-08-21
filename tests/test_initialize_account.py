import asyncio

from seat_assistant.config import Settings
from seat_assistant.initialization import run_interactive_initialization


def test_initialize_workflow_persists_preferences_without_booking_calls(tmp_path):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(
        '{"accounts":[{"id":"alice","enabled":true,"account":"1001",'
        '"password":"secret"}]}',
        encoding="utf-8",
    )

    class FakeVerifier:
        async def verify(self):
            return {"home": True, "my_reservations": True, "capabilities": {"history": True}}

    calls = []

    class ForbiddenBookingAdapter:
        def reserve(self, *args):
            calls.append("reserve")
            raise AssertionError("初始化不得预约")

        def cancel(self, *args):
            calls.append("cancel")
            raise AssertionError("初始化不得取消预约")

    result = asyncio.run(run_interactive_initialization(
        account_id="alice",
        settings=Settings(control_token="local-token", db_path=str(tmp_path / "alice.sqlite")),
        config_path=config_path,
        verifier=FakeVerifier(),
        booking_adapter=ForbiddenBookingAdapter(),
        input_fn=iter(["", "", "", "floor", "4F", "新图", "4F", ""]).__next__,
        output_fn=lambda _: None,
    ))

    assert result["status"] == "ready"
    assert calls == []
    saved = config_path.read_text(encoding="utf-8")
    assert '"mode": "floor"' in saved
    assert '"library": "新图"' in saved
    assert '"arrival_window": [' in saved
    assert '"08:00"' in saved
    assert '"12:00"' in saved


def test_initialize_workflow_records_failed_state_when_read_only_verification_raises(tmp_path):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(
        '{"accounts":[{"id":"alice","enabled":true,"account":"1001","password":"secret"}]}',
        encoding="utf-8",
    )

    class BrokenVerifier:
        async def verify(self):
            raise RuntimeError("我的预约接口不可用")

    result = asyncio.run(run_interactive_initialization(
        account_id="alice",
        settings=Settings(control_token="local-token", db_path=str(tmp_path / "alice.sqlite")),
        config_path=config_path,
        verifier=BrokenVerifier(),
        input_fn=lambda *_: "",
        output_fn=lambda _: None,
    ))

    assert result["status"] == "failed"
    assert "我的预约接口不可用" in result["message"]
