from datetime import datetime, timedelta
import time

from .initialization import initialization_skip_message
from .notifications import send_scheduler_notification


def next_booking_time(now: datetime) -> datetime:
    target = now.replace(hour=19, minute=30, second=0, microsecond=0)
    return target if now <= target else target + timedelta(days=1)


def run_once(service, day: str, now: datetime | None = None):
    """Advance one account by at most one reservation task for the day."""
    previous = service.repo.scheduler_run(day)
    if previous and previous["status"] == "completed":
        return previous["summary"]

    if getattr(service.settings, "require_initialization", False):
        state = service.repo.initialization_state()
        if state["status"] != "ready":
            summary = {
                "status": "skipped",
                "account_id": getattr(service, "account_id", "default"),
                "message": initialization_skip_message(state),
            }
            service.repo.save_scheduler_run(day, "skipped", summary)
            return summary

    now = now or datetime.now()
    periods = list(service.settings.periods.items())
    enabled = [(name, period) for name, period in periods if getattr(period, "enabled", True)]
    results = {
        name: _period_summary("skipped", "该学习时段未启用")
        for name, period in periods
        if not getattr(period, "enabled", True)
    }
    results.update({
        name: _period_summary("pending", "等待前一预约结束后执行")
        for name, period in enabled
    })

    blocking_status = None
    pending_name = None
    for name, _period in enabled:
        record = service.repo.get_reservation(day, name)
        if record is None:
            period = dict(enabled)[name]
            if _period_expired(day, period, now):
                service.repo.save_reservation(
                    day,
                    name,
                    "missed",
                    period.arrival_window[0],
                    period.arrival_window[1],
                    message="预约窗口已结束，未提交预约",
                )
                results[name] = _period_summary("missed", "预约窗口已结束，未提交预约")
                continue
            if pending_name is None:
                pending_name = name
            continue
        status = record["status"]
        if status == "reserved":
            results[name] = _period_summary(
                "reserved", "已预约" if not _ended(day, record["end"], now) else "预约已结束"
            )
            continue
        if status == "uncertain":
            results[name] = _period_summary("uncertain", record.get("message") or "预约结果不明确")
            blocking_status = "uncertain"
            break
        if status == "failed":
            results[name] = _period_summary("failed", record.get("message") or "预约失败")
            continue
        results[name] = _period_summary("skipped", record.get("message") or f"已有终态记录：{status}")

    if blocking_status:
        summary = _finish_summary(results, blocking_status, service, _status_message(blocking_status))
        service.repo.save_scheduler_run(day, blocking_status, summary)
        return summary

    if pending_name is None:
        final_status = "completed" if _all_tasks_reserved(service, day, enabled) else "failed"
        final_message = "全部启用学习时段已完成" if final_status == "completed" else "全部启用时段未全部预约成功"
        summary = _finish_summary(results, final_status, service, final_message)
        service.repo.save_scheduler_run(day, final_status, summary)
        return summary

    result = service.reserve_period(day, pending_name, quota_day=day, now=now)
    if result.success:
        results[pending_name] = _period_summary(
            "reserved", result.message or "预约成功", True, result.room, result.seat
        )
        if _all_tasks_reserved(service, day, enabled):
            summary_status = "completed"
        elif _all_tasks_terminal(service, day, enabled):
            summary_status = "failed"
        else:
            summary_status = "progressed"
    elif not result.conclusive:
        results[pending_name] = _period_summary("uncertain", result.message or "预约结果不明确")
        record = service.repo.get_reservation(day, pending_name)
        service.repo.save_reservation(
            day,
            pending_name,
            "uncertain",
            record["start"] if record else "",
            record["end"] if record else "",
            record["room"] if record else result.room,
            record["seat"] if record else result.seat,
            result.message or "预约结果不明确",
        )
        summary_status = "uncertain"
    elif "尚未结束" in str(result.message or ""):
        results[pending_name] = _period_summary("waiting", result.message)
        summary_status = "waiting"
    else:
        results[pending_name] = _period_summary("failed", result.message or "预约失败")
        record = service.repo.get_reservation(day, pending_name)
        service.repo.save_reservation(
            day,
            pending_name,
            "failed",
            record["start"] if record else "",
            record["end"] if record else "",
            record["room"] if record else result.room,
            record["seat"] if record else result.seat,
            result.message or "预约失败",
        )
        summary_status = "failed"

    summary_message = "全部启用学习时段已完成" if summary_status == "completed" else _status_message(summary_status)
    if summary_status == "failed" and _all_tasks_terminal(service, day, enabled):
        summary_message = "全部启用时段未全部预约成功"
    summary = _finish_summary(results, summary_status, service, summary_message)
    service.repo.save_scheduler_run(day, summary_status, summary)
    return summary


def run_accounts_once(services, day: str, interval_seconds: float = 15.0, now: datetime | None = None):
    """Run account services serially, preserving each account's result."""
    results = {}
    for index, service in enumerate(services):
        if index:
            time.sleep(max(0.0, interval_seconds))
        account_id = getattr(service, "account_id", None) or getattr(service.settings, "account_id", "default")
        try:
            results[account_id] = service.run_once(day, now=now) if now is not None else service.run_once(day)
        except Exception as exc:
            results[account_id] = {
                "status": "uncertain",
                "account_id": account_id,
                "message": f"账号运行异常：{exc}",
            }
        send_scheduler_notification(getattr(service, "notifier", None), account_id, day, results[account_id])
    return results


def _period_summary(status: str, message: str = "", success: bool = False, room: str = "", seat: str = "") -> dict:
    return {"status": status, "success": success, "message": message, "room": room, "seat": seat}


def _finish_summary(results: dict, status: str, service, message: str = "") -> dict:
    return {
        "status": status,
        "account_id": getattr(service, "account_id", "default"),
        "message": message,
        **results,
    }


def _status_message(status: str) -> str:
    return {
        "progressed": "本次已完成一个预约任务，后续时段等待下一次运行",
        "waiting": "前一个预约尚未结束，等待后续运行",
        "uncertain": "预约结果不明确，已停止后续提交",
        "failed": "当前预约任务明确失败，已停止后续提交",
    }.get(status, "")


def _all_tasks_terminal(service, day: str, enabled) -> bool:
    terminal = {"reserved", "failed", "missed", "cancelled"}
    return all(
        (record := service.repo.get_reservation(day, name)) is not None
        and record["status"] in terminal
        for name, _period in enabled
    )


def _all_tasks_reserved(service, day: str, enabled) -> bool:
    return all(
        (record := service.repo.get_reservation(day, name)) is not None
        and record["status"] == "reserved"
        for name, _period in enabled
    )


def _ended(day: str, end: str, now: datetime) -> bool:
    try:
        end_at = datetime.fromisoformat(f"{day} {end}")
    except (TypeError, ValueError):
        return False
    return now >= end_at


def _period_expired(day: str, period, now: datetime) -> bool:
    try:
        end_at = datetime.combine(
            datetime.fromisoformat(day).date(),
            datetime.strptime(period.arrival_window[1], "%H:%M").time(),
        )
    except (TypeError, ValueError):
        return False
    return now >= end_at
