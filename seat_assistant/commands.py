from dataclasses import dataclass
from datetime import datetime
import re


PERIODS = {"上午": "morning", "下午": "afternoon", "晚上": "evening"}
REMOTE_COMMAND_KINDS = frozenset({"cancel_day", "cancel", "delay", "status"})
REMOTE_COMMAND_HELP = (
    "命令列表：\n"
    "帮助\n"
    "状态\n"
    "今天不去了\n"
    "取消【上午/下午/晚上】\n"
    "【上午/下午/晚上】推迟到 HH:MM[-HH:MM]（按时段直接预约，最长4小时）\n"
    "以后【上午/下午/晚上】默认到馆时间为 HH:MM[-HH:MM]（后半为默认结束时间）\n"
    "启用账号 / 关闭账号 [账号或别名]"
)
BOT_COMMAND_HELP = REMOTE_COMMAND_HELP + "\n推文 【账号或别名】 标题 | 链接 [| 备注]"

@dataclass(frozen=True)
class Command:
    kind: str
    period: str | None = None
    at: str | None = None
    end: str | None = None
    target: str | None = None
    title: str | None = None
    url: str | None = None
    note: str | None = None


def parse_command(text: str) -> Command:
    text = text.strip().replace("：", ":")
    if text in {"帮助", "命令", "菜单"}:
        return Command("help")
    if text.startswith("推文"):
        return _parse_push_tweet(text)
    if text in {"今天不去了", "取消全天", "取消今天"}:
        return Command("cancel_day")
    match = re.match(
        r"(上午|下午|晚上)\s*(?:推迟|延迟)\s*到\s*(\d{1,2}:\d{2})(?:\s*(?:-|到|—|~)\s*(\d{1,2}:\d{2}))?$",
        text,
    )
    if match:
        value = _valid_time(match.group(2))
        end = _valid_time(match.group(3)) if match.group(3) else None
        return Command("delay", PERIODS[match.group(1)], value, end) if value else Command("help")
    match = re.match(
        r"(?:推迟|延迟)\s*到\s*(\d{1,2}:\d{2})(?:\s*(?:-|到|—|~)\s*(\d{1,2}:\d{2}))?$",
        text,
    )
    if match:
        value = _valid_time(match.group(1))
        end = _valid_time(match.group(2)) if match.group(2) else None
        return Command("ask_period", None, value, end) if value else Command("help")
    match = re.match(r"(上午|下午|晚上)\s*(?:推迟|延迟)$", text)
    if match:
        return Command("ask_delay", PERIODS[match.group(1)])
    match = re.match(
        r"(?:以后)?(上午|下午|晚上)默认(?:到馆)?(?:时间)?\s*(?:为|到)?\s*(\d{1,2}:\d{2})(?:\s*(?:-|到|—|~)\s*(\d{1,2}:\d{2}))?$",
        text,
    )
    if match:
        value = _valid_time(match.group(2))
        end = _valid_time(match.group(3)) if match.group(3) else None
        if not value:
            return Command("help")
        return Command("set_default", PERIODS[match.group(1)], value, end)
    match = re.match(r"记录(上午|下午|晚上)到馆\s*(\d{1,2}:\d{2})$", text)
    if match:
        value = _valid_time(match.group(2))
        return Command("record_arrival", PERIODS[match.group(1)], value) if value else Command("help")
    match = re.match(r"取消(上午|下午|晚上)$", text)
    if match:
        return Command("cancel", PERIODS[match.group(1)])
    match = re.match(r"(启用|关闭)账号\s*(\S+)?$", text)
    if match:
        kind = "enable_account" if match.group(1) == "启用" else "disable_account"
        return Command(kind, target=match.group(2))
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
