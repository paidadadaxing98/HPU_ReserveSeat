"""Initialize one account without selecting or submitting a reservation."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seat_assistant.auth_flow import is_seat_app_url
from seat_assistant.browser_session import LockedBrowser
from seat_assistant.calibration import sanitize_url
from seat_assistant.config import _load_dotenv, load_account_settings
from seat_assistant.initialization import (
    choose_library_from_input,
    initialization_summary,
    location_preference_from_input,
    parse_period_arguments,
    parse_time_arguments,
    run_interactive_initialization,
)
from seat_assistant.notifications import WeComNotifier, send_initialization_notification
from seat_assistant.storage import Repository
from scripts.preview_reservation import (
    capture_page_request,
    fetch_user_reservations_with_capabilities,
    login_if_configured,
    open_library_options,
    select_library,
    visible_room_names,
    wait_for_authenticated_page,
)


class ReadOnlyAccountVerifier:
    def __init__(self, settings):
        self.settings = settings

    async def verify(self) -> dict:
        async with LockedBrowser(Path(self.settings.profile_path)) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            auth_state = {"headers": {}, "token": ""}
            capture_tasks = set()

            def capture_request(request):
                task = asyncio.create_task(capture_page_request(auth_state, request))
                capture_tasks.add(task)
                task.add_done_callback(capture_tasks.discard)

            page.on("request", capture_request)
            await page.goto(self.settings.login_url, wait_until="domcontentloaded")
            logged_in = await login_if_configured(page, self.settings)
            if not logged_in and not is_seat_app_url(page.url):
                input("请在浏览器中完成登录并进入座位系统首页，完成后按回车继续：")
            await wait_for_authenticated_page(page, timeout_ms=30000)
            libraries = await visible_library_names(page)
            print("当前可选图书馆：" + ("、".join(libraries) if libraries else "未读取到"))
            rooms_by_library = {}
            catalog_errors = {}
            for library in libraries:
                try:
                    await select_library(page, library)
                    await page.wait_for_timeout(800)
                    rooms_by_library[library] = await visible_room_names(page)
                except Exception as exc:
                    print(f"读取图书馆‘{library}’的阅览室失败：{exc}")
                    rooms_by_library[library] = []
                    catalog_errors[library] = str(exc) or "未知错误"
            rooms = rooms_by_library.get(libraries[0], []) if libraries else []
            print("当前可选阅览室：" + ("、".join(rooms) if rooms else "未读取到"))
            # The homepage normally loads both reservation endpoints. Calling
            # the read-only helper directly also verifies captured auth data.
            reservations, capabilities = await fetch_user_reservations_with_capabilities(page, auth_state)
            return {
                "login": True,
                "home": is_seat_app_url(page.url),
                "my_reservations": capabilities["my_reservations"],
                "capabilities": capabilities,
                "reservation_count": len(reservations),
                "library_catalog": libraries,
                "seat_catalog": rooms,
                "rooms_by_library": rooms_by_library,
                "catalog_errors": catalog_errors,
            }


async def run(
    account_id: str | None,
    period_values: list[str],
    seat_values: list[str] | None = None,
    time_values: list[str] | None = None,
) -> int:
    _load_dotenv()
    settings = load_account_settings(account_id)
    config_path = Path(os.getenv("SEAT_ACCOUNTS_FILE", "accounts.json")).resolve()
    if not config_path.exists():
        raise ValueError("未找到 accounts.json；初始化命令需要使用多账号配置文件")
    verifier = ReadOnlyAccountVerifier(settings)
    overrides = parse_period_arguments(period_values)
    if time_values is not None:
        overrides.update(parse_time_arguments(time_values))
    state = await run_interactive_initialization(
        account_id=settings.account_id,
        settings=settings,
        config_path=config_path,
        verifier=verifier,
        period_overrides=overrides,
        seat_rule_values=seat_values,
        prompt_periods=not (bool(seat_values) or time_values is not None),
    )
    send_initialization_notification(WeComNotifier(settings.wecom_webhook), settings.account_id, state)
    if state["status"] != "ready":
        print(f"初始化未完成：{state.get('message') or '请检查登录和接口'}")
        return 1
    print("初始化摘要：")
    print(f"账号 ID：{settings.account_id}")
    refreshed = load_account_settings(settings.account_id)
    saved_periods = {
        name: tuple(period.arrival_window)
        for name, period in refreshed.periods.items()
    }
    print(initialization_summary(
        refreshed.account_id,
        refreshed.location_preference,
        refreshed.seat_preference,
        saved_periods,
    ))
    print("已验证：登录、座位系统首页、我的预约接口")
    print("本次只验证登录和接口，没有预约任何座位。")
    return 0


async def visible_library_names(page) -> list[str]:
    try:
        _, locator = await open_library_options(page)
    except Exception as exc:
        print(f"图书馆目录自动采集失败：{exc}")
        return []
    values = []
    for index in range(await locator.count()):
        text = (await locator.nth(index).inner_text()).strip()
        if text and text not in values:
            values.append(text)
    return values


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="初始化账号；只验证登录和我的预约接口，不预约座位",
        epilog=(
            "示例：--seat 2-x-x --seat 2-9-109 --time 10:00-12:00 x 19:00-21:00。"
            "座位规则为图书馆编号-阅览室编号-座位号，x 表示该级别自动选择；越精确越优先。"
        ),
    )
    parser.add_argument("--account", help="accounts.json 中的账号 id，多账号时必须填写")
    parser.add_argument(
        "--period",
        action="append",
        default=[],
        metavar="PERIOD=HH:MM-HH:MM",
        help="预填学习窗口，可重复；例如 morning=08:00-12:00",
    )
    parser.add_argument(
        "--seat",
        action="append",
        default=[],
        metavar="LIBRARY-ROOM-SEAT",
        help="自动设置位置/座位规则，可重复，例如 2-x-x、2-9-x、2-9-109；越精确越优先",
    )
    parser.add_argument(
        "--time",
        nargs=3,
        metavar=("MORNING", "AFTERNOON", "EVENING"),
        help="依次设置上午、下午、晚上窗口；用 x 保持原值，例如 10:00-12:00 x 19:00-21:00",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        arguments = parse_args()
        raise SystemExit(asyncio.run(run(arguments.account, arguments.period, arguments.seat, arguments.time)))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"初始化失败：{exc}")
        raise SystemExit(1)
