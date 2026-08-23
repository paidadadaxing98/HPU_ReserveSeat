from dataclasses import dataclass
from datetime import datetime
import re


PERIODS = {"上午": "morning", "下午": "afternoon", "晚上": "evening"}


@dataclass(frozen=True)
class Command:
    kind: str
    period: str | None = None
    at: str | None = None
    target: str | None = None
    title: str | None = None
    url: str | None = None
    note: str | None = None


def parse_command(text: str) -> Command:
    text = text.strip().replace("：", ":")
    if text.startswith("推文"):
        return _parse_push_tweet(text)
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


def _parse_push_tweet(text: str) -> Command:
    payload = text.removeprefix("推文").strip()
    if not payload:
        return Command("help")
    target, _, rest = payload.partition(" ")
    target = target.strip().lstrip("@")
    parts = [part.strip() for part in rest.split("|")]
    parts = [part for part in parts if part]
    if not target or len(parts) < 2:
        return Command("help")
    return Command(
        "push_tweet",
        target=target,
        title=parts[0],
        url=_unwrap_markdown_link(parts[1]),
        note=parts[2] if len(parts) > 2 else None,
    )


def _unwrap_markdown_link(value: str) -> str:
    match = re.fullmatch(r"\[([^\]]+)\]\((https?://[^)]+)\)", value.strip())
    return match.group(2) if match else value.strip()


def _valid_time(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%H:%M").strftime("%H:%M")
    except ValueError:
        return None
