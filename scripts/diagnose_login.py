import asyncio
import sys
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seat_assistant.account_lock import AccountLock
from seat_assistant.config import _load_dotenv, load_account_settings


async def main():
    _load_dotenv()
    account_id = sys.argv[1] if len(sys.argv) > 1 else None
    settings = load_account_settings(account_id)
    with AccountLock(Path(settings.profile_path).parent / "account.lock"):
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(str(settings.profile_path), executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe', headless=False)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(settings.login_url, wait_until='domcontentloaded')
                print('ACCOUNT_ID:', settings.account_id)
                await page.wait_for_timeout(2000)
                print('URL:', page.url)
                print('TITLE:', await page.title())
                print('TEXT:', (await page.locator('body').inner_text())[:3000])
                print('INPUTS:', await page.locator('input').count())
                print('BUTTONS:', await page.locator('button').all_inner_texts())
                input('检查完成后按回车关闭：')
            finally:
                await context.close()


if __name__ == '__main__':
    asyncio.run(main())
