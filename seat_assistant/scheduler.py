from datetime import datetime, timedelta
import time

from .initialization import initialization_skip_message
from .notifications import send_scheduler_notification


def next_booking_time(now: datetime) -> datetime:
    target = now.replace(hour=19, minute=30, second=0, microsecond=0)
    return target if now <= target else target + timedelta(days=1)


def run_once(
    service,
    day: str,
    now: datetime | None = None,
    target_period: str | None = None,
    persist_results: bool = True,
):
    """Advance one account by at most one reservation task for the day."""
    previous = service.repo.scheduler_run(day)
    if previous and previous["status"] == "completed":
        return previous["summary"]

    if target_period is not None:
        return _run_target_period(
            service,
            day,
            target_period,
            now or datetime.now(),
            persist_results=persist_results,
        )

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

    result = service.reserve_period(
        day,
        pending_name,
        quota_day=day,
        now=now,
        persist_results=persist_results,
    )
    if result.success:
        results[pending_name] = _period_summary(
            "reserved" if persist_results else "dry-run",
            result.message or "预约成功",
            True,
            result.room,
            result.seat,
        )
        if not persist_results:
            summary_status = "dry-run"
        elif _all_tasks_reserved(service, day, enabled):
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


def run_accounts_once(
    services,
    day: str,
    interval_seconds: float = 15.0,
    now: datetime | None = None,
    target_period: str | None = None,
    persist_results: bool = True,
):
    """Run account services serially, preserving each account's result."""
    results = {}
    for index, service in enumerate(services):
        if index:
            time.sleep(max(0.0, interval_seconds))
        account_id = getattr(service, "account_id", None) or getattr(service.settings, "account_id", "default")
        settings = getattr(service, "settings", None)
        account_label = getattr(settings, "wecom_aliases", ()) if settings is not None else ()
        account_label = account_label[0] if account_label else account_id
        try:
            if target_period is not None:
                results[account_id] = service.run_once(
                    day,
                    now=now,
                    target_period=target_period,
                    persist_results=persist_results,
                )
            elif now is not None:
                if persist_results:
                    results[account_id] = service.run_once(day, now=now)
                else:
                    results[account_id] = service.run_once(
                        day, now=now, persist_results=False
                    )
            else:
                if persist_results:
                    results[account_id] = service.run_once(day)
                else:
                    results[account_id] = service.run_once(day, persist_results=False)
        except Exception as exc:
            results[account_id] = {
                "status": "uncertain",
                "account_id": account_id,
                "message": f"账号运行异常：{exc}",
            }
        if getattr(settings, "notify_scheduler_summary", False):
            send_scheduler_notification(getattr(service, "notifier", None), account_id, day, results[account_id], account_label)
    return results


def _period_summary(status: str, message: str = "", success: bool = False, room: str = "", seat: str = "") -> dict:
    return {"status": status, "success": success, "message": message, "room": room, "seat": seat}


def _run_target_period(
    service,
    day: str,
    target_period: str,
    now: datetime,
    persist_results: bool = True,
) -> dict:
    """Run only the period represented by one Windows trigger."""
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

    periods = list(service.settings.periods.items())
    period_map = dict(periods)
    results = {
        name: _period_summary("skipped", "该学习时段未启用")
        for name, period in periods
        if not getattr(period, "enabled", True)
    }
    for name, period in periods:
        if getattr(period, "enabled", True):
            record = service.repo.get_reservation(day, name)
            if record is None:
                results[name] = _period_summary("pending", "等待该时段触发")
            else:
                results[name] = _record_period_summary(record, day, now)

    if target_period not in period_map:
        summary = _finish_summary(
            results, "failed", service, f"未知学习时段：{target_period}"
        )
        service.repo.save_scheduler_run(day, "failed", summary)
        return summary
    if not getattr(period_map[target_period], "enabled", True):
        summary = _finish_summary(
            results, "skipped", service, f"学习时段 {target_period} 未启用"
        )
        service.repo.save_scheduler_run(day, "skipped", summary)
        return summary

    record = service.repo.get_reservation(day, target_period)
    if record is not None:
        status = record["status"]
        if status == "uncertain":
            summary_status = "uncertain"
            summary_message = record.get("message") or "预约结果不明确，已停止后续提交"
        elif status == "reserved":
            summary_status = "waiting" if not _ended(day, record["end"], now) else "progressed"
            summary_message = "该时段已预约，避免重复提交"
        elif status in {"failed", "missed", "cancelled"}:
            summary_status = "failed" if status == "failed" else status
            summary_message = record.get("message") or f"该时段已有记录：{status}"
        else:
            summary_status = "waiting"
            summary_message = record.get("message") or "该时段已有待核验记录"
        summary = _finish_summary(results, summary_status, service, summary_message)
        service.repo.save_scheduler_run(day, summary_status, summary)
        return summary

    period = period_map[target_period]
    if _period_expired(day, period, now):
        if persist_results:
            service.repo.save_reservation(
                day,
                target_period,
                "missed",
                period.arrival_window[0],
                period.arrival_window[1],
                message="预约窗口已结束，未提交预约",
            )
        results[target_period] = _period_summary("missed", "预约窗口已结束，未提交预约")
        summary = _finish_summary(results, "missed", service, "预约窗口已结束，未提交预约")
        service.repo.save_scheduler_run(day, "missed", summary)
        return summary

    result = service.reserve_period(
        day,
        target_period,
        quota_day=day,
        now=now,
        persist_results=persist_results,
    )
    if result.success:
        results[target_period] = _period_summary(
            "reserved" if persist_results else "dry-run",
            result.message or "预约成功",
            True,
            result.room,
            result.seat,
        )
        if not persist_results:
            summary_status = "dry-run"
            summary_message = "演练完成：已跑预约流程，未写入预约记录和成功次数"
        else:
            summary_status = "completed" if _all_tasks_reserved(service, day, periods) else "progressed"
            summary_message = "全部启用学习时段已完成" if summary_status == "completed" else "本次已完成一个预约任务，后续时段等待对应计划任务"
    elif not result.conclusive:
        results[target_period] = _period_summary("uncertain", result.message or "预约结果不明确")
        summary_status = "uncertain"
        summary_message = result.message or "预约结果不明确，已停止后续提交"
    elif "尚未结束" in str(result.message or ""):
        results[target_period] = _period_summary("waiting", result.message)
        summary_status = "waiting"
        summary_message = result.message
    else:
        results[target_period] = _period_summary("failed", result.message or "预约失败")
        summary_status = "failed"
        summary_message = result.message or "预约失败"

    summary = _finish_summary(results, summary_status, service, summary_message)
    service.repo.save_scheduler_run(day, summary_status, summary)
    return summary


def _record_period_summary(record: dict, day: str, now: datetime) -> dict:
    status = record.get("status", "unknown")
    if status == "reserved":
        return _period_summary(
            "reserved",
            "已预约" if not _ended(day, record.get("end", ""), now) else "预约已结束",
            True,
            record.get("room", ""),
            record.get("seat", ""),
        )
    return _period_summary(
        status,
        record.get("message") or f"已有记录：{status}",
        False,
        record.get("room", ""),
        record.get("seat", ""),
    )


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
