"""Observe the authenticated seat site and save a selector inventory."""
import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seat_assistant.calibration import sanitize_url
from seat_assistant.account_lock import AccountLock
from seat_assistant.config import _load_dotenv, load_account_settings

SITE_URL = os.getenv("SEAT_LOGIN_URL", "https://seatlib.hpu.edu.cn/libseat/")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = Path(".browser-profile").resolve()
OUTPUT = Path("site-calibration.json")


async def main(account_id=None):
    _load_dotenv()
    settings = load_account_settings(account_id)
    profile = Path(settings.profile_path)
    with AccountLock(profile.parent / "account.lock"):
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(profile), executable_path=CHROME, headless=False,
                viewport={"width": 1440, "height": 900}
            )
            page = context.pages[0] if context.pages else await context.new_page()
            response_urls = set()
            response_bodies = {}

            async def capture_response(response):
                url = sanitize_url(response.url)
                response_urls.add(url)
                if not any(path in url for path in ("/rest/v2/room/layoutByDate/", "/rest/v2/room/stats2/", "/rest/v2/startTimesForSeat/", "/rest/v2/endTimesForSeat/")):
                    return
                try:
                    response_bodies[url] = await response.json()
                except Exception:
                    pass

            page.on("response", lambda response: asyncio.create_task(capture_response(response)))
            await page.goto(settings.login_url, wait_until="domcontentloaded")
            print(f"账号 {settings.account_id}：第 1/4 步：请完成 WebVPN/CAS 登录。")
            print("看到蓝色标题‘座位预约系统’和‘自选座位’首页后再按回车。")
            input("第 1 步完成后按回车：")
            snapshots = {"home": await snapshot(page)}

            print("第 2/4 步：请在页面上选择你要去的图书馆/校区。")
            print("确认页面出现多个阅览室按钮（例如‘4层计算机类借阅区’）后按回车。")
            input("第 2 步完成后按回车：")
            snapshots["rooms"] = await snapshot(page)

            print("第 3/6 步：请点击一个你确认有空闲座位的阅览室。")
            print("确认已经进入座位图，能看到多个座位编号后按回车；此时先不要点击具体座位。")
            input("第 3 步完成后按回车：")
            snapshots["free_seat_map"] = await snapshot(page)

            print("第 4/6 步：请切换回阅览室列表，选择一个你确认有同学预约的阅览室。")
            print("进入该阅览室的座位图后，不要点击座位，按回车采集占用样本。")
            input("第 4 步完成后按回车：")
            snapshots["occupied_seat_map"] = await snapshot(page)

            print("第 5/6 步：请回到一个有空闲座位的阅览室。")
            print("点击一个空闲座位，确认弹出时间选择窗口后按回车。")
            input("第 5 步完成后按回车：")
            snapshots["seat_dialog"] = await snapshot(page)

            print("第 6/6 步：请确认弹窗中出现开始时间、结束时间和‘立即预约’。")
            print("不要点击‘立即预约’，按回车保存最终采集。")
            input("第 6 步完成后按回车：")
            snapshots["seat_dialog_final"] = await snapshot(page)
            await page.wait_for_timeout(500)
            data = {
                "url": snapshots["seat_dialog_final"]["url"],
                "title": snapshots["seat_dialog_final"]["title"],
                "snapshots": snapshots,
                "text": snapshots["seat_dialog"]["text"],
                "response_urls": sorted(response_urls),
                "response_bodies": response_bodies,
            }
            data["url"] = sanitize_url(data["url"])
            OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已保存页面结构：{OUTPUT.resolve()}")
            await context.close()


async def snapshot(page):
    data = await page.evaluate("""() => ({
      url: location.href, title: document.title,
      controls: [...document.querySelectorAll('button, input, select, textarea, a, [role=button]')].map(x => ({tag:x.tagName,type:x.type||'',text:(x.innerText||x.value||'').trim(),aria:x.getAttribute('aria-label'),title:x.getAttribute('title'),name:x.getAttribute('name'),id:x.id,placeholder:x.getAttribute('placeholder'),className:typeof x.className==='string'?x.className:''})).filter(x=>x.text||x.aria||x.title||x.name||x.id||x.placeholder),
      interactive_candidates: [...document.querySelectorAll('[class*="seat"], [class*="room"], [class*="area"], [class*="item"], [class*="btn"], [data-seat], [data-id]')].map(x => ({tag:x.tagName,text:(x.innerText||'').trim().slice(0,80),className:typeof x.className==='string'?x.className:'',data:[...x.attributes].filter(a=>a.name.startsWith('data-')).reduce((o,a)=>(o[a.name]=a.value,o),{}),box:(()=>{const r=x.getBoundingClientRect(); return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}})()})).filter(x=>x.box.w>0&&x.box.h>0),
      frames: [...document.querySelectorAll('iframe')].map(x => ({title:x.title,name:x.name,src:x.src.split('?')[0]})),
      scripts: [...document.scripts].map(x=>x.src).filter(Boolean).map(x=>x.split('?')[0]),
      text: document.body.innerText.slice(0,12000)
    })""")
    data["url"] = sanitize_url(data["url"])
    return data


if __name__ == "__main__":
    if "--check-imports" in sys.argv:
        print("imports ok")
    else:
        asyncio.run(main(sys.argv[sys.argv.index("--account") + 1] if "--account" in sys.argv else None))
