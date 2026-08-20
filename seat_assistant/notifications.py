import json
import logging
from urllib.request import Request, urlopen


_PERIOD_LABELS = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}


def render_reservation(day: str, period: str, result, start: str, end: str) -> str:
    label = _PERIOD_LABELS.get(period, period)
    if result.success:
        status = "预约成功"
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
