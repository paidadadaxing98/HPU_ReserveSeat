"""Verify the configured account can reach the authenticated seat homepage."""
import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seat_assistant.account_lock import AccountLock
from seat_assistant.auth_flow import is_seat_app_url
from seat_assistant.calibration import sanitize_url
from seat_assistant.config import _load_dotenv, load_account_settings
from scripts.preview_reservation import CHROME, login_if_configured


async def run(account_id: str | None = None) -> int:
    _load_dotenv()
    settings = load_account_settings(account_id)
    profile = Path(settings.profile_path)
    lock = AccountLock(profile.parent / "account.lock")
    lock.__enter__()
    playwright = await async_playwright().start()
    context = None
    try:
        context = await playwright.chromium.launch_persistent_context(
            str(profile),
            executable_path=CHROME,
            headless=False,
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(settings.login_url, wait_until="domcontentloaded")
        try:
            logged_in = await login_if_configured(page, settings)
        except Exception as exc:
            print(f"登录测试失败：{exc}")
            print(f"当前页面：{sanitize_url(page.url)}")
            return 1
        if not logged_in or not is_seat_app_url(page.url):
            print("登录测试失败：未确认进入座位预约首页。")
            print(f"当前页面：{sanitize_url(page.url)}")
            return 1
        print("登录测试通过：已进入座位预约首页。")
        print(f"页面：{sanitize_url(page.url)}")
        return 0
    finally:
        if context is not None:
            await context.close()
        await playwright.stop()
        lock.__exit__(None, None, None)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只验证登录，不进行座位预约")
    parser.add_argument("--account", help="多账号配置中的账号 ID")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args().account)))
