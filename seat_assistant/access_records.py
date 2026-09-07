"""Access-record parsing and the browser-backed provider boundary."""

import asyncio
from datetime import date, datetime, time
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlencode, urlsplit, urlunsplit, parse_qsl


class AuthenticationError(RuntimeError):
    """The browser session can no longer authenticate API requests."""


def is_authentication_error(message: str) -> bool:
    text = str(message or "").lower()
    return (
        "code=12" in text
        or "http 401" in text
        or "http 403" in text
        or "登录失败" in text
        or "认证失败" in text
        or "登录验证码" in text
        or "验证码视觉模型" in text
    )


class AccessRecordProvider(Protocol):
    async def records(self, day: str) -> list[dict]:
        ...


class BrowserAccessRecordProvider:
    """Keep one authenticated browser page for low-frequency access polling."""

    def __init__(self, settings):
        self.settings = settings
        self.browser = None
        self.context = None
        self.page = None
        self.activation_code = ""
        self.api_auth = {"headers": {}, "token": ""}
        self.capture_tasks = set()

    async def open(self):
        from .browser_session import LockedBrowser
        from scripts.preview_reservation import capture_page_request, login_if_configured, wait_for_authenticated_page

        configured_profile = getattr(self.settings, "monitor_profile_path", "")
        profile = Path(configured_profile or Path(self.settings.profile_path).parent / "monitor-profile")
        try:
            await self._open_context(profile, headless=True)
            await self.page.goto(self.settings.login_url, wait_until="domcontentloaded")
            logged_in = await login_if_configured(self.page, self.settings)
            if not logged_in and "#/login" in self.page.url:
                raise AuthenticationError("未能自动登录，无法读取预约或门禁记录")
            await wait_for_authenticated_page(self.page, timeout_ms=30000)
            await self.health_check()
            return await self._finish_open()
        except Exception as exc:
            should_fallback = isinstance(exc, AuthenticationError) or is_authentication_error(str(exc))
            if should_fallback:
                await self.invalidate_authentication()
            await self.close()
            if not should_fallback or getattr(self.settings, "dynamic_manual_login_timeout_seconds", 0) <= 0:
                if should_fallback:
                    raise AuthenticationError(str(exc)) from exc
                raise
            return await self._open_manual(profile)

    async def _open_context(self, profile: Path, headless: bool):
        from .browser_session import LockedBrowser, prepare_context_page
        from scripts.preview_reservation import capture_page_request

        self.browser = LockedBrowser(profile, headless=headless)
        self.context = await self.browser.__aenter__()
        self.page = await prepare_context_page(self.context)

        def capture_request(request):
            task = asyncio.create_task(capture_page_request(self.api_auth, request))
            self.capture_tasks.add(task)
            task.add_done_callback(self.capture_tasks.discard)

        self.page.on("request", capture_request)

    async def _finish_open(self):
        if self.settings.access_records_url:
            self.activation_code = await read_activation_code(self.page)
        return self

    async def _open_manual(self, profile: Path):
        from scripts.preview_reservation import wait_for_authenticated_page

        deadline = asyncio.get_running_loop().time() + self.settings.dynamic_manual_login_timeout_seconds
        try:
            await self._open_context(profile, headless=False)
            await self.page.goto(self.settings.login_url, wait_until="domcontentloaded")
            print("动态监控自动认证失败，请在弹出的独立浏览器窗口中完成登录。", flush=True)
            while asyncio.get_running_loop().time() < deadline:
                if self.page.is_closed():
                    break
                try:
                    await wait_for_authenticated_page(self.page, timeout_ms=1000)
                    await self.health_check()
                    return await self._finish_open()
                except Exception:
                    await self.page.wait_for_timeout(1000)
        except Exception:
            pass
        await self.close()
        raise AuthenticationError("独立监控会话人工登录超时或健康检查未通过")

    async def close(self):
        capture_tasks, self.capture_tasks = self.capture_tasks, set()
        for task in capture_tasks:
            if not task.done():
                task.cancel()
        if capture_tasks:
            await asyncio.gather(*capture_tasks, return_exceptions=True)
        if self.browser is not None:
            browser, self.browser = self.browser, None
            self.context = None
            self.page = None
            self.activation_code = ""
            self.api_auth = {"headers": {}, "token": ""}
            await browser.__aexit__(None, None, None)

    async def invalidate_authentication(self) -> None:
        """Remove stale browser credentials before an authentication rebuild."""
        page, context = self.page, self.context
        self.api_auth = {"headers": {}, "token": ""}
        self.activation_code = ""
        if page is not None:
            try:
                if not page.is_closed():
                    await page.evaluate(
                        """() => {
                            localStorage.clear();
                            sessionStorage.clear();
                        }"""
                    )
            except Exception:
                pass
        if context is not None:
            try:
                await context.clear_cookies()
            except Exception:
                pass

    async def health_check(self) -> list[dict]:
        """Require a successful authenticated reservation API request."""
        if self.page is None:
            raise RuntimeError("监控浏览器会话尚未建立")
        from scripts.preview_reservation import fetch_current_reservations, wait_for_api_auth

        auth = await wait_for_api_auth(self.page, self.api_auth)
        try:
            return await fetch_current_reservations(self.page, auth)
        except RuntimeError as exc:
            if is_authentication_error(str(exc)):
                raise AuthenticationError(str(exc)) from exc
            raise

    async def reservation_records(self, day: str) -> list[dict]:
        """Read the site's full “我的预约” records in the same session.

        Uses the same merged history+current query as the booking flow so the
        monitor still sees a new reservation while the paginated history view
        is syncing.
        """
        if self.page is None:
            raise RuntimeError("预约查询浏览器会话尚未建立")
        from scripts.preview_reservation import fetch_user_reservations, wait_for_api_auth
        from .submission import _extract_date

        auth = await wait_for_api_auth(self.page, self.api_auth)
        try:
            records = await fetch_user_reservations(self.page, auth)
        except RuntimeError as exc:
            if is_authentication_error(str(exc)):
                raise AuthenticationError(str(exc)) from exc
            raise
        return [
            record
            for record in records or []
            if isinstance(record, dict) and (_extract_date(record) or "") == day
        ]

    async def records(self, day: str) -> list[dict]:
        if self.page is None or not self.settings.access_records_url or not self.activation_code:
            raise RuntimeError("门禁记录浏览器会话尚未建立或接口地址未配置")
        try:
            return await fetch_access_records(
                self.page,
                self.settings.access_records_url,
                self.activation_code,
            )
        except RuntimeError as exc:
            if is_authentication_error(str(exc)):
                raise AuthenticationError(str(exc)) from exc
            raise


def _record_day(record: dict) -> str:
    for key in ("date", "day", "onDate", "reservationDate", "reserveDate"):
        value = str(record.get(key, "")).strip()
        if value:
            match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
            if match:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
            return value[:10]
    return ""


def extract_activation_code(text: str) -> str:
    match = re.search(r"激活码\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9_-]{3,})", text or "")
    if not match:
        raise ValueError("激活码弹窗中没有找到有效激活码")
    return match.group(1)


def first_entry_time(records: list[dict], day: str) -> datetime | None:
    entries = []
    for record in records or []:
        if not isinstance(record, dict) or not _is_entry_record(record):
            continue
        value = _record_timestamp(record)
        if value is None or value.date().isoformat() != day:
            continue
        entries.append(value)
    return min(entries) if entries else None


def _is_entry_record(record: dict) -> bool:
    direction = " ".join(
        str(record.get(key, ""))
        for key in ("type", "direction", "event", "action", "recordType", "remark", "name")
    ).strip().lower()
    if any(_direction_contains(direction, marker) for marker in ("出馆", "离馆", "out", "leave", "exit")):
        return False
    if any(_direction_contains(direction, marker) for marker in ("入馆", "进馆", "entry", "enter", "in")):
        return True
    return any(key in record for key in ("entryTime", "entry_time", "inTime", "in_time"))


def _direction_contains(direction: str, marker: str) -> bool:
    if marker.isascii() and marker.isalpha():
        return re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", direction) is not None
    return marker in direction


def _record_timestamp(record: dict) -> datetime | None:
    for key in (
        "timestamp", "datetime", "dateTime", "accessTime", "access_time", "recordTime", "record_time",
        "occurredAt", "occurred_at", "entryTime", "entry_time", "inTime", "in_time", "time",
    ):
        if record.get(key) in (None, ""):
            continue
        value = record[key]
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            return parsed.replace(tzinfo=None)
        except ValueError:
            pass
        try:
            parsed_time = time.fromisoformat(text)
        except ValueError:
            continue
        day = str(record.get("date") or record.get("day") or "").strip()
        if not day:
            continue
        try:
            return datetime.combine(datetime.fromisoformat(day).date(), parsed_time)
        except ValueError:
            continue
    return None


async def read_activation_code(page) -> str:
    labels = page.get_by_text("激活码", exact=True)
    if await labels.count() == 0:
        raise RuntimeError("登录页面没有找到激活码入口")
    await labels.last.click()
    dialogs = page.locator(".el-dialog:visible, [role='dialog']:visible, .el-dialog__wrapper:visible")
    await dialogs.first.wait_for(state="visible", timeout=5000)
    dialog = dialogs.last
    try:
        text = await dialog.inner_text()
        code = extract_activation_code(text)
    finally:
        await close_visible_dialog(page, dialog, timeout_ms=3000)
    return code


async def close_visible_dialog(page, dialog, timeout_ms: int = 3000) -> None:
    """Close a known modal and verify it no longer blocks the page."""
    buttons = dialog.locator(
        ".el-dialog__headerbtn, .el-message-box__headerbtn, "
        "button[aria-label*='关'], [role='button'][aria-label*='关']"
    )
    try:
        count = await buttons.count()
    except Exception:
        count = 0
    clicked = False
    if count:
        target = buttons.last
        try:
            if await target.is_visible():
                await target.click()
                clicked = True
        except Exception:
            clicked = False
    if not clicked:
        try:
            actions = dialog.get_by_role("button", name=re.compile("关闭|确定|我知道了|知道了|返回"))
            for index in range(await actions.count()):
                target = actions.nth(index)
                if await target.is_visible():
                    await target.click()
                    clicked = True
                    break
        except Exception:
            pass
    if not clicked:
        await page.keyboard.press("Escape")
    try:
        await dialog.wait_for(state="hidden", timeout=timeout_ms)
        return
    except Exception as first_error:
        try:
            await page.keyboard.press("Escape")
            await dialog.wait_for(state="hidden", timeout=timeout_ms)
            return
        except Exception as second_error:
            raise RuntimeError("弹窗关闭后仍可见，已停止复用当前浏览器会话") from second_error


async def fetch_access_records(page, endpoint: str, activation_code: str, label: str = "读取门禁记录") -> list[dict]:
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        raise RuntimeError("未配置门禁记录接口地址；请先完成门禁接口校准")
    if "{activation_code}" in endpoint or "{code}" in endpoint:
        url = endpoint.replace("{activation_code}", quote(activation_code, safe="")).replace("{code}", quote(activation_code, safe=""))
    else:
        parts = urlsplit(endpoint)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault("activation_code", activation_code)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    payload = await page.evaluate(
        """async ({url}) => {
            const response = await fetch(url, {credentials: 'include', cache: 'no-store'});
            const text = await response.text();
            let body = {};
            try { body = text ? JSON.parse(text) : {}; } catch (_) { body = {message: text}; }
            return {status: response.status, body};
        }""",
        {"url": url},
    )
    if payload.get("status") != 200:
        raise RuntimeError(f"{label}失败：HTTP {payload.get('status')}")
    body = payload.get("body") or {}
    if isinstance(body, dict) and body.get("code") not in (None, 0, "0"):
        raise RuntimeError(f"{label}失败：code={body.get('code')}，message={body.get('message') or '无'}")
    records = _record_list(body)
    if records is None:
        raise RuntimeError(f"{label}返回结构无法识别")
    return records


def _record_list(value) -> list[dict] | None:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("records", "list", "rows", "items", "data", "result"):
            if key in value:
                found = _record_list(value[key])
                if found is not None:
                    return found
    return None
