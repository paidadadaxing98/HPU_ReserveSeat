"""Open a reservation preview and stop before the submit button."""
import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seat_assistant.calibration import sanitize_url
from seat_assistant.account_lock import AccountLock
from seat_assistant.browser_session import LockedBrowser
from seat_assistant.auth_flow import auth_header_names, browser_api_headers, captcha_image_selectors, captcha_input_selectors, captcha_kind_from_text, credentials_available, is_captcha_failure_message, is_seat_app_url, library_selected, login_failure_message, normalize_library
from seat_assistant.captcha_llm import CaptchaVisionError, QwenCaptchaClient
from seat_assistant.config import _load_dotenv, load_account_settings
from seat_assistant.date_selection import date_option_matches, normalize_date
from seat_assistant.end_times import parse_native_end_times
from seat_assistant.booking_window import validate_booking_date
from seat_assistant.initialization import initialization_skip_message
from seat_assistant.notifications import WeComNotifier, send_reservation_notification
from seat_assistant.preview import choose_room_for_preference, layout_from_response, layout_request_matches, normalize_room_name, preview_seat_candidates, room_preference_candidates, selection_seed
from seat_assistant.reservation import SeatResult
from seat_assistant.seat_inventory import seats_from_layout
from seat_assistant.storage import Repository
from seat_assistant.submission import active_reservations_for_day, blocking_active_reservations_for_day, confirmation_required, day_reservations, end_time_response_matches_start, find_matching_reservation, find_reservation_by_day_and_time, find_similar_reservation, history_page_records, local_reservation_blocks_retry, normalize_time_option, requested_times_available, reservation_matches, submission_settled, time_option_id, time_values, validate_half_hour_time

SITE_URL = os.getenv("SEAT_LOGIN_URL", "https://seatlib.hpu.edu.cn/libseat/")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = Path(".browser-profile").resolve()


def ensure_initialized_account(settings, repository) -> None:
    """Stop direct account commands until the read-only setup is complete."""
    if not getattr(settings, "require_initialization", False):
        return
    state = repository.initialization_state()
    if state["status"] != "ready":
        raise ValueError(initialization_skip_message(state))


def library_switch_needed(current_library: str, target_library: str) -> bool:
    return not library_selected(current_library, target_library)


async def main(args):
    _load_dotenv()
    interactive = getattr(args, "interactive", True)
    record_success_quota = getattr(args, "record_success_quota", True)
    account_settings = load_account_settings(args.account)
    cli_preferred = bool(args.preferred)
    if not cli_preferred:
        args.preferred = list(account_settings.preferred_seats)
    args.preference = (
        {"mode": "seats", "seats": list(args.preferred)}
        if cli_preferred
        else getattr(account_settings, "seat_preference", {})
    )
    args.location = dict(getattr(account_settings, "location_preference", {}) or {})
    seat_rules = [dict(rule) for rule in getattr(account_settings, "seat_rules", [])]
    use_seat_rules = bool(seat_rules) and not getattr(args, "room", "") and not cli_preferred
    if not args.location.get("library") and not use_seat_rules:
        raise ValueError("账号尚未配置图书馆位置偏好，请先运行初始化命令")
    profile = Path(account_settings.profile_path)
    repository = Repository(str(account_settings.db_path), account_settings.account_id)
    ensure_initialized_account(account_settings, repository)
    notifier = WeComNotifier(account_settings.wecom_webhook)
    validate_booking_date(args.date, __import__('datetime').datetime.now())
    args.start = validate_half_hour_time(args.start)
    args.end = validate_half_hour_time(args.end)
    async with LockedBrowser(profile, headless=getattr(args, "headless", False)) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        api_auth = {"headers": {}, "token": ""}
        capture_tasks = set()

        def capture_request(request):
            task = asyncio.create_task(capture_page_request(api_auth, request))
            capture_tasks.add(task)
            task.add_done_callback(capture_tasks.discard)

        page.on("request", capture_request)
        try:
            await page.goto(account_settings.login_url, wait_until="domcontentloaded")
            # The seat app may briefly render #/login while its SSO redirect
            # exchanges the ticket and adds the authenticated token.
            if not is_seat_app_url(page.url) and "#/login" in page.url:
                try:
                    await page.wait_for_url("**/libseat/**#/home", timeout=15000)
                except Exception:
                    pass
            logged_in = await login_if_configured(page, account_settings)
            if not logged_in:
                if "#/login" in page.url:
                    print("当前是座位系统登录状态失效，正在重新打开统一认证入口……")
                    await page.goto(account_settings.login_url, wait_until="domcontentloaded")
                    logged_in = await login_if_configured(page, account_settings)
                if not logged_in:
                    if not interactive:
                        raise RuntimeError("未能自动登录，已停止无人值守预约")
                    print("第 1 步（手动）：请完成登录，直到进入‘自选座位’首页。")
                    pause_for_manual_interaction("登录完成后按回车：", interactive=interactive)
            print("正在确认进入座位预约首页……")
            await page.wait_for_url("**/libseat/**", timeout=30000)
            if use_seat_rules:
                libraries = await visible_library_names(page)
                rooms_by_library, catalog_errors = await collect_rooms_by_library(page, libraries)
                for library, error in catalog_errors.items():
                    print(f"读取图书馆‘{library}’的阅览室失败：{error}")
                room_candidates = room_preference_candidates(
                    seat_rules,
                    libraries,
                    rooms_by_library,
                    seed=selection_seed(
                        account_settings.account_id,
                        args.date,
                        getattr(args, "period", "manual"),
                        args.start,
                        args.end,
                    ),
                )
                args.location = {"library": room_candidates[0]["library"], "floor": "", "room": room_candidates[0]["room"]}
                args.preference = dict(room_candidates[0]["preference"])
                args.room = room_candidates[0]["room"]
                print(f"按座位规则准备候选：{len(room_candidates)} 个图书馆/阅览室组合。")
                await select_library(page, room_candidates[0]["library"])
                current_library = room_candidates[0]["library"]
            else:
                print(f"程序操作：自动选择图书馆‘{args.location['library']}’。")
                await select_library(page, args.location["library"])
                room_candidates = None
                current_library = args.location["library"]
            print(f"程序操作：自动选择预约日期‘{args.date}’。")
            await select_date(page, args.date)
            if room_candidates is None and not getattr(args, "room", ""):
                room_names = await visible_room_names(page)
                room_preference = {
                    **args.location,
                    "seat_preference": args.preference,
                }
                round_robin = None
                if args.location.get("floor") and not args.location.get("room"):
                    round_robin = lambda floor, candidates: repository.next_room_round_robin(
                        args.location["library"], floor, candidates
                    )
                args.room = choose_room_for_preference(
                    room_names,
                    room_preference,
                    seed=selection_seed(
                        account_settings.account_id,
                        args.date,
                        getattr(args, "period", "manual"),
                        args.start,
                        args.end,
                    ),
                    round_robin=round_robin,
                )
                print(f"根据座位偏好选择阅览室：{args.room}")
            if room_candidates is None:
                room_candidates = [{
                    "library": args.location["library"],
                    "room": args.room,
                    "preference": dict(args.preference),
                }]
            existing = await fetch_user_reservations(page, api_auth)
            print(f"当天预约记录：{daily_reservation_details(existing, args.date) or '无'}")
            reservation_key = _reservation_storage_key(args)
            local_record = repository.get_reservation(args.date, reservation_key)
            if local_reservation_blocks_retry(local_record):
                print(f"本地记录显示本次日期已有{local_record['status']}提交，停止以避免重复预约：{local_record['start']}-{local_record['end']}")
                await context.close()
                return
            active_today = blocking_active_reservations_for_day(existing, args.date, datetime.now())
            if active_today:
                details = "；".join(reservation_summary(item) for item in active_today)
                print(f"当天已经存在有效预约，学校限制一次只能预约一个时间段，跳过本次预约：{details}")
                await context.close()
                return
            quota_day = date.today().isoformat()
            if repository.successful_booking_count(quota_day) >= account_settings.daily_success_limit:
                print(f"账号 {account_settings.account_id} 今日已完成 {account_settings.daily_success_limit} 次成功预约，停止本次提交。")
                await context.close()
                return
            similar = find_similar_reservation(existing, args.date, args.room, args.start, args.end)
            if similar:
                print(f"首页已检测到相近预约，跳过选座和提交：{reservation_summary(similar)}")
                await context.close()
                return
        except Exception as exc:
            print(f"流程暂停：{exc}")
            print(f"浏览器当前页面：{sanitize_url(page.url)}")
            pause_for_manual_interaction("请检查浏览器页面；确认后按回车关闭浏览器：", interactive=interactive)
            return
        selected = None
        request_headers = {}
        candidate_errors = []
        for room_candidate in room_candidates:
            args.location["library"] = room_candidate["library"]
            args.room = room_candidate["room"]
            args.preference = dict(room_candidate["preference"])
            try:
                if library_switch_needed(current_library, room_candidate["library"]):
                    await select_library(page, room_candidate["library"])
                    await select_date(page, args.date)
                    current_library = room_candidate["library"]
                print(f"程序操作：自动点击阅览室‘{args.room}’，请不要在网页上点击阅览室。")
                room_selected, room_headers, room_errors = await select_room_and_seat(
                    page,
                    api_auth,
                    args.room,
                    args.preferred,
                    args.preference,
                    args.date,
                    args.start,
                    args.end,
                    selection_seed(
                        account_settings.account_id,
                        args.date,
                        getattr(args, "period", "manual"),
                        args.start,
                        args.end,
                        args.room,
                    ),
                    args.room_id,
                )
            except Exception as exc:
                if page.is_closed():
                    raise RuntimeError(f"浏览器页面已关闭，阅览室错误：{exc}") from exc
                room_selected, room_headers, room_errors = None, {}, [f"阅览室 {args.room}: {exc}"]
            candidate_errors.extend(room_errors)
            if room_selected is not None:
                selected = room_selected
                request_headers = room_headers
                print(f"接口选择座位：{selected.number}（座位ID {selected.seat_id}，状态 FREE）")
                break
        if selected is None:
            diagnostics = await auth_diagnostics(page, request_headers)
            details = "；".join(candidate_errors) or "没有可尝试的空闲座位"
            raise RuntimeError(
                f"没有找到同时满足 {args.start}-{args.end} 的空闲座位：{details}。"
                f"认证诊断（仅字段名，不含值）：{diagnostics}"
            )
        # Recheck immediately before submit in case another process created a
        # similar reservation after the homepage check.
        existing = await fetch_user_reservations(page, api_auth)
        print(f"提交前当天预约记录：{daily_reservation_details(existing, args.date) or '无'}")
        reservation_key = _reservation_storage_key(args)
        local_record = repository.get_reservation(args.date, reservation_key)
        if local_reservation_blocks_retry(local_record):
            print(f"提交前发现本地已有{local_record['status']}提交，取消本次操作以避免重复预约：{local_record['start']}-{local_record['end']}")
            await close_time_dialog(page)
            await context.close()
            return
        active_today = blocking_active_reservations_for_day(existing, args.date, datetime.now())
        if active_today:
            details = "；".join(reservation_summary(item) for item in active_today)
            print(f"提交前发现当天已有有效预约，学校限制一次只能预约一个时间段，取消本次提交：{details}")
            await close_time_dialog(page)
            await context.close()
            return
        similar = find_similar_reservation(existing, args.date, args.room, args.start, args.end)
        if similar:
            print(f"提交前再次检测到相近预约，取消本次操作：{reservation_summary(similar)}")
            await close_time_dialog(page)
            await context.close()
            return
        phrase = input("当前页面已选好座位和时间。输入 SUBMIT 才会提交，直接回车保持预览：") if args.confirm_submit and interactive else ""
        if confirmation_required(args.submit, args.confirm_submit, phrase):
            print("预览已完成：页面停在‘立即预约’前。没有提交预约。")
            print(f"页面：{sanitize_url(page.url)}")
            pause_for_manual_interaction("确认页面选择正确后按回车关闭预览：", interactive=interactive)
            await context.close()
            return
        print("正在提交一次真实预约……")
        await page.get_by_role("button", name="立即预约").click()
        try:
            await page.wait_for_function("() => !document.body.innerText.includes('正在玩命预约中') && !document.body.innerText.includes('玩命预约')", timeout=30000)
        except Exception:
            print("提交请求超过 30 秒仍未结束，结果不明确；不会重复提交。")
            repository.save_reservation(
                args.date, reservation_key, "pending", args.start, args.end, args.room, selected.number,
                "提交请求超过 30 秒仍未结束",
            )
            send_preview_notification(
                notifier,
                args,
                SeatResult(False, args.room, selected.number, "提交请求超过 30 秒仍未结束", conclusive=False),
            )
            pause_for_manual_interaction("请在浏览器中检查状态后按回车关闭：", interactive=interactive)
            await context.close()
            return
        await page.wait_for_timeout(1000)
        page_text = await page.locator("body").inner_text()
        print("提交后的页面提示：", " ".join(page_text.split())[-500:])
        submission_signal = submission_notice(page_text)
        if submission_signal[1]:
            print(f"提交页面信号：{submission_signal[1]}")
        if await close_success_dialog(page):
            print("已自动关闭预约成功弹窗，并确认阻塞层已隐藏。")
        print("正在打开‘我的预约’核验……")
        try:
            await page.get_by_text("我的预约", exact=True).last.click()
        except Exception as exc:
            print(f"打开‘我的预约’页面失败，将只通过接口核验：{exc}")
        try:
            await page.wait_for_function("() => document.body.innerText.includes('我的预约') && !document.body.innerText.includes('正在加载')", timeout=15000)
        except Exception:
            pass
        verification_status, matched_reservation, verification_message = await wait_for_reservation_confirmation(
            page, api_auth, args.date, args.room, selected.number, args.start, args.end,
            existing, submission_signal=submission_signal,
        )
        if verification_status == "success":
            if record_success_quota:
                repository.record_successful_booking(date.today().isoformat(), f"{args.date}:{selected.number}:{uuid.uuid4().hex}")
            repository.save_reservation(args.date, reservation_key, "reserved", args.start, args.end, args.room, selected.number, verification_message)
            print(f"核验成功：{args.date}，{args.room}，座位 {selected.number}，{args.start}-{args.end}")
            send_preview_notification(
                notifier,
                args,
                SeatResult(True, args.room, selected.number, verification_message),
            )
        elif verification_status == "failed":
            print(f"预约明确失败：{verification_message}")
            send_preview_notification(
                notifier,
                args,
                SeatResult(False, args.room, selected.number, verification_message),
            )
        elif verification_status == "pending":
            print(f"预约已提交，待核验：{verification_message}")
            repository.save_reservation(args.date, reservation_key, "pending", args.start, args.end, args.room, selected.number, verification_message)
            send_preview_notification(
                notifier,
                args,
                SeatResult(False, args.room, selected.number, verification_message, conclusive=False),
            )
        else:
            print(f"核验结果不明确：{verification_message or '请在‘我的预约’页面手动确认'}；程序不会重复提交。")
            repository.save_reservation(args.date, reservation_key, "uncertain", args.start, args.end, args.room, selected.number, verification_message or "提交后核验结果不明确")
            send_preview_notification(
                notifier,
                args,
                SeatResult(False, args.room, selected.number, verification_message or "提交后核验结果不明确", conclusive=False),
            )
        print(f"页面：{sanitize_url(page.url)}")
        if not args.submit or args.confirm_submit:
            pause_for_manual_interaction("确认页面选择正确后按回车关闭预览：", interactive=interactive)
        await context.close()


async def select_room_and_seat(
    page,
    api_auth: dict,
    room: str,
    preferred: list[str],
    preference: dict,
    day: str,
    start: str,
    end: str,
    seed: str,
    room_id: int | None = None,
) -> tuple[object | None, dict, list[str]]:
    """Open one room and try its free seats, returning errors for fallback."""
    errors = []
    try:
        async with page.expect_response(lambda response: layout_request_matches(response.url), timeout=30000) as response_info:
            await page.get_by_text(room, exact=True).first.click()
        response = await response_info.value
        body = await response.json()
        print(f"座位布局接口：HTTP {response.status}，code={body.get('code')}，message={body.get('message')}")
        layout = layout_from_response(body)
        actual_room_id = layout.get("id")
        print(f"网页返回阅览室：{layout.get('name')}，ID={actual_room_id}")
        if normalize_room_name(layout.get("name", "")) != normalize_room_name(room):
            raise ValueError(f"网页返回的阅览室是‘{layout.get('name')}’，不是‘{room}’。请检查图书馆选择。")
        if room_id is not None and room_id != actual_room_id:
            print(f"提示：命令中的 room-id={room_id} 与网页实际 ID={actual_room_id} 不一致；本次使用网页实际 ID。")
    except Exception as exc:
        if page.is_closed():
            raise RuntimeError(f"浏览器页面已关闭，阅览室错误：{exc}") from exc
        return None, {}, [f"阅览室 {room}: {exc}"]

    seats = seats_from_layout(layout)
    request_headers = {}
    for candidate in preview_seat_candidates(seats, preferred, preference, seed=seed):
        print(f"尝试座位：{candidate.number}（座位ID {candidate.seat_id}，状态 FREE）")
        try:
            async with page.expect_response(lambda r: "/rest/v2/startTimesForSeat/" in r.url, timeout=15000) as start_info:
                await page.get_by_text(candidate.number, exact=True).last.click()
            candidate_start_response = await start_info.value
            candidate_start_body = await candidate_start_response.json()
            candidate_headers = await candidate_start_response.request.all_headers()
            record_api_auth(api_auth, candidate_start_response.request.url, candidate_headers)
            if candidate_start_body.get("code") not in (0, "0"):
                raise RuntimeError(f"开始时间接口失败：code={candidate_start_body.get('code')}，message={candidate_start_body.get('message') or '无'}")
            normalized_start = time_values(candidate_start_body, "startTimes")
            if not requested_times_available(normalized_start, [start]):
                raise RuntimeError(f"可选开始时间为 {normalized_start or '未知'}，不包含 {start}")
            start_id = time_option_id(candidate_start_body, "startTimes", start)
            if not start_id:
                raise RuntimeError(f"开始时间 {start} 缺少网页返回的原生 id，已停止。")
            async with page.expect_response(lambda r: end_time_response_matches_start(r.url, start_id), timeout=15000) as end_info:
                await click_and_verify_time(page, start, "开始", verify=True)
            candidate_end_response = await end_info.value
            candidate_end_body = await candidate_end_response.json()
            candidate_end_headers = await candidate_end_response.request.all_headers()
            record_api_auth(api_auth, candidate_end_response.request.url, candidate_end_headers)
            native_end = parse_native_end_times(candidate_end_response.url, candidate_end_body)
            candidate_end = list(native_end.options)
            if not native_end.ok:
                raise RuntimeError(
                    f"结束时间接口失败：URL={native_end.url}，code={candidate_end_body.get('code')}，"
                    f"message={candidate_end_body.get('message') or '无'}"
                )
            if not requested_times_available(candidate_end, [end]):
                raise RuntimeError(
                    f"可选结束时间为 {candidate_end or '未知'}，不包含 {end}；"
                    f"接口 URL={native_end.url}，code={candidate_end_body.get('code')}，"
                    f"message={candidate_end_body.get('message') or '无'}"
                )
            await wait_for_time_option(page, end, "结束")
            await click_and_verify_time(page, end, "结束")
        except Exception as exc:
            errors.append(f"{room}/{candidate.number}: {exc}")
            if page.is_closed():
                raise RuntimeError(f"浏览器页面已关闭，原始座位错误：{exc}") from exc
            await close_time_dialog(page)
            continue
        return candidate, candidate_headers, errors
    errors.append(f"阅览室 {room} 没有满足 {start}-{end} 的可用座位")
    return None, request_headers, errors


async def run_scheduled_reservation(settings, day: str, period: str, start: str, end: str) -> SeatResult:
    """Run the same browser flow used by the CLI without interactive prompts."""
    args = argparse.Namespace(
        account=settings.account_id,
        room="",
        room_id=None,
        date=day,
        start=start,
        end=end,
        preferred=[],
        period=period,
        reservation_key=period or "manual",
        submit=True,
        confirm_submit=False,
        interactive=False,
        record_success_quota=False,
        headless=True,
    )
    repository = Repository(str(settings.db_path), settings.account_id)
    try:
        await main(args)
    except Exception as exc:
        return SeatResult(False, message=f"定时预约流程异常：{exc}", conclusive=False)
    record = repository.get_reservation(day, getattr(args, "reservation_key", period))
    if not record:
        return SeatResult(False, message="定时预约未产生可核验记录，已停止重复尝试", conclusive=False)
    if record["status"] == "reserved":
        return SeatResult(True, record["room"], record["seat"], record["message"])
    return SeatResult(
        False,
        record["room"],
        record["seat"],
        record["message"] or "定时预约结果不明确",
        conclusive=record["status"] == "failed",
    )


async def fetch_scheduled_current_reservations(settings, day: str) -> list[dict]:
    """Read current reservations for scheduling without selecting or submitting."""
    account_settings = settings
    profile = Path(account_settings.profile_path)
    async with LockedBrowser(profile, headless=True) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        api_auth = {"headers": {}, "token": ""}
        capture_tasks = set()

        def capture_request(request):
            task = asyncio.create_task(capture_page_request(api_auth, request))
            capture_tasks.add(task)
            task.add_done_callback(capture_tasks.discard)

        page.on("request", capture_request)
        await page.goto(account_settings.login_url, wait_until="domcontentloaded")
        logged_in = await login_if_configured(page, account_settings)
        if not logged_in and not is_seat_app_url(page.url):
            raise RuntimeError("未能自动登录，无法读取当前预约")
        await wait_for_authenticated_page(page, timeout_ms=30000)
        records = await fetch_user_reservations(page, api_auth)
        return day_reservations(records, day)


def _reservation_storage_key(args) -> str:
    return str(getattr(args, "reservation_key", None) or getattr(args, "period", None) or "manual")


def send_preview_notification(notifier, args, result) -> bool:
    sent = send_reservation_notification(notifier, args.date, "手动", result, args.start, args.end)
    if sent:
        print("企业微信通知已发送。")
    else:
        print("企业微信通知未发送（未配置或发送失败）。")
    return sent


def pause_for_manual_interaction(message: str, interactive: bool = True) -> bool:
    """Pause only for a human when the caller explicitly allows interaction."""
    if not interactive:
        print(f"无人值守模式：{message} 已停止等待人工操作。")
        return False
    input(message)
    return True


async def visible_room_names(page) -> list[str]:
    locator = page.locator(".room-name.item:visible, .room-wrap:visible")
    values = []
    for index in range(await locator.count()):
        text = (await locator.nth(index).inner_text()).strip()
        if text and text not in values:
            values.append(text)
    return values


async def collect_rooms_by_library(page, libraries: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Read each library independently so a stale catalog cannot block fallback rules."""
    rooms_by_library = {}
    errors = {}
    for library in libraries:
        try:
            await select_library(page, library)
            await page.wait_for_timeout(500)
            rooms_by_library[library] = await visible_room_names(page)
        except Exception as exc:
            rooms_by_library[library] = []
            errors[library] = str(exc) or "未知错误"
    return rooms_by_library, errors


async def visible_library_names(page) -> list[str]:
    _, locator = await open_library_options(page)
    values = []
    for index in range(await locator.count()):
        text = (await locator.nth(index).inner_text()).strip()
        if text and text not in values:
            values.append(text)
    return values


def library_control_selectors() -> tuple[str, ...]:
    return (
        "input[placeholder*='场馆'], input[placeholder*='地点'], input[placeholder*='图书馆']",
        "input.el-input__inner",
        ".el-select__caret",
        ".el-input__suffix",
    )


async def find_library_selector(page):
    for selector_text in (
        library_control_selectors()[0],
        ".el-select:visible input.el-input__inner",
    ):
        selector = page.locator(selector_text).first
        if await selector.count() > 0:
            return selector
    return None


async def click_library_option(page, options, name) -> None:
    """Click a library item while tolerating Element UI list re-renders."""
    target = normalize_library(name)
    visible_names = []
    option = None
    for index in range(await options.count()):
        candidate = options.nth(index)
        text = (await candidate.inner_text()).strip()
        if text:
            visible_names.append(text)
        if normalize_library(text) == target:
            option = candidate
            break
    if option is None:
        raise RuntimeError(f"未找到图书馆选项‘{name}’；当前可见选项：{visible_names}")

    try:
        await option.click(force=True, timeout=2500)
        return
    except Exception as click_error:
        try:
            await option.evaluate(
                """element => {
                    element.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    element.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    element.click();
                }"""
            )
            return
        except Exception as dom_error:
            raise RuntimeError(
                f"图书馆选项‘{name}’点击失败：{click_error}；DOM 触发也失败：{dom_error}"
            ) from dom_error


async def open_library_options(page):
    """Open the library dropdown without requiring a first manual click."""
    selector = await find_library_selector(page)
    if selector is None:
        raise RuntimeError("未找到图书馆选择框，请确认已进入座位预约首页。")
    await selector.wait_for(state="visible", timeout=15000)
    container = selector.locator("xpath=ancestor::div[contains(@class,'el-select')][1]")
    arrow = container.locator(".el-select__caret, .el-input__suffix").first
    options = page.locator(".el-select-dropdown:visible .el-select-dropdown__item:visible")
    for _ in range(4):
        for control in (arrow, container, selector):
            try:
                if await control.count() == 0:
                    continue
                await control.click(force=True)
            except Exception:
                try:
                    await control.evaluate("element => element.click()")
                except Exception:
                    continue
            try:
                await options.first.wait_for(state="visible", timeout=2500)
                return selector, options
            except Exception:
                pass
        for key in ("Enter", "ArrowDown"):
            try:
                await selector.focus()
                await selector.press(key)
                await options.first.wait_for(state="visible", timeout=1500)
                return selector, options
            except Exception:
                pass
        await page.wait_for_timeout(300)
    raise RuntimeError("图书馆下拉菜单无法自动打开，请确认页面组件已加载。")


def reservation_verification_status(
    reservations: list[dict],
    page_text: str,
    day: str,
    room: str,
    seat: str,
    start: str,
    end: str,
    submission_signal: tuple[str, str] = ("", ""),
    pre_submit_reservations: list[dict] | None = None,
) -> tuple[str, dict | None, str]:
    all_today = day_reservations(reservations, day)
    active_today = active_reservations_for_day(all_today, day)
    all_details = daily_reservation_details(all_today, day)
    matched = find_matching_reservation(reservations, day, room, seat, start, end, excluded=pre_submit_reservations)
    if matched:
        return "success", matched, f"网页历史记录已确认；当天全部预约：{all_details}"
    normalized = " ".join((page_text or "").replace("：", ":").split())
    for marker in ("当天已有预约", "已有预约", "只能预约一个", "预约失败", "座位已被占用", "不能预约", "无法预约", "预约冲突"):
        if marker in normalized:
            suffix = f"；当天全部预约：{all_details}" if all_details else ""
            return "failed", None, f"{marker}{suffix}"
    if submission_signal[0] == "success":
        time_match = find_reservation_by_day_and_time(reservations, day, start, end, excluded=pre_submit_reservations)
        if time_match:
            return "success", time_match, f"网页记录按日期和时间匹配（地点字段缺失）；当天全部预约：{all_details}"
        if all_details:
            return "pending", None, f"已提交，页面提示预约成功，但当天记录暂未形成唯一匹配；当天全部预约：{all_details}"
        return "pending", None, "已提交，页面提示预约成功，但历史接口尚未同步记录；当天全部预约：无"
    if active_today:
        return "failed", None, f"当天已有其他有效预约，本次请求未生效；当天全部预约：{all_details}"
    return "uncertain", None, f"提交后未在我的预约历史中找到完全匹配记录；当天全部预约：{all_details or '无'}"


def daily_reservation_details(reservations: list[dict], day: str) -> str:
    """Render all history records for a day with their native status."""
    records = day_reservations(reservations, day)
    details = []
    for item in records:
        status = str(item.get("stat") or item.get("status") or item.get("state") or "UNKNOWN").strip()
        details.append(f"[{status}] {reservation_summary(item)}")
    return "；".join(details)


def submission_notice(page_text: str) -> tuple[str, str]:
    normalized = " ".join((page_text or "").replace("：", ":").split())
    for marker in ("当天已有预约", "已有预约", "只能预约一个", "预约失败", "座位已被占用", "不能预约", "无法预约", "预约冲突"):
        if marker in normalized:
            return "failed", marker
    if "预约成功" in normalized:
        return "success", "预约成功"
    return "", ""


def reservation_verification_delay(attempt: int) -> int:
    """Return a conservative delay between post-submit API reads."""
    return (2000, 3000, 5000)[min(max(0, attempt), 2)]


async def wait_for_reservation_confirmation(
    page,
    auth_state: dict,
    day: str,
    room: str,
    seat: str,
    start: str,
    end: str,
    pre_submit_reservations: list[dict] | None = None,
    timeout_ms: int = 45000,
    submission_signal: tuple[str, str] = ("", ""),
) -> tuple[str, dict | None, str]:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    last_error = ""
    last_message = ""
    last_status = "uncertain"
    attempt = 0
    while asyncio.get_running_loop().time() < deadline:
        try:
            reservations = await fetch_post_submit_reservations(page, await wait_for_api_auth(page, auth_state))
            page_text = await page.locator("body").inner_text()
            status, matched, message = reservation_verification_status(
                reservations, page_text, day, room, seat, start, end,
                submission_signal=submission_signal,
                pre_submit_reservations=pre_submit_reservations,
            )
            last_status = status
            last_message = message
            if status in {"success", "failed"}:
                return status, matched, message
        except Exception as exc:
            last_error = str(exc)
        await page.wait_for_timeout(reservation_verification_delay(attempt))
        attempt += 1
    if last_status == "pending":
        try:
            reservations = await fetch_user_reservations(page, auth_state)
            page_text = await page.locator("body").inner_text()
            last_status, matched, last_message = reservation_verification_status(
                reservations, page_text, day, room, seat, start, end,
                submission_signal=submission_signal,
                pre_submit_reservations=pre_submit_reservations,
            )
            if last_status == "success":
                return last_status, matched, last_message
        except Exception as exc:
            last_error = str(exc)
        return "pending", None, last_message or last_error
    return "uncertain", None, last_error or last_message or "提交后未在我的预约历史中找到完全匹配记录"


async def login_if_configured(page, settings=None):
    account = (settings.account if settings is not None else os.getenv("SEAT_ACCOUNT", "")).strip()
    password = (settings.password if settings is not None else os.getenv("SEAT_PASSWORD", "")).strip()
    if not credentials_available(account, password):
        print("账号凭据为空，将复用浏览器会话；若未登录请先手动登录。")
        return False
    if is_seat_app_url(page.url):
        body_text = await page.locator("body").inner_text()
        reason = login_failure_message(body_text)
        if not reason:
            print(f"账号 {getattr(settings, 'account_id', 'default')} 浏览器会话已经登录，跳过账号密码填写。")
            return True
        raise RuntimeError(f"登录失败：{reason}")
    if "#/login" in page.url:
        try:
            await wait_for_authenticated_page(page, timeout_ms=15000)
            print("座位系统已通过现有会话登录，跳过账号密码填写。")
            return True
        except Exception:
            pass
    print("检测到本地凭据，正在自动填写统一身份认证。")
    captcha = page.locator(", ".join(captcha_input_selectors())).first
    captcha_visible = await captcha.count() > 0 and await captcha.is_visible()
    user = page.locator("input[name='username'], input[name='userName'], input[placeholder*='账号'], input[placeholder*='用户名']").first
    pwd = page.locator("input[type='password'], input[name='password']").first
    if await user.count() == 0 or await pwd.count() == 0:
        raise RuntimeError("未找到统一身份认证输入框，请清空凭据后手动登录，或检查登录页面变化。")
    submit = page.locator("button[type='submit'], input[type='submit'], button:has-text('登录'), input[value*='登录']").first
    if await submit.count() == 0:
        raise RuntimeError("未找到登录按钮，请清空凭据后手动登录。")
    for login_attempt in range(2):
        await user.fill(account)
        await pwd.fill(password)
        captcha = page.locator(", ".join(captcha_input_selectors())).first
        captcha_visible = await captcha.count() > 0 and await captcha.is_visible()
        if captcha_visible:
            answer = await solve_captcha_if_configured(page, settings, captcha)
            if answer is None:
                raise RuntimeError("检测到登录验证码，但本地识别和已配置的视觉模型都未给出可验证答案；为避免盲目提交，本次登录已停止。")
            await captcha.fill(answer)
        await submit.click()
        try:
            await wait_for_authenticated_page(page, timeout_ms=30000)
            return True
        except Exception as exc:
            body_text = await page.locator("body").inner_text()
            reason = login_failure_message(body_text)
            if login_attempt == 0 and is_captcha_failure_message(reason):
                print(f"登录验证码校验失败：{reason}；正在刷新验证码并重试一次。")
                await refresh_login_captcha(page, getattr(settings, "login_url", SITE_URL))
                continue
            if reason:
                raise RuntimeError(f"登录失败：{reason}") from exc
            raise RuntimeError("登录后未确认进入座位预约首页") from exc
    raise RuntimeError("登录验证码连续失败，已停止自动重试。")


async def wait_for_authenticated_page(page, timeout_ms: int = 30000):
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        body_text = await page.locator("body").inner_text()
        reason = login_failure_message(body_text)
        if reason:
            raise RuntimeError(f"登录失败：{reason}")
        if is_seat_app_url(page.url):
            return
        await page.wait_for_timeout(500)
    raise RuntimeError(f"登录跳转超时，当前页面：{sanitize_url(page.url)}")


async def refresh_login_captcha(page, login_url: str | None = None) -> None:
    try:
        if login_url:
            await page.goto(login_url, wait_until="domcontentloaded")
        else:
            await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(500)
        return
    except Exception:
        pass
    image_locator = page.locator(", ".join(captcha_image_selectors())).first
    if await image_locator.count() == 0 or not await image_locator.is_visible():
        print("验证码失败后未找到可点击的验证码图片，将直接重新读取登录表单。")
        return
    try:
        await image_locator.click(force=True)
    except Exception:
        try:
            await image_locator.evaluate("element => element.click()")
        except Exception:
            print("验证码图片刷新失败，将直接重新读取登录表单。")
            return
    await page.wait_for_timeout(500)


async def solve_captcha_if_configured(page, settings, captcha_input):
    if settings is None or not getattr(settings, "captcha_llm_enabled", False):
        print("检测到登录验证码，但验证码模型未启用。")
        return None
    image_locator = page.locator(", ".join(captcha_image_selectors())).first
    if await image_locator.count() == 0 or not await image_locator.is_visible():
        print("检测到验证码输入框，但未找到可截图的验证码图片。")
        return None
    image_bytes = await image_locator.screenshot(type="png")
    prompt_text = await page.locator("body").inner_text()
    kind = captcha_kind_from_text(prompt_text)
    if kind == "auto":
        print("验证码类型不明确，交给视觉模型判断并进行严格格式校验。")
    client = QwenCaptchaClient(
        settings.captcha_llm_api_key,
        settings.captcha_llm_base_url,
        settings.captcha_llm_model,
        settings.captcha_llm_timeout_seconds,
    )
    for attempt in range(1, settings.captcha_llm_max_attempts + 1):
        try:
            answer = client.solve(image_bytes, "image/png", kind)
            print(f"验证码视觉识别得到合规答案（第 {attempt} 次），准备提交登录。")
            return answer
        except CaptchaVisionError as exc:
            print(f"验证码视觉识别第 {attempt} 次未通过：{exc}")
            if attempt < settings.captcha_llm_max_attempts:
                await page.wait_for_timeout(2500)
    return None


def request_token(url: str) -> str:
    for key, values in parse_qs(urlsplit(url).query).items():
        if key.lower() == "token" and values and values[0]:
            return values[0]
    return ""


def record_api_auth(auth_state: dict, url: str, headers: dict[str, str]) -> None:
    if "/rest/v2/" not in url:
        return
    filtered = browser_api_headers(headers)
    token = request_token(url)
    if filtered and auth_header_names(filtered):
        auth_state["headers"] = filtered
    if token:
        auth_state["token"] = token


async def capture_page_request(auth_state: dict, request) -> None:
    if "/rest/v2/" not in request.url:
        return
    try:
        headers = await request.all_headers()
    except Exception:
        return
    record_api_auth(auth_state, request.url, headers)


async def wait_for_api_auth(page, auth_state: dict, timeout_ms=5000) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        headers = auth_state.get("headers") or {}
        token = auth_state.get("token") or ""
        if headers and token:
            return {"headers": dict(headers), "token": token}
        if page.is_closed():
            break
        await page.wait_for_timeout(100)
    raise RuntimeError(
        "未捕获座位系统 API 认证信息，无法读取当前预约；已停止。"
        "请确认已完成登录并让网页先加载一次座位数据。"
    )


async def fetch_user_reservations(page, auth_state: dict) -> list[dict]:
    records, _ = await fetch_user_reservations_with_capabilities(page, auth_state)
    return records


async def fetch_user_reservations_with_capabilities(page, auth_state: dict) -> tuple[list[dict], dict[str, bool]]:
    """Read both reservation endpoints and retain the result of each probe."""
    auth = await wait_for_api_auth(page, auth_state)
    history_error = None
    current_error = None
    capabilities = {
        "history": False,
        "current_reservations": False,
        "my_reservations": False,
    }
    try:
        history = await fetch_reservation_history(page, auth)
        capabilities["history"] = True
    except RuntimeError as exc:
        history = []
        history_error = str(exc)
    try:
        current = await fetch_current_reservations(page, auth)
        capabilities["current_reservations"] = True
    except RuntimeError as exc:
        current = []
        current_error = str(exc)
    # The web client's “我的预约” view is backed by the history endpoint;
    # keep that capability distinct from the lighter current-reservation poll.
    capabilities["my_reservations"] = capabilities["history"]
    if history_error and current_error:
        raise RuntimeError(
            "无法读取预约历史或当前预约，已停止："
            f"历史接口：{history_error}；当前预约接口：{current_error}"
        )
    return unique_reservation_records(history + current), capabilities


async def fetch_reservation_history(page, auth: dict) -> list[dict]:
    reservations = []
    page_number = 1
    page_size = 100
    while True:
        endpoint = f"/rest/v2/history/{page_number}/{page_size}?token={quote(auth['token'], safe='')}"
        body = await fetch_reservation_payload(page, endpoint, auth["headers"], "读取预约历史")
        batch, total = history_page_records(body)
        reservations.extend(batch)
        try:
            total_count = int(total) if total is not None else None
        except (TypeError, ValueError):
            total_count = None
        if not batch:
            return reservations
        if total_count is not None:
            if len(reservations) >= total_count:
                return reservations
        elif len(batch) < page_size:
            return reservations
        page_number += 1


async def fetch_current_reservations(page, auth: dict) -> list[dict]:
    endpoint = f"/rest/v2/user/reservations?token={quote(auth['token'], safe='')}"
    body = await fetch_reservation_payload(page, endpoint, auth["headers"], "读取当前预约")
    records, _ = history_page_records(body)
    return records


async def fetch_post_submit_reservations(page, auth: dict) -> list[dict]:
    """Use the lightweight current-reservation endpoint during polling."""
    return await fetch_current_reservations(page, auth)


async def fetch_reservation_payload(page, endpoint: str, headers: dict, label: str) -> dict:
    payload = await page.evaluate(
        """async ({endpoint, headers}) => {
            const response = await fetch(endpoint, {
                credentials: 'include',
                cache: 'no-store',
                headers,
            });
            const text = await response.text();
            let body = {};
            try { body = text ? JSON.parse(text) : {}; } catch (_) { body = {message: text}; }
            return {status: response.status, body};
        }""",
        {"endpoint": endpoint, "headers": headers},
    )
    if payload.get("status") != 200:
        raise RuntimeError(f"{label}失败：HTTP {payload.get('status')}")
    body = payload.get("body") or {}
    if body.get("code") not in (None, 0, "0"):
        raise RuntimeError(f"{label}失败：code={body.get('code')}，message={body.get('message') or '无'}")
    return body


def unique_reservation_records(records: list[dict]) -> list[dict]:
    unique = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        key = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            unique.append(record)
    return unique


def reservation_summary(item: dict) -> str:
    location = str(item.get("location") or item.get("loc") or "").strip()
    location_room = re.split(r"[，,]", location, maxsplit=1)[0].strip()
    location_seat = re.search(r"(?:座位号|座位)\s*([0-9A-Za-z-]+)", location)
    room = item.get("roomName") or item.get("room_name") or item.get("room") or location_room or "阅览室未知"
    seat = item.get("seatNumber") or item.get("seatNo") or item.get("seat") or (location_seat.group(1) if location_seat else "座位未知")
    start = item.get("startTime") or item.get("start_time") or item.get("beginTime") or item.get("begin") or "开始时间未知"
    end = item.get("endTime") or item.get("end_time") or item.get("finishTime") or item.get("end") or "结束时间未知"
    return f"{room}，座位 {seat}，{start}-{end}"


async def select_library(page, name):
    selector = await find_library_selector(page)
    if selector is None:
        raise RuntimeError("未找到图书馆选择框，请确认已进入座位预约首页。")
    current = await selector.input_value()
    if library_selected(current, name):
        print(f"图书馆已经是‘{name}’，无需切换。")
        return
    last_error = None
    for attempt in range(1, 4):
        try:
            _, options = await open_library_options(page)
            await click_library_option(page, options, name)
            await page.wait_for_timeout(300 + attempt * 200)
            selected = await selector.input_value()
            if library_selected(selected, name):
                print(f"图书馆已切换为：{selected}")
                return
            last_error = RuntimeError(f"当前显示‘{selected}’")
        except Exception as exc:
            last_error = exc
        if attempt < 3:
            await page.wait_for_timeout(300)
    raise RuntimeError(f"图书馆切换未生效：{name}；最后一次原因：{last_error}") from last_error


async def select_date(page, target_date):
    target_date = normalize_date(target_date)
    selector = page.locator("input[placeholder*='预约日期']").first
    if await selector.count() == 0:
        raise RuntimeError("未找到预约日期选择框。")
    current = (await selector.input_value()).strip()
    if current == target_date:
        print(f"预约日期已经是：{target_date}")
        return
    await selector.click()
    # This is an Element UI select, not an editable date input. Choose the
    # visible option so the app's change handler and data reload are triggered.
    options = page.locator(".el-select-dropdown__item:visible, .el-picker-panel:visible li:visible")
    try:
        await options.first.wait_for(state="visible", timeout=5000)
    except Exception:
        raise RuntimeError("预约日期下拉菜单没有打开。")
    option = None
    visible = []
    for i in range(await options.count()):
        candidate = options.nth(i)
        text = (await candidate.inner_text()).strip()
        if text:
            visible.append(text)
        if date_option_matches(text, target_date):
            option = candidate
            break
    if option is None:
        raise RuntimeError(f"预约日期‘{target_date}’尚未出现在下拉选项中；当前可选日期：{visible}")
    await option.click()
    await page.wait_for_timeout(1000)
    current = (await selector.input_value()).strip()
    if current != target_date:
        raise RuntimeError(f"预约日期切换未生效：当前为‘{current}’，目标为‘{target_date}’。请检查日期是否已开放。")
    print(f"预约日期已切换为：{current}")


async def click_and_verify_time(page, value, label, verify=True):
    matches = page.get_by_text(value, exact=True)
    candidates = []
    for i in range(await matches.count()):
        item = matches.nth(i)
        if not await item.is_visible():
            continue
        box = await item.bounding_box()
        if box:
            candidates.append((item, box["x"]))
    if not candidates:
        raise RuntimeError(f"未找到{label}时间选项：{value}")
    # The dialog has start choices on the left and end choices on the right.
    # Derive the split from the visible options instead of assuming a viewport width.
    split = (min(box_x for _, box_x in candidates) + max(box_x for _, box_x in candidates)) / 2
    matching = [pair for pair in candidates if (pair[1] < split if label == "开始" else pair[1] >= split)]
    item = matching[0][0] if matching else (min(candidates, key=lambda pair: pair[1])[0] if label == "开始" else max(candidates, key=lambda pair: pair[1])[0])
    await item.click()
    await page.wait_for_timeout(250)
    if not verify:
        return
    selected = page.locator(".seatTimeCliss:visible")
    selected_items = []
    for i in range(await selected.count()):
        node = selected.nth(i)
        box = await node.bounding_box()
        if not box:
            continue
        style = await node.evaluate("el => ({className: el.className, background: getComputedStyle(el).backgroundColor, color: getComputedStyle(el).color})")
        background = style["background"]
        blue = _looks_selected_blue(background, style["color"])
        selected_items.append((normalize_time_option(await node.inner_text()), box["x"], blue, background, style["className"]))
    if selected_items:
        split = (min(item[1] for item in selected_items) + max(item[1] for item in selected_items)) / 2
    else:
        split = 700
    relevant = [item for item in selected_items if (item[1] < split if label == "开始" else item[1] >= split)]
    active = [item[0] for item in relevant if item[2] or any(marker in str(item[4]).lower() for marker in ("active", "selected", "current"))]
    if value not in active:
        raise RuntimeError(f"{label}时间选择未确认：要求 {value}，页面实际选中 {active or '未识别'}，已停止。")


async def wait_for_time_option(page, value, label, timeout_ms=30000):
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        matches = page.get_by_text(value, exact=True)
        for i in range(await matches.count()):
            node = matches.nth(i)
            if await node.is_visible() and await node.bounding_box():
                return
        await page.wait_for_timeout(250)
    raise RuntimeError(f"{label}时间选项 {value} 未在页面刷新后出现，已停止；请检查开始时间是否实际切换。")


async def close_time_dialog(page):
    buttons = page.locator(".el-dialog:visible .el-dialog__headerbtn")
    if await buttons.count():
        await buttons.last.click()
    else:
        await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)


async def close_success_dialog(page, timeout_ms=5000):
    """Close the post-submit success UI and verify no blocking layer remains."""
    success_pattern = re.compile("预约成功")
    annotation = await _mark_success_ui(page)
    annotated_root = None
    annotated_overlays = None
    if annotation.get("found"):
        annotated_root = await _present_locator(page.locator("[data-seat-assistant-success-root='true']:visible"))
        annotated_overlays = await _present_locator(page.locator("[data-seat-assistant-success-overlay='true']:visible"))
    dialogs = page.locator(
        ".el-message-box:visible, .el-dialog:visible, [role='dialog']:visible"
    ).filter(has_text=success_pattern)
    dialog = None
    try:
        await dialogs.first.wait_for(state="visible", timeout=timeout_ms)
        dialog = dialogs.last
    except Exception:
        pass

    if dialog is not None:
        close_button = dialog.locator(".el-message-box__headerbtn, .el-dialog__headerbtn")
        if await close_button.count() and await close_button.last.is_visible():
            await close_button.last.click()
        else:
            action = dialog.get_by_role("button", name=re.compile("确定|关闭|我知道了|知道了|返回"))
            if await action.count() == 0:
                raise RuntimeError("检测到预约成功弹窗，但未找到可用的关闭按钮。")
            await action.last.click()
        try:
            await dialog.wait_for(state="hidden", timeout=timeout_ms)
        except Exception as exc:
            raise RuntimeError("预约成功弹窗未能自动关闭，已停止后续核验。") from exc
        if not await _wait_success_ui_hidden(None, annotated_root, annotated_overlays, timeout_ms):
            await page.keyboard.press("Escape")
            if not await _wait_success_ui_hidden(None, annotated_root, annotated_overlays, timeout_ms):
                raise RuntimeError("预约成功弹窗或其阻塞层未能自动关闭，已停止后续核验。")
        return True

    # Some deployments render the success view outside Element UI's standard
    # dialog classes. Mark the actual blocking wrapper and any backdrop first;
    # this prevents a page-level button from being mistaken for the close action.
    root = None
    overlays = None
    if annotation.get("found"):
        root = annotated_root
        overlays = annotated_overlays

    # Find a visible success marker, then use a close control inside its own
    # wrapper whenever possible.
    marker = None
    markers = page.get_by_text(success_pattern)
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        for index in range(await markers.count()):
            candidate = markers.nth(index)
            if await candidate.is_visible():
                marker = candidate
                break
        if marker is not None:
            break
        await page.wait_for_timeout(100)
    if marker is None:
        return False
    visible_action = await _success_close_control(root, page)
    if visible_action is None:
        if root is None:
            raise RuntimeError("检测到预约成功提示，但未找到可用的关闭按钮。")
    else:
        await visible_action.click()
    hidden = await _wait_success_ui_hidden(marker, root, overlays, timeout_ms)
    if not hidden:
        # A few custom views ignore their button's click handler while still
        # accepting Escape. Use it only after the scoped close action failed.
        await page.keyboard.press("Escape")
        hidden = await _wait_success_ui_hidden(marker, root, overlays, timeout_ms)
    if not hidden:
        raise RuntimeError("预约成功弹窗或其阻塞层未能自动关闭，已停止后续核验。")
    return True


async def _mark_success_ui(page):
    """Annotate the custom success wrapper and backdrop for reliable waiting."""
    try:
        result = await page.evaluate(
            """() => {
                const nodes = Array.from(document.querySelectorAll('body *'));
                const marker = nodes
                    .filter(node => {
                        const style = getComputedStyle(node);
                        return /预约成功/.test((node.textContent || '').trim())
                            && node.getClientRects().length > 0
                            && style.display !== 'none'
                            && style.visibility !== 'hidden';
                    })
                    .sort((a, b) => (a.textContent || '').trim().length - (b.textContent || '').trim().length)[0];
                if (!marker) return {found: false};

                let root = marker;
                let modalRoot = null;
                for (let depth = 0; root && root !== document.body && depth < 10; depth += 1, root = root.parentElement) {
                    const style = getComputedStyle(root);
                    const className = typeof root.className === 'string' ? root.className : '';
                    const modalLike = /dialog|modal|popup|layer|message|success|reserve/i.test(className)
                        || root.getAttribute('role') === 'dialog'
                        || ['fixed', 'absolute', 'sticky'].includes(style.position)
                        || Number.parseInt(style.zIndex || '0', 10) > 10;
                    if (modalLike) {
                        modalRoot = root;
                        break;
                    }
                }
                const wrapper = modalRoot || marker.parentElement || marker;
                wrapper.setAttribute('data-seat-assistant-success-root', 'true');
                const candidates = [
                    ...Array.from(wrapper.parentElement ? wrapper.parentElement.children : []),
                    ...Array.from(wrapper.querySelectorAll('*')),
                ];
                for (const node of candidates) {
                    const className = typeof node.className === 'string' ? node.className : '';
                    if (/mask|overlay|backdrop|shade/i.test(className)) {
                        node.setAttribute('data-seat-assistant-success-overlay', 'true');
                    }
                }
                return {found: true};
            }"""
        )
        return result if isinstance(result, dict) else {"found": False}
    except Exception:
        # Older test doubles and unusual pages may not expose evaluate; the
        # marker/global fallback below remains available for those cases.
        return {"found": False}


async def _success_close_control(root, page):
    selectors = (
        ".el-message-box__headerbtn, .el-dialog__headerbtn, "
        "button[aria-label*='关'], [role='button'][aria-label*='关'], "
        "button[title*='关'], [role='button'][title*='关'], "
        "button:has-text('确定'), button:has-text('关闭'), "
        "button:has-text('我知道了'), button:has-text('知道了'), button:has-text('返回'), "
        "[role='button']:has-text('确定'), [role='button']:has-text('关闭'), "
        "[role='button']:has-text('我知道了'), [role='button']:has-text('知道了'), "
        "[role='button']:has-text('返回'), .close, [class*='close']"
    )
    scopes = [root] if root is not None else [page]
    for scope in scopes:
        try:
            actions = scope.locator(selectors)
            for index in range(await actions.count()):
                candidate = actions.nth(index)
                if await candidate.is_visible():
                    return candidate
        except Exception:
            continue
    # Keep the existing role-based fallback only when no success wrapper could
    # be identified. A known wrapper must never fall back to page-level actions.
    if root is not None:
        return None
    try:
        actions = page.get_by_role("button", name=re.compile("确定|关闭|我知道了|知道了|返回"))
        for index in range(await actions.count()):
            candidate = actions.nth(index)
            if await candidate.is_visible():
                return candidate
    except Exception:
        return None
    return None


async def _wait_success_ui_hidden(marker, root, overlays, timeout_ms):
    targets = [target for target in (marker, root, overlays) if target is not None]
    for target in targets:
        try:
            await target.wait_for(state="hidden", timeout=timeout_ms)
        except Exception:
            try:
                if await target.is_visible():
                    return False
            except Exception:
                return False
    return True


async def _present_locator(locator):
    """Return a locator only when it can expose at least one matching node."""
    try:
        return locator if await locator.count() else None
    except Exception:
        return None


async def auth_diagnostics(page, request_headers):
    storage = await page.evaluate("""() => ({
        localStorage: Object.keys(localStorage),
        sessionStorage: Object.keys(sessionStorage)
    })""")
    cookies = await page.context.cookies()
    return {
        "auth_header_names": auth_header_names(request_headers),
        "cookie_names": sorted({item.get("name", "") for item in cookies if item.get("name")}),
        "local_storage_keys": sorted(storage.get("localStorage", [])),
        "session_storage_keys": sorted(storage.get("sessionStorage", [])),
    }


def _looks_selected_blue(background, color):
    # Element UI's selected time chip is blue; unselected chips are gray.
    import re
    values = re.findall(r"\d+", f"{background} {color}")
    if len(values) < 3:
        return False
    r, g, b = map(int, values[:3])
    return b >= 180 and r <= 150 and g >= 90


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default=None, help="多账号配置中的账号 ID；未指定时使用 default")
    parser.add_argument(
        "--room",
        default="",
        help="可选；不填写时使用初始化保存的位置偏好自动选择阅览室",
    )
    parser.add_argument("--room-id", type=int, help="可选，仅用于核对；实际 ID 从网页请求读取")
    parser.add_argument("--date", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--preferred", nargs="*", default=[])
    parser.add_argument("--submit", action="store_true", help="允许真实提交；默认直接提交并自动核验")
    parser.add_argument("--confirm-submit", action="store_true", help="调试护栏：与 --submit 一起使用时要求输入 SUBMIT")
    return parser.parse_args(argv)


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
