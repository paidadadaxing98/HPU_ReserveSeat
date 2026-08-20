import json
from urllib.request import Request, urlopen


def render_reservation(period: str, result, start: str, end: str) -> str:
    return f"{period}预约成功\n时间：{start} - {end}\n阅览室：{result.room}\n座位：{result.seat}\n请在预约前30分钟至预约后15分钟内现场刷卡签到。"


class WeComNotifier:
    def __init__(self, webhook: str = ""):
        self.webhook = webhook

    def send(self, text: str) -> bool:
        if not self.webhook:
            return False
        body = json.dumps({"msgtype": "text", "text": {"content": text}}, ensure_ascii=False).encode()
        request = Request(self.webhook, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=10) as response:
            return response.status == 200
