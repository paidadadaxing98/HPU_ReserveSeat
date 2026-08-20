"""Capture the site's native end-time request without submitting a reservation."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seat_assistant.calibration import sanitize_url

SITE_URL = "https://seatlib.hpu.edu.cn/libseat/"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = Path(".browser-profile").resolve()
OUTPUT = Path("end-time-capture.json")


def redact_url(value: str) -> dict:
    parts = urlsplit(value)
    query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        safe_value = "<redacted>" if any(marker in lowered for marker in ("token", "auth", "ticket", "cookie", "secret")) else item
        query.append({"key": key, "value": safe_value})
    return {
        "url_without_query": urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment)),
        "query": query,
    }


async def main(args):
    captured = []
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(PROFILE), executable_path=CHROME, headless=False, viewport={"width": 1440, "height": 900}
        )
        page = context.pages[0] if context.pages else await context.new_page()

        async def record(response):
            if "/rest/v2/endTimesForSeat/" not in response.url:
                return
            body = {}
            try:
                body = await response.json()
            except Exception:
                pass
            request_headers = await response.request.all_headers()
            captured.append({
                "request_method": response.request.method,
                "request": redact_url(response.request.url),
                "response": redact_url(response.url),
                "response_status": response.status,
                "request_header_names": sorted(request_headers),
                "body": body,
            })

        page.on("response", lambda response: asyncio.create_task(record(response)))
        await page.goto(args.url, wait_until="domcontentloaded")
        print("请在浏览器中完成登录、选择图书馆、日期和阅览室。")
        print("然后点击一个空闲座位，点击一个预约开始时间；不要点击‘立即预约’。")
        input("开始时间点击完成后按回车保存采集：")
        await page.wait_for_timeout(1000)
        output = {
            "page": sanitize_url(page.url),
            "room": args.room,
            "date": args.date,
            "captured": captured,
            "note": "query 中 token/auth 等敏感值已脱敏；未执行预约提交。",
        }
        OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存 {len(captured)} 条结束时间响应：{OUTPUT.resolve()}")
        if captured:
            print(json.dumps(captured[-1], ensure_ascii=False, indent=2))
        await context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--room", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--url", default=SITE_URL)
    asyncio.run(main(parser.parse_args()))
