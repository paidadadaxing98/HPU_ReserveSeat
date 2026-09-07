"""Small, redacted summaries for unattended runtime diagnostics."""

import logging
import re
from datetime import date
from pathlib import Path


_SENSITIVE_FIELD = re.compile(
    r"(?i)(?:token|secret|password|passwd|authorization|cookie|api[_-]?key)"
    r"\s*[:=]\s*[^\s,;，；]+"
)
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_PERIOD_LABELS = {
    "morning": "上午",
    "afternoon": "下午",
    "evening": "晚上",
    "period04": "第4段",
    "period05": "第5段",
}
_STATUS_LABELS = {
    "idle": "空闲",
    "waiting": "等待",
    "monitoring": "监测中",
    "wait": "等待",
    "entered": "已入馆",
    "early_reschedule": "早到重约",
    "reschedule": "迟到重约",
    "reconciled": "已核对",
    "cancel_unentered": "取消未入馆",
    "cancelled": "已取消",
    "away_cancel": "暂离取消",
    "away_wait": "暂离等待",
    "away_wait_completion": "等待完成",
    "error_hold": "异常保持",
    "safe_hold": "安全保持",
    "safe_stop": "安全停止",
    "disabled": "未启用",
    "completed": "已完成",
    "missed": "已失约",
    "uncertain": "结果不明确",
    "error": "异常",
    "reserved": "已预约",
    "dry-run": "演练",
    "failed": "预约失败",
    "pending": "未确认成功",
    "skipped": "已跳过",
}


def compact_message(message, limit=240):
    limit = max(16, int(limit))
    text = " ".join(str(message or "").split())
    text = _URL.sub("<url>", text)
    text = _SENSITIVE_FIELD.sub("敏感字段已隐藏", text)
    # A bare cookie/header dump may not use key=value consistently.
    text = re.sub(r"(?i)\b(?:cookie|authorization|bearer)\b", "敏感字段", text)
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def summarize_candidate_errors(errors, limit=240):
    groups = []
    indexes = {}
    for raw in errors or ():
        text = compact_message(raw, limit=limit)
        if not text:
            continue
        detail = text.split(":", 1)[1].strip() if ":" in text else text
        key = re.sub(r"\s+", " ", detail)
        if key in indexes:
            groups[indexes[key]][1] += 1
        else:
            indexes[key] = len(groups)
            groups.append([detail, 1])
    parts = []
    for detail, count in groups[:4]:
        parts.append(f"{detail}（另有{count - 1}项）" if count > 1 else detail)
    if len(groups) > 4:
        parts.append(f"另有{len(groups) - 4}类错误")
    return compact_message("；".join(parts), limit=limit)


def format_account_results(result, prefix="监控结束"):
    accounts = []
    for account_id, value in (result or {}).items():
        if not isinstance(value, dict):
            accounts.append(f"{account_id}[{_status_label(value)}]")
            continue
        periods = []
        for period, status in value.items():
            if period in {"status", "message", "account_id", "commands"}:
                continue
            if isinstance(status, dict):
                status = status.get("status") or status.get("message") or "unknown"
            periods.append(f"{_PERIOD_LABELS.get(period, period)}={_status_label(status)}")
        if periods:
            accounts.append(f"{account_id}[{('，'.join(periods))}]")
        else:
            accounts.append(f"{account_id}[{_status_label(value.get('status', 'unknown'))}]")
    return f"{prefix}：" + "；".join(accounts)


def format_monitor_result(result):
    return format_account_results(result, prefix="监控结束")


def _status_label(status):
    value = str(status or "unknown")
    return _STATUS_LABELS.get(value, compact_message(value, limit=32))


def configure_runtime_logging(level=logging.WARNING):
    """Keep third-party SDK chatter out while retaining project warnings."""
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s", force=True)
    for name in ("AiBotSDK", "wecom", "wecom_aibot_sdk", "wecom_aibot_sdk.client"):
        logging.getLogger(name).setLevel(logging.WARNING)


def attach_file_log(log_dir="logs", prefix="runtime", level=logging.INFO):
    """Append project logging to a dated file so scheduled runs leave traces.

    Windows scheduled tasks discard stdout, so every long-running entrypoint
    attaches one of these handlers; without it there is no way to audit why a
    command or reservation behaved the way it did.
    """
    try:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(
            directory / f"{prefix}-{date.today().isoformat()}.log",
            encoding="utf-8",
        )
    except OSError:
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if not root.level or root.level > level:
        root.setLevel(level)
    for name in ("AiBotSDK", "wecom", "wecom_aibot_sdk", "wecom_aibot_sdk.client"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return str(handler.baseFilename)
