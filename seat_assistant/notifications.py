import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


_PERIOD_LABELS = {
    "morning": "上午",
    "afternoon": "下午",
    "evening": "晚上",
    "period04": "第4段",
    "period05": "第5段",
}


def render_reservation(day: str, period: str, result, start: str, end: str, account_label: str | None = None) -> str:
    label = _PERIOD_LABELS.get(period, period)
    if result.success:
        status = "预约成功"
    elif not result.conclusive and str(result.message or "").lstrip().startswith("已提交"):
        status = "预约已提交，待核验"
    elif result.conclusive:
        status = "预约失败"
    else:
        status = "预约结果不明确"
    display = account_label or ""
    lines = []
    if display:
        lines.append(f"账号：{display}")
    lines.extend([f"{day} {label}{status}", f"时间：{start} - {end}"])
    if result.room:
        lines.append(f"阅览室：{result.room}")
    if result.message:
        lines.append(f"说明：{result.message}")
    return "\n".join(lines)


def send_reservation_notification(notifier, day: str, period: str, result, start: str, end: str, account_label: str | None = None) -> bool:
    if notifier is None:
        return False
    try:
        return bool(notifier.send(render_reservation(day, period, result, start, end, account_label)))
    except Exception as exc:
        logging.getLogger(__name__).warning("预约通知发送失败：%s", exc)
        return False


def render_scheduler_summary(account_id: str, day: str, summary: dict, account_label: str | None = None) -> str:
    status = str(summary.get("status") or "completed")
    label = {"reserved": "已完成", "skipped": "已跳过", "uncertain": "结果不明确", "completed": "已运行"}.get(status, status)
    display = account_label or account_id
    lines = [f"账号 {display} 定时任务{label}", f"日期：{day}"]
    if summary.get("message"):
        lines.append(f"说明：{summary['message']}")
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
        lines.append(f"{period_label}：{item_status}{f'，{item_message}' if item_message else ''}")
    for period, result in summary.items():
        if period in {"status", "message", "account_id"} or period in seen or not isinstance(result, dict):
            continue
        period_label = _PERIOD_LABELS.get(period, period)
        item_status = result.get("status", "unknown")
        item_message = result.get("message", "")
        lines.append(f"{period_label}：{item_status}{f'，{item_message}' if item_message else ''}")
    return "\n".join(lines)


def render_initialization(account_id: str, state: dict, account_label: str | None = None) -> str:
    status = "初始化完成" if state.get("status") == "ready" else "初始化失败"
    display = account_label or account_id
    lines = [f"账号 {display}{status}"]
    if state.get("last_verified_at"):
        lines.append(f"验证时间：{state['last_verified_at']}")
    if state.get("message"):
        lines.append(f"说明：{state['message']}")
    return "\n".join(lines)


def send_initialization_notification(notifier, account_id: str, state: dict, account_label: str | None = None) -> bool:
    if notifier is None:
        return False
    try:
        return bool(notifier.send(render_initialization(account_id, state, account_label)))
    except Exception as exc:
        logging.getLogger(__name__).warning("初始化通知发送失败：%s", exc)
        return False


def send_scheduler_notification(notifier, account_id: str, day: str, summary: dict, account_label: str | None = None) -> bool:
    if notifier is None:
        return False
    try:
        return bool(notifier.send(render_scheduler_summary(account_id, day, summary, account_label)))
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


class WeComNotifier:
    def __init__(self, webhook: str = "", outbox_path: str | Path = "logs/wecom-webhook-outbox.jsonl"):
        self.webhook = webhook
        self.outbox_path = Path(outbox_path)

    def send(self, text: str) -> bool:
        if not self.webhook:
            return False
        pending = self._read_outbox()
        pending.append({"text": text, "queued_at": datetime.now().isoformat(timespec="seconds")})
        remaining = []
        for item in pending:
            if not self._post(str(item.get("text") or "")):
                remaining.append(item)
        self._write_outbox(remaining)
        return not remaining

    def _post(self, text: str) -> bool:
        body = json.dumps({"msgtype": "text", "text": {"content": text}}, ensure_ascii=False).encode()
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
            if isinstance(item, dict) and item.get("text"):
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
