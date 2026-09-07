"""Capture the raw “我的预约” API payloads for one account.

The captured JSON is the ground truth used to harden the reservation query
and matching logic: every endpoint response is stored verbatim (tokens and
authorization headers redacted) so field shapes, status codes and sync lag
can be replayed without touching the live site.
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seat_assistant.browser_session import LockedBrowser, prepare_context_page
from seat_assistant.config import load_account_settings


_SENSITIVE = re.compile(r"(token=|Bearer |authorization)", re.IGNORECASE)


def _redact(value):
    """Strip auth material from captured text so files can be shared safely."""
    if isinstance(value, str):
        value = re.sub(r"token=[^&\"' ]+", "token=<redacted>", value, flags=re.IGNORECASE)
        value = re.sub(r"(Bearer\s+)[A-Za-z0-9._-]+", r"\1<redacted>", value, flags=re.IGNORECASE)
        return value
    if isinstance(value, dict):
        return {
            (key if key.lower() not in {"authorization", "token", "cookie"} else "<redacted>"):
                "<redacted>" if key.lower() in {"authorization", "token", "cookie"} else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


async def capture(account_id: str, out_dir: Path) -> Path:
    from scripts.preview_reservation import (
        capture_page_request,
        fetch_reservation_payload,
        is_seat_app_url,
        login_if_configured,
        wait_for_api_auth,
        wait_for_authenticated_page,
    )

    settings = load_account_settings(account_id)
    profile = Path(settings.profile_path)
    captured = {
        "account_id": account_id,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "day": date.today().isoformat(),
        "responses": [],
        "errors": [],
    }

    async with LockedBrowser(profile, headless=True) as context:
        page = await prepare_context_page(context)
        api_auth = {"headers": {}, "token": ""}
        tasks = set()

        def on_request(request):
            task = asyncio.create_task(capture_page_request(api_auth, request))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        page.on("request", on_request)
        await page.goto(settings.login_url, wait_until="domcontentloaded")
        logged_in = await login_if_configured(page, settings)
        if not logged_in and not is_seat_app_url(page.url):
            raise RuntimeError("未能自动登录，无法采集页面数据")
        await wait_for_authenticated_page(page, timeout_ms=30000)
        auth = await wait_for_api_auth(page, api_auth)

        async def probe(endpoint: str, label: str):
            try:
                body = await fetch_reservation_payload(page, endpoint, auth["headers"], label)
                captured["responses"].append({"label": label, "endpoint": endpoint, "body": _redact(body)})
            except Exception as exc:
                captured["errors"].append({"label": label, "endpoint": endpoint, "error": str(exc)})

        for page_number in range(1, 6):
            endpoint = (
                f"/rest/v2/history/{page_number}/100"
                f"?page={page_number}&pageSize=100&token={quote(auth['token'], safe='')}"
            )
            await probe(endpoint, f"history_page_{page_number}")

        await probe(
            f"/rest/v2/user/reservations?token={quote(auth['token'], safe='')}",
            "current_reservations",
        )

        try:
            captured["my_reservations_page_text"] = await page.locator("body").inner_text()
        except Exception as exc:
            captured["errors"].append({"label": "page_text", "error": str(exc)})

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"capture-reservations-{account_id}-{stamp}.json"
    path.write_text(json.dumps(_redact(captured), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="采集“我的预约”接口原始数据用于回放分析")
    parser.add_argument("--account", required=True, help="accounts.json 中的账号 id")
    parser.add_argument("--out", default="logs/captures", help="输出目录，默认 logs/captures")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    path = asyncio.run(capture(args.account, Path(args.out)))
    print(f"已采集：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
