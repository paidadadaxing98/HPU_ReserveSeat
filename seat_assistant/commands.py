from dataclasses import dataclass
from datetime import datetime
import re


PERIODS = {"上午": "morning", "下午": "afternoon", "晚上": "evening"}


@dataclass(frozen=True)
class Command:
    kind: str
    period: str | None = None
    at: str | None = None


def parse_command(text: str) -> Command:
    text = text.strip().replace("：", ":")
    if text in {"今天不去了", "取消全天", "取消今天"}:
        return Command("cancel_day")
    match = re.match(r"(上午|下午|晚上)\s*(?:推迟|延迟)\s*到\s*(\d{1,2}:\d{2})$", text)
    if match:
        value = _valid_time(match.group(2))
        return Command("delay", PERIODS[match.group(1)], value) if value else Command("help")
    match = re.match(r"(?:推迟|延迟)\s*到\s*(\d{1,2}:\d{2})$", text)
    if match:
        value = _valid_time(match.group(1))
        return Command("ask_period", None, value) if value else Command("help")
    match = re.match(r"(上午|下午|晚上)\s*(?:推迟|延迟)$", text)
    if match:
        return Command("ask_delay", PERIODS[match.group(1)])
    match = re.match(r"(?:以后)?(上午|下午|晚上)默认(?:到馆)?(?:时间)?\s*(?:为|到)?\s*(\d{1,2}:\d{2})$", text)
    if match:
        value = _valid_time(match.group(2))
        return Command("set_default", PERIODS[match.group(1)], value) if value else Command("help")
    match = re.match(r"记录(上午|下午|晚上)到馆\s*(\d{1,2}:\d{2})$", text)
    if match:
        value = _valid_time(match.group(2))
        return Command("record_arrival", PERIODS[match.group(1)], value) if value else Command("help")
    match = re.match(r"取消(上午|下午|晚上)$", text)
    if match:
        return Command("cancel", PERIODS[match.group(1)])
    if text in {"状态", "查看状态"}:
        return Command("status")
    return Command("help")


def _valid_time(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%H:%M").strftime("%H:%M")
    except ValueError:
        return None
