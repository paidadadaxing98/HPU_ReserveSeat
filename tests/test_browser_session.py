import asyncio

from seat_assistant.browser_session import run_initialization_verification


def test_initialization_verification_only_runs_read_checks():
    calls = []

    class FakeVerifier:
        async def verify(self):
            calls.append("verify")
            return {"home": True, "my_reservations": True, "capabilities": {"history": True}}

    result = asyncio.run(run_initialization_verification(FakeVerifier()))

    assert result["ready"] is True
    assert calls == ["verify"]
