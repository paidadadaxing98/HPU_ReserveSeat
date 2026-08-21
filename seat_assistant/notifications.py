import json
import logging
from urllib.request import Request, urlopen


_PERIOD_LABELS = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}


def render_reservation(day: str, period: str, result, start: str, end: str) -> str:
    label = _PERIOD_LABELS.get(period, period)
    if result.success:
        status = "预约成功"
    elif not result.conclusive and str(result.message or "").lstrip().startswith("已提交"):
        status = "预约已提交，待核验"
    elif result.conclusive:
        status = "预约失败"
    else:
        status = "预约结果不明确"
    lines = [f"{day} {label}{status}", f"时间：{start} - {end}"]
    if result.room:
        lines.append(f"阅览室：{result.room}")
    if result.seat:
        lines.append(f"座位：{result.seat}")
    if result.message:
        lines.append(f"说明：{result.message}")
    if result.success:
        lines.append("请在预约前30分钟至预约后15分钟内现场刷卡签到。")
    return "\n".join(lines)


def send_reservation_notification(notifier, day: str, period: str, result, start: str, end: str) -> bool:
    if notifier is None:
        return False
    try:
        return bool(notifier.send(render_reservation(day, period, result, start, end)))
    except Exception as exc:
        logging.getLogger(__name__).warning("预约通知发送失败：%s", exc)
        return False


def render_scheduler_summary(account_id: str, day: str, summary: dict) -> str:
    status = str(summary.get("status") or "completed")
    label = {"reserved": "已完成", "skipped": "已跳过", "uncertain": "结果不明确", "completed": "已运行"}.get(status, status)
    lines = [f"账号 {account_id} 定时任务{label}", f"日期：{day}"]
    if summary.get("message"):
        lines.append(f"说明：{summary['message']}")
    for period, result in summary.items():
        if period in {"status", "message", "account_id"} or not isinstance(result, dict):
            continue
        period_label = _PERIOD_LABELS.get(period, period)
        item_status = result.get("status", "unknown")
        item_message = result.get("message", "")
        lines.append(f"{period_label}：{item_status}{f'，{item_message}' if item_message else ''}")
    return "\n".join(lines)


def render_initialization(account_id: str, state: dict) -> str:
    status = "初始化完成" if state.get("status") == "ready" else "初始化失败"
    lines = [f"账号 {account_id}{status}"]
    if state.get("last_verified_at"):
        lines.append(f"验证时间：{state['last_verified_at']}")
    if state.get("message"):
        lines.append(f"说明：{state['message']}")
    return "\n".join(lines)


def send_initialization_notification(notifier, account_id: str, state: dict) -> bool:
    if notifier is None:
        return False
    try:
        return bool(notifier.send(render_initialization(account_id, state)))
    except Exception as exc:
        logging.getLogger(__name__).warning("初始化通知发送失败：%s", exc)
        return False


def send_scheduler_notification(notifier, account_id: str, day: str, summary: dict) -> bool:
    if notifier is None:
        return False
    try:
        return bool(notifier.send(render_scheduler_summary(account_id, day, summary)))
    except Exception as exc:
        logging.getLogger(__name__).warning("定时任务通知发送失败：%s", exc)
        return False


class WeComNotifier:
    def __init__(self, webhook: str = ""):
        self.webhook = webhook

    def send(self, text: str) -> bool:
        if not self.webhook:
            return False
        body = json.dumps({"msgtype": "text", "text": {"content": text}}, ensure_ascii=False).encode()
        request = Request(self.webhook, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=10) as response:
            if response.status != 200:
                return False
            try:
                payload = json.loads(response.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return False
            return payload.get("errcode") == 0
