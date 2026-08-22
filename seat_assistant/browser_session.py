"""Shared persistent-browser lifecycle and read-only initialization contract."""

import sys
from pathlib import Path

from playwright.async_api import async_playwright

from .account_lock import AccountLock


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


class LockedBrowser:
    def __init__(self, profile: Path, headless: bool = False):
        self.profile = Path(profile)
        self.headless = bool(headless)
        self.lock = AccountLock(self.profile.parent / "account.lock")
        self.playwright = None
        self.context = None

    async def __aenter__(self):
        self.lock.__enter__()
        try:
            self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(self.profile),
                executable_path=CHROME,
                headless=self.headless,
                viewport={"width": 1440, "height": 900},
            )
            return self.context
        except Exception:
            self.lock.__exit__(*sys.exc_info())
            if self.playwright is not None:
                await self.playwright.stop()
            raise

    async def __aexit__(self, exc_type, exc_value, traceback):
        try:
            if self.context is not None:
                await self.context.close()
            if self.playwright is not None:
                await self.playwright.stop()
        finally:
            self.lock.__exit__(exc_type, exc_value, traceback)
        return False


async def run_initialization_verification(verifier) -> dict:
    """Normalize a read-only verifier result without exposing booking methods."""
    result = await verifier.verify()
    result = result if isinstance(result, dict) else {}
    home = bool(result.get("home", result.get("home_verified", False)))
    login = bool(result.get("login", result.get("login_verified", home)))
    reservations = bool(result.get("my_reservations", result.get("my_reservations_verified", False)))
    capabilities = result.get("capabilities") if isinstance(result.get("capabilities"), dict) else {}
    return {
        **result,
        "home": home,
        "my_reservations": reservations,
        "capabilities": capabilities,
        "login": login,
        "ready": login and home and reservations,
    }
