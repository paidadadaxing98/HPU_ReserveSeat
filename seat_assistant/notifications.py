import json
import logging
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen


_PERIOD_LABELS = {
    "morning": "上午",
    "afternoon": "下午",
    "evening": "晚上",
    "period04": "第4段",
    "period05": "第5段",
}

_WEBHOOK_URL = "https://seatlib.hpu.edu.cn/libseat/"
_LOGIN_URL = "https://seatlib.hpu.edu.cn/libseat/#/login"


def render_reservation(day: str, period: str, result, start: str, end: str, account_label: str | None = None) -> str:
    display = account_label or "未提供"
    try:
        weekday = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[date.fromisoformat(day).weekday()]
    except (TypeError, ValueError):
        weekday = ""
    if result.success:
        status = "已确认"
        title = "预约成功确认"
    elif not result.conclusive and str(result.message or "").lstrip().startswith("已提交"):
        status = "已提交，待核验"
        title = "预约提交确认"
    elif result.conclusive:
        status = "预约失败"
        title = "预约失败通知"
    else:
        status = "结果不明确"
        title = "预约结果通知"
    explanation = result.message or "无"
    date_value = f"**{day}**{f' ({weekday})' if weekday else ''}"
    room = result.room or "未提供"
    return "\n".join(
        [
            f"## 📌 {title}",
            "",
            "---",
            "",
            "| 🔑 **账号** | `" + display + "` |",
            "| ----------- | ------------------- |",
            f"| 📅 **日期** | {date_value} |",
            f"| ⏰ **时段** | **{start} — {end}** |",
            f"| 🏛️ **阅览室** | **{room}** |",
            f"| 💺 **座位** | **{result.seat or '未提供'}** |",
            f"| ✅ **状态** | {status}（{explanation}） |",
            "",
            "---",
        ]
    )


def send_reservation_notification(notifier, day: str, period: str, result, start: str, end: str, account_label: str | None = None) -> bool:
    if notifier is None:
        return False
    try:
        card = reservation_card(day, period, result, start, end, account_label, getattr(notifier, "login_url", None))
        send_card = getattr(notifier, "send_template_card", None)
        return bool(send_card(card) if send_card else notifier.send(render_reservation(day, period, result, start, end, account_label)))
    except Exception as exc:
        logging.getLogger(__name__).warning("预约通知发送失败：%s", exc)
        return False


def render_scheduler_summary(account_id: str, day: str, summary: dict, account_label: str | None = None) -> str:
    status = str(summary.get("status") or "completed")
    label = {"reserved": "已完成", "skipped": "已跳过", "uncertain": "结果不明确", "completed": "已运行"}.get(status, status)
    display = account_label or account_id
    lines = [
        "## 📋 定时任务结果",
        "",
        "---",
        "",
        f"| 🔑 **账号** | `{display}` |",
        "| ----------- | ------------------- |",
        f"| 📅 **日期** | **{day}** |",
        f"| ✅ **状态** | **{label}** |",
    ]
    if summary.get("message"):
        lines.append(f"| 📝 **说明** | {summary['message']} |")
    ordered_periods = ("morning", "afternoon", "evening", "period04", "period05")
    seen = set()
    for period in ordered_periods:
        result = summary.get(period)
        if not isinstance(result, dict):
            continue
        seen.add(period)
        period_label = _PERIOD_LABELS.get(period, period)
        item_status = result.get("status", "unknown")
        item_message = result.get("message", "")
        lines.append(f"| {period_label} | {item_status}{f'，{item_message}' if item_message else ''} |")
    for period, result in summary.items():
        if period in {"status", "message", "account_id"} or period in seen or not isinstance(result, dict):
            continue
        period_label = _PERIOD_LABELS.get(period, period)
        item_status = result.get("status", "unknown")
        item_message = result.get("message", "")
        lines.append(f"| {period_label} | {item_status}{f'，{item_message}' if item_message else ''} |")
    return "\n".join(lines)


def render_initialization(account_id: str, state: dict, account_label: str | None = None) -> str:
    status = "初始化完成" if state.get("status") == "ready" else "初始化失败"
    display = account_label or account_id
    lines = [
        "## 🔧 初始化验证结果",
        "",
        "---",
        "",
        f"| 🔑 **账号** | `{display}` |",
        "| ----------- | ------------------- |",
        f"| ✅ **状态** | **{status}** |",
    ]
    if state.get("last_verified_at"):
        lines.append(f"| 🕒 **验证时间** | **{state['last_verified_at']}** |")
    if state.get("message"):
        lines.append(f"| 📝 **说明** | {state['message']} |")
    return "\n".join(lines)


def send_initialization_notification(notifier, account_id: str, state: dict, account_label: str | None = None) -> bool:
    if notifier is None:
        return False
    try:
        text = render_initialization(account_id, state, account_label)
        send_card = getattr(notifier, "send_template_card", None)
        return bool(send_card(_text_card("初始化验证结果", text, getattr(notifier, "login_url", None))) if send_card else notifier.send(text))
    except Exception as exc:
        logging.getLogger(__name__).warning("初始化通知发送失败：%s", exc)
        return False


def send_scheduler_notification(notifier, account_id: str, day: str, summary: dict, account_label: str | None = None) -> bool:
    if notifier is None:
        return False
    try:
        text = render_scheduler_summary(account_id, day, summary, account_label)
        send_card = getattr(notifier, "send_template_card", None)
        return bool(send_card(_text_card("定时任务结果", text, getattr(notifier, "login_url", None))) if send_card else notifier.send(text))
    except Exception as exc:
        logging.getLogger(__name__).warning("定时任务通知发送失败：%s", exc)
        return False


def render_tweet_push(account_id: str, user_name: str, title: str, url: str, note: str | None = None) -> str:

    lines = [
        f"账号：{account_id}",
        f"接收人：{user_name}",
        f"推文：{title}",
        f"链接：{url}",
    ]

    if note:

        lines.append(f"备注：{note}")

    return "\n".join(lines)


def _text_card(title: str, text: str, login_url: str = _LOGIN_URL) -> dict:
    return {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "text_notice",
            "main_title": {"title": title},
            "sub_title_text": text[:112],
            "horizontal_content_list": [],
            "card_action": {"type": 1, "url": _LOGIN_URL},
        },
    }


def reservation_card(day: str, period: str, result, start: str, end: str, account_label: str | None = None, login_url: str | None = None) -> dict:
    label = _PERIOD_LABELS.get(period, period)
    if result.success:
        title, status = "预约成功确认", "已确认"
    elif not result.conclusive:
        title, status = "预约结果待核验", "结果不明确"
    else:
        title, status = "预约失败通知", "预约失败"
    explanation = result.message or "无"
    fields = [
        {"keyname": "账号", "value": account_label or "未提供"},
        {"keyname": "日期", "value": day},
        {"keyname": "时段", "value": f"{start} — {end}"},
        {"keyname": "阅览室", "value": result.room or "未提供"},
        {"keyname": "座位", "value": result.seat or "未提供"},
        {"keyname": "状态", "value": f"{status}（{explanation}）"},
    ]
    return {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "text_notice",
            "main_title": {"title": title, "desc": f"{day} {label}"},
            "horizontal_content_list": fields,
            "card_action": {"type": 1, "url": _LOGIN_URL},
        },
    }


class WeComNotifier:
    def __init__(self, webhook: str = "", outbox_path: str | Path = "logs/wecom-webhook-outbox.jsonl", login_url: str = _LOGIN_URL):
        self.webhook = webhook
        self.outbox_path = Path(outbox_path)
        self.login_url = _LOGIN_URL

    def send(self, text: str) -> bool:
        return self.send_template_card(_text_card("座位助手通知", text, self.login_url))

    def send_template_card(self, card: dict) -> bool:
        if not self.webhook:
            return False
        pending = self._read_outbox()
        pending.append({"payload": card, "queued_at": datetime.now().isoformat(timespec="seconds")})
        remaining = []
        for item in pending:
            payload = item.get("payload") or _text_card("座位助手通知", str(item.get("text") or ""))
            if not self._post(payload):
                remaining.append(item)
        self._write_outbox(remaining)
        return not remaining

    def _post(self, payload: dict) -> bool:
        body = json.dumps(payload, ensure_ascii=False).encode()
        request = Request(self.webhook, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=10) as response:
                if response.status != 200:
                    return False
                try:
                    payload = json.loads(response.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return False
                return payload.get("errcode") == 0
        except (OSError, TimeoutError):
            return False

    def _read_outbox(self) -> list[dict]:
        try:
            lines = self.outbox_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        pending = []
        for line in lines:
            try:
                item = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(item, dict) and (item.get("payload") or item.get("text")):
                pending.append(item)
        return pending

    def _write_outbox(self, items: list[dict]) -> None:
        if not items:
            try:
                self.outbox_path.unlink()
            except FileNotFoundError:
                pass
            return
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
        self.outbox_path.write_text(content, encoding="utf-8")
