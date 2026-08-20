import asyncio
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(str(Path('.browser-profile').resolve()), executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe', headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto('https://seatlib.hpu.edu.cn/libseat/', wait_until='domcontentloaded')
        await page.wait_for_timeout(2000)
        print('URL:', page.url)
        print('TITLE:', await page.title())
        print('TEXT:', (await page.locator('body').inner_text())[:3000])
        print('INPUTS:', await page.locator('input').count())
        print('BUTTONS:', await page.locator('button').all_inner_texts())
        input('检查完成后按回车关闭：')
        await context.close()


if __name__ == '__main__':
    asyncio.run(main())
