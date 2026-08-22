import asyncio

from seat_assistant.browser_session import LockedBrowser


def test_locked_browser_passes_headless_mode_to_playwright(monkeypatch, tmp_path):
    captured = {}

    class FakeChromium:
        async def launch_persistent_context(self, profile, **kwargs):
            captured["profile"] = profile
            captured.update(kwargs)
            class Context:
                async def close(self):
                    pass

            return Context()

    class FakePlaywright:
        chromium = FakeChromium()

        async def stop(self):
            pass

    class FakePlaywrightFactory:
        async def start(self):
            return FakePlaywright()

    monkeypatch.setattr("seat_assistant.browser_session.async_playwright", lambda: FakePlaywrightFactory())
    browser = LockedBrowser(tmp_path / "browser-profile", headless=True)

    async def run():
        async with browser:
            pass

    asyncio.run(run())

    assert captured["headless"] is True
