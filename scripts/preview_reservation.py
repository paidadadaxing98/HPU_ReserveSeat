"""Open a reservation preview and stop before the submit button."""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seat_assistant.calibration import sanitize_url
from seat_assistant.auth_flow import auth_header_names, browser_api_headers, captcha_input_selectors, credentials_available, is_seat_app_url, library_selected, normalize_library
from seat_assistant.config import _load_dotenv
from seat_assistant.date_selection import date_option_matches, normalize_date
from seat_assistant.end_times import parse_native_end_times
from seat_assistant.booking_window import validate_booking_date
from seat_assistant.notifications import WeComNotifier, send_reservation_notification
from seat_assistant.preview import layout_from_response, layout_request_matches, normalize_room_name, preview_seat_candidates
from seat_assistant.reservation import SeatResult
from seat_assistant.seat_inventory import seats_from_layout
from seat_assistant.submission import active_reservations_for_day, confirmation_required, day_reservations, end_time_response_matches_start, find_matching_reservation, find_similar_reservation, history_page_records, normalize_time_option, requested_times_available, reservation_matches, submission_settled, time_option_id, time_values, validate_half_hour_time

SITE_URL = os.getenv("SEAT_LOGIN_URL", "https://seatlib.hpu.edu.cn/libseat/")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = Path(".browser-profile").resolve()


async def main(args):
    _load_dotenv()
    notifier = WeComNotifier(os.getenv("SEAT_WECOM_WEBHOOK", ""))
    validate_booking_date(args.date, __import__('datetime').datetime.now())
    args.start = validate_half_hour_time(args.start)
    args.end = validate_half_hour_time(args.end)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(str(PROFILE), executable_path=CHROME, headless=False, viewport={"width": 1440, "height": 900})
        page = context.pages[0] if context.pages else await context.new_page()
        api_auth = {"headers": {}, "token": ""}
        capture_tasks = set()

        def capture_request(request):
            task = asyncio.create_task(capture_page_request(api_auth, request))
            capture_tasks.add(task)
            task.add_done_callback(capture_tasks.discard)

        page.on("request", capture_request)
        try:
            await page.goto(os.getenv("SEAT_LOGIN_URL", SITE_URL), wait_until="domcontentloaded")
            # The seat app may briefly render #/login while its SSO redirect
            # exchanges the ticket and adds the authenticated token.
            if not is_seat_app_url(page.url) and "#/login" in page.url:
                try:
                    await page.wait_for_url("**/libseat/**#/home", timeout=15000)
                except Exception:
                    pass
            logged_in = await login_if_configured(page)
            if not logged_in:
                if "#/login" in page.url:
                    print("当前是座位系统登录状态失效，正在重新打开统一认证入口……")
                    await page.goto(os.getenv("SEAT_LOGIN_URL", SITE_URL), wait_until="domcontentloaded")
                    logged_in = await login_if_configured(page)
                if not logged_in:
                    print("第 1 步（手动）：请完成登录，直到进入‘自选座位’首页。")
                    input("登录完成后按回车：")
            print("正在确认进入座位预约首页……")
            await page.wait_for_url("**/libseat/**", timeout=30000)
            print("程序操作：自动选择图书馆‘南校区第二图书馆’。")
            await select_library(page, "南校区第二图书馆")
            print(f"程序操作：自动选择预约日期‘{args.date}’。")
            await select_date(page, args.date)
            existing = await fetch_user_reservations(page, api_auth)
            print(f"当天预约记录：{daily_reservation_details(existing, args.date) or '无'}")
            active_today = active_reservations_for_day(existing, args.date)
            if active_today:
                details = "；".join(reservation_summary(item) for item in active_today)
                print(f"当天已经存在有效预约，学校限制一次只能预约一个时间段，跳过本次预约：{details}")
                await context.close()
                return
            similar = find_similar_reservation(existing, args.date, args.room, args.start, args.end)
            if similar:
                print(f"首页已检测到相近预约，跳过选座和提交：{reservation_summary(similar)}")
                await context.close()
                return
            print(f"程序操作：自动点击阅览室‘{args.room}’，请不要在网页上点击阅览室。")
            async with page.expect_response(lambda response: layout_request_matches(response.url), timeout=30000) as response_info:
                await page.get_by_text(args.room, exact=True).first.click()
            response = await response_info.value
        except Exception as exc:
            print(f"流程暂停：{exc}")
            print(f"浏览器当前页面：{sanitize_url(page.url)}")
            input("请检查浏览器页面；确认后按回车关闭浏览器：")
            return
        body = await response.json()
        print(f"座位布局接口：HTTP {response.status}，code={body.get('code')}，message={body.get('message')}")
        layout = layout_from_response(body)
        actual_room_id = layout.get("id")
        print(f"网页返回阅览室：{layout.get('name')}，ID={actual_room_id}")
        if normalize_room_name(layout.get("name", "")) != normalize_room_name(args.room):
            raise ValueError(f"网页返回的阅览室是‘{layout.get('name')}’，不是‘{args.room}’。请检查图书馆选择。")
        if args.room_id is not None and args.room_id != actual_room_id:
            print(f"提示：命令中的 room-id={args.room_id} 与网页实际 ID={actual_room_id} 不一致；本次使用网页实际 ID。")
        seats = seats_from_layout(layout)
        selected = None
        request_headers = {}
        candidate_errors = []
        for candidate in preview_seat_candidates(seats, args.preferred):
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
                if not requested_times_available(normalized_start, [args.start]):
                    raise RuntimeError(f"可选开始时间为 {normalized_start or '未知'}，不包含 {args.start}")
                start_id = time_option_id(candidate_start_body, "startTimes", args.start)
                if not start_id:
                    raise RuntimeError(f"开始时间 {args.start} 缺少网页返回的原生 id，已停止。")
                async with page.expect_response(lambda r: end_time_response_matches_start(r.url, start_id), timeout=15000) as end_info:
                    await click_and_verify_time(page, args.start, "开始", verify=True)
                candidate_end_response = await end_info.value
                candidate_end_body = await candidate_end_response.json()
                candidate_end_headers = await candidate_end_response.request.all_headers()
                record_api_auth(api_auth, candidate_end_response.request.url, candidate_end_headers)
                native_end = parse_native_end_times(candidate_end_response.url, candidate_end_body)
                candidate_end = list(native_end.options)
                end_url = native_end.url
                if not native_end.ok:
                    raise RuntimeError(
                        f"结束时间接口失败：URL={end_url}，code={candidate_end_body.get('code')}，"
                        f"message={candidate_end_body.get('message') or '无'}"
                    )
                if not requested_times_available(candidate_end, [args.end]):
                    raise RuntimeError(
                        f"可选结束时间为 {candidate_end or '未知'}，不包含 {args.end}；"
                        f"接口 URL={end_url}，code={candidate_end_body.get('code')}，"
                        f"message={candidate_end_body.get('message') or '无'}"
                    )
                await wait_for_time_option(page, args.end, "结束")
                await click_and_verify_time(page, args.end, "结束")
            except Exception as exc:
                candidate_errors.append(f"{candidate.number}: {exc}")
                if page.is_closed():
                    raise RuntimeError(f"浏览器页面已关闭，原始座位错误：{exc}") from exc
                await close_time_dialog(page)
                continue
            selected = candidate
            request_headers = candidate_headers
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
        active_today = active_reservations_for_day(existing, args.date)
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
        phrase = input("当前页面已选好座位和时间。输入 SUBMIT 才会提交，直接回车保持预览：") if args.confirm_submit else ""
        if confirmation_required(args.submit, args.confirm_submit, phrase):
            print("预览已完成：页面停在‘立即预约’前。没有提交预约。")
            print(f"页面：{sanitize_url(page.url)}")
            input("确认页面选择正确后按回车关闭预览：")
            await context.close()
            return
        print("正在提交一次真实预约……")
        await page.get_by_role("button", name="立即预约").click()
        try:
            await page.wait_for_function("() => !document.body.innerText.includes('正在玩命预约中') && !document.body.innerText.includes('玩命预约')", timeout=30000)
        except Exception:
            print("提交请求超过 30 秒仍未结束，结果不明确；不会重复提交。")
            send_preview_notification(
                notifier,
                args,
                SeatResult(False, args.room, selected.number, "提交请求超过 30 秒仍未结束", conclusive=False),
            )
            input("请在浏览器中检查状态后按回车关闭：")
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
            page, api_auth, args.date, args.room, selected.number, args.start, args.end, submission_signal=submission_signal
        )
        if verification_status == "success":
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
        else:
            print(f"核验结果不明确：{verification_message or '请在‘我的预约’页面手动确认'}；程序不会重复提交。")
            send_preview_notification(
                notifier,
                args,
                SeatResult(False, args.room, selected.number, verification_message or "提交后核验结果不明确", conclusive=False),
            )
        print(f"页面：{sanitize_url(page.url)}")
        if not args.submit or args.confirm_submit:
            input("确认页面选择正确后按回车关闭预览：")
        await context.close()


def send_preview_notification(notifier, args, result) -> bool:
    sent = send_reservation_notification(notifier, args.date, "手动", result, args.start, args.end)
    if sent:
        print("企业微信通知已发送。")
    else:
        print("企业微信通知未发送（未配置或发送失败）。")
    return sent


def reservation_verification_status(
    reservations: list[dict],
    page_text: str,
    day: str,
    room: str,
    seat: str,
    start: str,
    end: str,
    submission_signal: tuple[str, str] = ("", ""),
) -> tuple[str, dict | None, str]:
    all_today = day_reservations(reservations, day)
    active_today = active_reservations_for_day(all_today, day)
    all_details = daily_reservation_details(all_today, day)
    matched = find_matching_reservation(reservations, day, room, seat, start, end)
    if matched:
        return "success", matched, f"网页历史记录已确认；当天全部预约：{all_details}"
    normalized = " ".join((page_text or "").replace("：", ":").split())
    for marker in ("当天已有预约", "已有预约", "只能预约一个", "预约失败", "座位已被占用", "不能预约", "无法预约", "预约冲突"):
        if marker in normalized:
            suffix = f"；当天全部预约：{all_details}" if all_details else ""
            return "failed", None, f"{marker}{suffix}"
    if active_today:
        return "failed", None, f"当天已有其他有效预约，本次请求未生效；当天全部预约：{all_details}"
    if submission_signal[0] == "success":
        return "uncertain", None, f"页面提示预约成功，但历史接口尚未出现匹配记录；当天全部预约：{all_details or '无'}"
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


async def wait_for_reservation_confirmation(
    page,
    auth_state: dict,
    day: str,
    room: str,
    seat: str,
    start: str,
    end: str,
    timeout_ms: int = 15000,
    submission_signal: tuple[str, str] = ("", ""),
) -> tuple[str, dict | None, str]:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    last_error = ""
    while asyncio.get_running_loop().time() < deadline:
        try:
            reservations = await fetch_user_reservations(page, auth_state)
            page_text = await page.locator("body").inner_text()
            status, matched, message = reservation_verification_status(
                reservations, page_text, day, room, seat, start, end, submission_signal
            )
            if status != "uncertain":
                return status, matched, message
        except Exception as exc:
            last_error = str(exc)
        await page.wait_for_timeout(500)
    return "uncertain", None, last_error or "提交后未在我的预约历史中找到完全匹配记录"


async def login_if_configured(page):
    account = os.getenv("SEAT_ACCOUNT", "").strip()
    password = os.getenv("SEAT_PASSWORD", "").strip()
    if not credentials_available(account, password):
        print("SEAT_ACCOUNT/SEAT_PASSWORD 为空，将复用浏览器会话；若未登录请先手动登录。")
        return False
    if is_seat_app_url(page.url):
        print("浏览器会话已经登录，跳过账号密码填写。")
        return True
    if "#/login" in page.url:
        try:
            await page.wait_for_url("**/libseat/**#/home", timeout=15000)
            print("座位系统已通过现有会话登录，跳过账号密码填写。")
            return True
        except Exception:
            pass
    print("检测到本地凭据，正在自动填写统一身份认证。")
    captcha = page.locator(", ".join(captcha_input_selectors())).first
    if await captcha.count() and await captcha.is_visible():
        print("检测到登录验证码，请在浏览器中完成验证码和登录。")
        input("登录成功并进入座位预约首页后按回车：")
        if not is_seat_app_url(page.url):
            await page.wait_for_url("**/libseat/**", timeout=30000)
        return True
    user = page.locator("input[name='username'], input[name='userName'], input[placeholder*='账号'], input[placeholder*='用户名']").first
    pwd = page.locator("input[type='password'], input[name='password']").first
    if await user.count() == 0 or await pwd.count() == 0:
        raise RuntimeError("未找到统一身份认证输入框，请清空凭据后手动登录，或检查登录页面变化。")
    await user.fill(account)
    await pwd.fill(password)
    captcha = page.locator(", ".join(captcha_input_selectors())).first
    if await captcha.count() and await captcha.is_visible():
        print("检测到登录验证码。账号密码已填写，请在浏览器中输入图片验证码并点击登录。")
        input("完成验证码并进入座位预约首页后按回车：")
        await page.wait_for_url("**/libseat/**", timeout=30000)
        return True
    submit = page.locator("button[type='submit'], input[type='submit'], button:has-text('登录'), input[value*='登录']").first
    if await submit.count() == 0:
        raise RuntimeError("未找到登录按钮，请清空凭据后手动登录。")
    await submit.click()
    await page.wait_for_url("**/libseat/**", timeout=30000)
    return True


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
    auth = await wait_for_api_auth(page, auth_state)
    history_error = None
    current_error = None
    try:
        history = await fetch_reservation_history(page, auth)
    except RuntimeError as exc:
        history = []
        history_error = str(exc)
    try:
        current = await fetch_current_reservations(page, auth)
    except RuntimeError as exc:
        current = []
        current_error = str(exc)
    if history_error and current_error:
        raise RuntimeError(
            "无法读取预约历史或当前预约，已停止："
            f"历史接口：{history_error}；当前预约接口：{current_error}"
        )
    return unique_reservation_records(history + current)


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
    target = normalize_library(name)
    selector = page.locator("input[placeholder*='场馆地点']").first
    if await selector.count() == 0:
        raise RuntimeError("未找到图书馆选择框，请确认已进入座位预约首页。")
    current = await selector.input_value()
    if library_selected(current, name):
        print(f"图书馆已经是‘{name}’，无需切换。")
        return
    # Click the full Element UI select container and wait for its visible menu.
    await selector.locator("xpath=ancestor::div[contains(@class,'el-select')][1]").click()
    options = page.locator(".el-select-dropdown__item:visible")
    try:
        await options.first.wait_for(state="visible", timeout=5000)
    except Exception:
        raise RuntimeError(f"图书馆下拉菜单没有打开；当前输入框内容为‘{await selector.input_value()}’。")
    option = None
    visible_names = []
    for i in range(await options.count()):
        candidate = options.nth(i)
        text = (await candidate.inner_text()).strip()
        visible_names.append(text)
        if normalize_library(text) == target:
            option = candidate
            break
    if option is None:
        raise RuntimeError(f"未找到图书馆选项‘{name}’；当前可见选项：{visible_names}")
    await option.click()
    await page.wait_for_timeout(800)
    selected = await selector.input_value()
    if not library_selected(selected, name):
        raise RuntimeError(f"图书馆切换未生效，当前显示‘{selected}’。")
    print(f"图书馆已切换为：{selected}")


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--room", required=True)
    parser.add_argument("--room-id", type=int, help="可选，仅用于核对；实际 ID 从网页请求读取")
    parser.add_argument("--date", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--preferred", nargs="*", default=[])
    parser.add_argument("--submit", action="store_true", help="允许真实提交；默认直接提交并自动核验")
    parser.add_argument("--confirm-submit", action="store_true", help="调试护栏：与 --submit 一起使用时要求输入 SUBMIT")
    asyncio.run(main(parser.parse_args()))
