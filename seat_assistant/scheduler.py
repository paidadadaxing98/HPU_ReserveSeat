from datetime import datetime, timedelta
import time

from .commands import parse_command
from .initialization import initialization_skip_message
from .notifications import send_scheduler_notification


def next_booking_time(now: datetime) -> datetime:
    target = now.replace(hour=19, minute=30, second=0, microsecond=0)
    return target if now <= target else target + timedelta(days=1)


def _apply_pending_cancels(service, day: str, periods, persist_results: bool = True):
    """Honor pending cancel commands before any booking decision.

    Date-scoped cancel commands (取消MM-DD上午) are queued under their target
    day. Every covered period without an active reservation gets a terminal
    "cancelled" record so this and later triggers skip booking it; a command
    covering a single period is completed right away. cancel_day commands
    stay pending so the monitor or bot fallback can still cancel remotely if
    an active reservation turns up.
    """
    try:
        items = service.repo.pending_bot_commands(day)
    except Exception:
        return
    if not items:
        return
    period_names = [
        name for name, period in periods if getattr(period, "enabled", True)
    ]
    for item in items:
        try:
            command = parse_command(item["text"])
        except Exception:
            continue
        if command.kind == "cancel_day":
            covered = list(period_names)
        elif command.kind == "cancel" and command.period in period_names:
            covered = [command.period]
        else:
            continue
        blocked = False
        for name in covered:
            existing = service.repo.get_reservation(day, name)
            if existing is not None and existing["status"] in {"reserved", "pending", "uncertain"}:
                # An active reservation exists: the monitor or bot fallback
                # must cancel it remotely; keep the command pending.
                blocked = True
                continue
            if persist_results:
                service.repo.save_reservation(
                    day,
                    name,
                    "cancelled",
                    "",
                    "",
                    "",
                    "",
                    "收到取消指令，未预约",
                )
        if blocked or command.kind != "cancel":
            continue
        service.repo.complete_bot_command(
            item["request_id"],
            "completed",
            {"ok": True, "message": f"已在预约前跳过 {day} {'、'.join(covered)} 的预约。"},
        )
        send = getattr(getattr(service, "notifier", None), "send", None)
        if callable(send):
            try:
                send(f"已按要求取消：{day} {'、'.join(covered)} 的预约不会提交。")
            except Exception:
                pass


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
    _apply_pending_cancels(service, day, periods, persist_results)
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
        if status in {"pending", "uncertain"} and persist_results:
            reconciliation = service.reconcile_existing_submission(day, name)
            if reconciliation is not None:
                outcome, detail = reconciliation
                if outcome == "recovered":
                    results[name] = _period_summary(
                        "reserved",
                        detail.message or "已从‘我的预约’确认成功",
                        True,
                        detail.room,
                        detail.seat,
                    )
                    continue
                if outcome == "retry":
                    # Verified absent in a fresh query: retire the old submit
                    # and retry instead of leaving an uncertain dead end.
                    service.repo.save_reservation(
                        day,
                        name,
                        "failed",
                        record.get("start", ""),
                        record.get("end", ""),
                        record.get("room", ""),
                        record.get("seat", ""),
                        "上次提交已确认远端无有效预约，准备重试",
                    )
                    results[name] = _period_summary("pending", "已确认上次提交不存在，准备重试")
                    if pending_name is None:
                        pending_name = name
                    continue
                results[name] = _period_summary("pending", detail.message or "无法确认上次提交结果，稍后重查")
                continue
        if status == "uncertain":
            results[name] = _period_summary("uncertain", record.get("message") or "预约未确认成功")
            # An uncertain result only prevents retrying this period. It must
            # not prevent independent later periods from being attempted.
            continue
        if status == "failed":
            if not _period_expired(day, _period, now):
                # Transient glitches and verified-absent submits are retried
                # while the period window is still open.
                results[name] = _period_summary("pending", record.get("message") or "上次预约失败，本轮重试")
                if pending_name is None:
                    pending_name = name
                continue
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
        record = service.repo.get_reservation(day, pending_name)
        result_status = record.get("status") if record and record.get("status") == "pending" else "uncertain"
        results[pending_name] = _period_summary(result_status, result.message or "预约未确认成功")
        record = service.repo.get_reservation(day, pending_name)
        service.repo.save_reservation(
            day,
            pending_name,
            result_status,
            record["start"] if record else "",
            record["end"] if record else "",
            record["room"] if record else result.room,
            record["seat"] if record else result.seat,
            result.message or "预约未确认成功",
        )
        summary_status = result_status
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
    _apply_pending_cancels(service, day, periods, persist_results)
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
    if record is not None and record["status"] in {"pending", "uncertain"} and persist_results:
        reconciliation = service.reconcile_existing_submission(day, target_period)
        if reconciliation is not None:
            outcome, detail = reconciliation
            if outcome == "recovered":
                record = service.repo.get_reservation(day, target_period)
                results[target_period] = _period_summary(
                    "reserved",
                    detail.message or "已从‘我的预约’确认成功",
                    True,
                    detail.room,
                    detail.seat,
                )
            elif outcome == "retry":
                # A fresh query proved the earlier submit never landed.
                # Retire it and book again instead of dead-ending the period.
                service.repo.save_reservation(
                    day,
                    target_period,
                    "failed",
                    record.get("start", ""),
                    record.get("end", ""),
                    record.get("room", ""),
                    record.get("seat", ""),
                    "上次提交已确认远端无有效预约，准备重试",
                )
                record = None
            else:
                summary = _finish_summary(
                    results,
                    "pending",
                    service,
                    detail.message or "无法确认上次提交结果，暂不重复提交",
                )
                service.repo.save_scheduler_run(day, "pending", summary)
                return summary
    if record is not None and record["status"] == "failed" and not _period_expired(
        day, period_map[target_period], now
    ):
        # Retry a failed submission while the period window is still open.
        record = None
    if record is not None:
        status = record["status"]
        if status == "uncertain":
            summary_status = "uncertain"
            summary_message = record.get("message") or "预约未确认成功，已停止后续提交"
        elif status == "reserved":
            summary_status = "waiting" if not _ended(day, record["end"], now) else "progressed"
            summary_message = "该时段已预约，避免重复提交"
        elif status in {"failed", "missed", "cancelled"}:
            summary_status = "failed" if status == "failed" else status
            summary_message = record.get("message") or f"该时段已有记录：{status}"
        else:
            summary_status = "waiting"
            summary_message = record.get("message") or "该时段已有未确认的提交记录"
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
        record = service.repo.get_reservation(day, target_period)
        result_status = record.get("status") if record and record.get("status") == "pending" else "uncertain"
        results[target_period] = _period_summary(result_status, result.message or "预约未确认成功")
        summary_status = result_status
        summary_message = result.message or "预约未确认成功，已停止后续提交"
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
        "uncertain": "预约未确认成功，已停止后续提交",
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
