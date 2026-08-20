"""Open a reservation preview and stop before the submit button."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seat_assistant.calibration import sanitize_url
from seat_assistant.auth_flow import auth_header_names, captcha_input_selectors, credentials_available, is_seat_app_url, library_selected, normalize_library
from seat_assistant.config import _load_dotenv
from seat_assistant.date_selection import date_option_matches, normalize_date
from seat_assistant.end_times import parse_native_end_times
from seat_assistant.booking_window import validate_booking_date
from seat_assistant.preview import layout_from_response, layout_request_matches, normalize_room_name, preview_seat_candidates
from seat_assistant.seat_inventory import seats_from_layout
from seat_assistant.submission import confirmation_required, end_time_response_matches_start, normalize_time_option, requested_times_available, reservation_matches, submission_settled, time_option_id, time_values, validate_half_hour_time

SITE_URL = os.getenv("SEAT_LOGIN_URL", "https://seatlib.hpu.edu.cn/libseat/")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = Path(".browser-profile").resolve()


async def main(args):
    _load_dotenv()
    validate_booking_date(args.date, __import__('datetime').datetime.now())
    args.start = validate_half_hour_time(args.start)
    args.end = validate_half_hour_time(args.end)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(str(PROFILE), executable_path=CHROME, headless=False, viewport={"width": 1440, "height": 900})
        page = context.pages[0] if context.pages else await context.new_page()
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
        # Refuse to submit when an existing reservation is already present for
        # the requested day; this prevents duplicate reservations on reruns.
        existing = await page.evaluate("""async () => (await (await fetch('/rest/v2/user/reservations')).json()).data""")
        if existing and any(str(item.get('date', item.get('day', ''))) == args.date for item in existing if isinstance(item, dict)):
            raise RuntimeError(f"你在 {args.date} 已经存在预约，已停止，不会重复提交。")
        if confirmation_required(args.submit, input("当前页面已选好座位和时间。输入 SUBMIT 才会提交，直接回车保持预览：")):
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
            input("请在浏览器中检查状态后按回车关闭：")
            await context.close()
            return
        await page.wait_for_timeout(1000)
        page_text = await page.locator("body").inner_text()
        print("提交后的页面提示：", " ".join(page_text.split())[-500:])
        print("正在打开‘我的预约’核验……")
        await page.get_by_text("我的预约", exact=True).last.click()
        try:
            await page.wait_for_function("() => document.body.innerText.includes('我的预约') && !document.body.innerText.includes('正在加载')", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)
        verification_text = await page.locator("body").inner_text()
        if reservation_matches(verification_text, args.date, args.room, selected.number, args.start, args.end):
            print(f"核验成功：{args.date}，{args.room}，座位 {selected.number}，{args.start}-{args.end}")
        else:
            print("核验结果不明确：请在‘我的预约’页面手动确认，程序不会重复提交。")
        print(f"页面：{sanitize_url(page.url)}")
        input("确认页面选择正确后按回车关闭预览：")
        await context.close()


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
    parser.add_argument("--submit", action="store_true", help="允许真实提交；仍需终端输入 SUBMIT")
    asyncio.run(main(parser.parse_args()))
