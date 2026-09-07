"""Run the shared low-frequency dynamic compensation monitor."""

import argparse
import asyncio
import json
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from seat_assistant.access_records import AuthenticationError, BrowserAccessRecordProvider
from seat_assistant.config import is_account_enabled
from seat_assistant.dynamic_monitor import DynamicMonitor
from seat_assistant.main import build_services
from seat_assistant.runtime_logging import attach_file_log, compact_message, format_account_results


PERIODS = ("morning", "afternoon", "evening", "period04", "period05")

_EVENT_LOG_LOCK = threading.Lock()
_EVENT_LOG_ENABLED = False


def enable_event_log_file():
    """Route the JSON event stream to a dated file (production entrypoints only).

    Library callers and tests must not write into the shared runtime log;
    only ``main`` enables this so scheduled runs leave an audit trail.
    """
    global _EVENT_LOG_ENABLED
    _EVENT_LOG_ENABLED = True


def keep_system_awake():
    """Ask Windows to stay awake for as long as this monitor process runs.

    Scheduled tasks use WakeToRun, which wakes the machine for the trigger but
    does not keep it awake; a mid-window sleep silently stops every check,
    including the final cancellation.
    """
    try:
        import ctypes  # noqa: PLC0415

        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception:
        pass


def allow_system_sleep():
    try:
        import ctypes  # noqa: PLC0415

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    except Exception:
        pass


def reconnect_delay(attempt: int, base: int, maximum: int) -> int:
    return min(maximum, base * (2 ** max(0, attempt)))


def reconnect_attempt_exhausted(attempt: int, maximum_attempts: int) -> bool:
    return attempt >= maximum_attempts


def _event_log_path() -> Path | None:
    """Keep the JSON event stream on disk; scheduled tasks discard stdout."""
    if not _EVENT_LOG_ENABLED:
        return None
    try:
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"dynamic-monitor-{date.today().isoformat()}.log"
    except Exception:
        return None


def _default_event_writer(line: str) -> None:
    print(line, flush=True)
    path = _event_log_path()
    if path is None:
        return
    try:
        with _EVENT_LOG_LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        # Diagnostics must never interrupt reservation safety logic.
        pass


def _emit_event(service, event_writer, event: str, **fields) -> None:
    settings = getattr(service, "settings", None)
    account_id = getattr(service, "account_id", None) or getattr(settings, "account_id", "default")
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "event": event,
        "account_id": str(account_id),
    }
    for key in ("day", "attempt", "delay_seconds", "error_type", "reason", "status", "stage", "summary"):
        if key in fields and fields[key] is not None:
            payload[key] = compact_message(fields[key], limit=260) if key in {"reason", "summary"} else fields[key]
    if fields.get("detail") is not None:
        payload["detail"] = compact_message(fields["detail"], limit=260)
    try:
        event_writer(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except Exception:
        # Diagnostics must never interrupt reservation safety logic.
        return


async def _close_provider(provider, service=None, event_writer=None) -> None:
    if provider is None:
        return
    try:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()
    except Exception as exc:
        if service is not None and event_writer is not None:
            _emit_event(
                service,
                event_writer,
                "session_close_failure",
                error_type=type(exc).__name__,
                detail=exc,
            )
        else:
            _default_event_writer(
                f"[{datetime.now().isoformat(timespec='seconds')}] 动态监控关闭旧会话失败：{type(exc).__name__}"
            )


async def _record_authentication_hold(
    monitor,
    day: str,
    error: Exception,
    target_period: str | None = None,
) -> dict:
    message = f"动态监控认证失效，暂停预约操作：{type(error).__name__}"
    try:
        kwargs = {"target_period": target_period} if target_period is not None else {}
        return await asyncio.to_thread(
            monitor.tick,
            day,
            datetime.now(),
            [],
            message,
            None,
            message,
            **kwargs,
        )
    except Exception as exc:
        _emit_event(
            monitor.service,
            _default_event_writer,
            "safe_hold_write_failure",
            day=day,
            error_type=type(exc).__name__,
            detail=exc,
        )
        return {"status": "safe_hold", "message": message}


async def _handle_authentication_failure(
    service,
    monitor,
    day: str,
    error: AuthenticationError,
    provider,
    reconnect_attempt: int,
    event_writer,
    sleep,
    target_period: str | None = None,
    deadline: datetime | None = None,
) -> tuple[None, int, dict]:
    """Rebuild the session and keep retrying for as long as the window runs.

    Exhausting reconnect attempts must not stop the monitor: a hold is only
    safe while nothing else can advance, and the scheduled task's run limit
    (not the reconnect counter) decides when the process goes away.
    """
    invalidate = getattr(provider, "invalidate_authentication", None)
    if provider is not None and callable(invalidate):
        try:
            await invalidate()
        except Exception as exc:
            _emit_event(
                service,
                event_writer,
                "session_auth_reset_failure",
                day=day,
                error_type=type(exc).__name__,
                detail=exc,
            )
    await _close_provider(provider, service, event_writer)
    failed_attempt = reconnect_attempt + 1
    delay = reconnect_delay(
        reconnect_attempt,
        getattr(service.settings, "dynamic_reconnect_base_seconds", 10),
        getattr(service.settings, "dynamic_reconnect_max_seconds", 300),
    )
    _emit_event(
        service,
        event_writer,
            "authentication_failure",
            day=day,
            attempt=failed_attempt,
            error_type=type(error).__name__,
            detail=error,
    )
    last_result = await _record_authentication_hold(monitor, day, error, target_period)
    _emit_event(service, event_writer, "safe_hold", day=day, attempt=failed_attempt, status="safe_hold")
    _emit_event(
        service,
        event_writer,
        "session_rebuild_scheduled",
        day=day,
        attempt=failed_attempt,
        delay_seconds=delay,
    )
    if deadline is not None:
        remaining = (deadline - datetime.now()).total_seconds()
        if remaining <= 0:
            return None, failed_attempt, last_result
        delay = min(delay, remaining)
    await sleep(delay)
    return None, failed_attempt, last_result


async def _handle_browser_session_failure(
    service,
    monitor,
    day: str,
    error: Exception,
    provider,
    reconnect_attempt: int,
    event_writer,
    sleep,
    target_period: str | None = None,
    deadline: datetime | None = None,
) -> tuple[None, int, dict]:
    """Rebuild a broken browser session without changing reservation state.

    Unlike the earlier versions this never gives up on the window: every
    rebuild retries the same stage, because an unmarked checkpoint keeps the
    target and the round unchanged.
    """
    await _close_provider(provider, service, event_writer)
    failed_attempt = reconnect_attempt + 1
    delay = reconnect_delay(
        reconnect_attempt,
        getattr(service.settings, "dynamic_reconnect_base_seconds", 10),
        getattr(service.settings, "dynamic_reconnect_max_seconds", 300),
    )
    _emit_event(
        service,
        event_writer,
        "session_failure",
        day=day,
        attempt=failed_attempt,
        error_type=type(error).__name__,
        detail=error,
    )
    last_result = await _record_authentication_hold(monitor, day, error, target_period)
    _emit_event(service, event_writer, "safe_hold", day=day, attempt=failed_attempt, status="safe_hold")
    _emit_event(
        service,
        event_writer,
        "session_rebuild_scheduled",
        day=day,
        attempt=failed_attempt,
        delay_seconds=delay,
    )
    if deadline is not None:
        remaining = (deadline - datetime.now()).total_seconds()
        if remaining <= 0:
            return None, failed_attempt, last_result
        delay = min(delay, remaining)
    await sleep(delay)
    return None, failed_attempt, last_result


async def run_account(
    service,
    day: str,
    once: bool = False,
    provider_factory=BrowserAccessRecordProvider,
    sleep=asyncio.sleep,
    event_writer=None,
    target_period: str | None = None,
    deadline: datetime | None = None,
    clock=None,
) -> dict:
    event_writer = _default_event_writer if event_writer is None else event_writer
    clock = datetime.now if clock is None else clock
    account_id = getattr(getattr(service, "settings", None), "account_id", "") or "default"

    def account_disabled() -> bool:
        # Re-read the flag every cycle: a 关闭账号 issued while this monitor
        # runs must stop it from re-booking or otherwise managing the account.
        return not is_account_enabled(account_id)

    if account_disabled():
        _emit_event(service, event_writer, "account_disabled", day=day, status="stopped")
        return {"status": "stopped", "message": "账号已关闭，动态监控停止。"}
    monitor = DynamicMonitor(service)
    _emit_event(service, event_writer, "monitor_start", day=day)
    prepare_kwargs = {"target_period": target_period} if target_period is not None else {}
    has_pending = getattr(monitor, "has_pending_commands", None)

    def pending_commands_exist() -> bool:
        return bool(has_pending(day)) if has_pending is not None else False

    prepared = monitor.prepare(day, **prepare_kwargs)
    pending_commands = pending_commands_exist()
    if prepared.get("status") == "disabled" and not pending_commands:
        _emit_event(service, event_writer, "monitor_disabled", day=day, status="disabled")
        return prepared
    if not prepared and not pending_commands and (deadline is None or once):
        _emit_event(service, event_writer, "monitor_idle", day=day, status="idle")
        return {"status": "idle", "message": "当天没有可监测的有效预约"}
    dry_run = bool(getattr(service.settings, "dry_run", False))
    provider = None
    last_result = prepared or {"status": "waiting", "message": "等待本地预约记录"}
    reconnect_attempt = 0
    first_cycle = True
    remote_schedule = getattr(monitor, "remote_query_plan", None)
    remote_retry_after = None
    try:
        while True:
            now = clock()
            if deadline is not None and now >= deadline:
                return last_result
            if account_disabled():
                _emit_event(service, event_writer, "account_disabled", day=day, status="stopped")
                return {"status": "stopped", "message": "账号已关闭，动态监控停止。"}
            if not first_cycle:
                prepared = monitor.prepare(day, **prepare_kwargs)
                pending_commands = pending_commands_exist()
            if prepared.get("status") == "disabled" and not pending_commands:
                _emit_event(service, event_writer, "monitor_disabled", day=day, status="disabled")
                return prepared
            has_work = bool(prepared) or pending_commands
            normal_poll = timedelta(
                seconds=getattr(service.settings, "dynamic_normal_poll_seconds", 180)
            )
            local_scan_only = False
            if callable(remote_schedule):
                plan = remote_schedule(day, now, target_period=target_period)
                stage = plan.get("stage") if isinstance(plan, dict) else None
                retrying = remote_retry_after is not None and now < remote_retry_after
                if pending_commands:
                    delay = timedelta(0)
                elif plan.get("due") and stage and not retrying:
                    delay = timedelta(0)
                else:
                    if deadline is None and once and not pending_commands:
                        return last_result
                    next_at = plan.get("next_at") if isinstance(plan, dict) else None
                    delay = normal_poll
                    if isinstance(next_at, datetime) and next_at > now:
                        delay = min(delay, next_at - now)
                    elif isinstance(next_at, datetime) and next_at <= now:
                        delay = normal_poll
                    local_scan_only = True
            elif not has_work:
                if deadline is None or once:
                    return last_result
                delay = normal_poll
            else:
                delay_kwargs = {"target_period": target_period} if target_period is not None else {}
                delay = monitor.next_poll_delay(day, now, **delay_kwargs)
                if delay is None:
                    if deadline is None:
                        return last_result
                    delay = normal_poll
                    local_scan_only = True
                elif delay > normal_poll:
                    delay = normal_poll
                    local_scan_only = True
            had_work = has_work
            first_cycle = False
            seconds = max(0.0, delay.total_seconds())
            if deadline is not None:
                seconds = min(seconds, max(0.0, (deadline - clock()).total_seconds()))
            if seconds:
                await sleep(seconds)
            now = clock()
            if deadline is not None and now >= deadline:
                return last_result
            refreshed = monitor.prepare(day, **prepare_kwargs)
            refreshed_pending = pending_commands_exist()
            if callable(remote_schedule):
                refreshed_plan = remote_schedule(day, now, target_period=target_period)
                if refreshed_pending:
                    stage = "commands"
                elif (
                    isinstance(refreshed_plan, dict)
                    and refreshed_plan.get("due")
                    and (remote_retry_after is None or now >= remote_retry_after)
                ):
                    stage = refreshed_plan.get("stage")
                else:
                    continue
                local_scan_only = False
            if not had_work and not refreshed_pending:
                # A new reservation is picked up on the next cycle so its own
                # dynamic window is recalculated before any browser access.
                continue
            if not refreshed and not refreshed_pending:
                continue
            if local_scan_only:
                continue
            access_error = ""
            reservation_error = ""
            records = []
            reservation_records = None
            if not dry_run and callable(remote_schedule) and stage == "commands":
                # Command execution owns its own short-lived booking browser;
                # a read-only monitor session must not hold the profile lock.
                pass
            elif not dry_run:
                if callable(remote_schedule):
                    _emit_event(service, event_writer, "remote_query_start", day=day, stage=stage)
                if provider is None:
                    candidate = provider_factory(service.settings)
                    _emit_event(
                        service,
                        event_writer,
                        "session_open",
                        day=day,
                        attempt=reconnect_attempt + 1,
                    )
                    try:
                        provider = await candidate.open()
                        if reconnect_attempt:
                            _emit_event(
                                service,
                                event_writer,
                                "session_recovered",
                                day=day,
                                attempt=reconnect_attempt,
                            )
                        else:
                            _emit_event(service, event_writer, "session_ready", day=day)
                    except AuthenticationError as exc:
                        await _close_provider(candidate, service, event_writer)
                        provider, reconnect_attempt, last_result = await _handle_authentication_failure(
                            service,
                            monitor,
                            day,
                            exc,
                            None,
                            reconnect_attempt,
                            event_writer,
                            sleep,
                            target_period,
                            deadline,
                        )
                        continue
                    except Exception as exc:
                        provider, reconnect_attempt, last_result = await _handle_browser_session_failure(
                            service,
                            monitor,
                            day,
                            exc,
                            candidate,
                            reconnect_attempt,
                            event_writer,
                            sleep,
                            target_period,
                            deadline,
                        )
                        continue
                try:
                    if callable(remote_schedule) and stage == "access":
                        if getattr(service.settings, "access_records_url", ""):
                            records = await provider.records(day)
                        else:
                            # No gate source: report an empty observation so
                            # the tick can record the access check as done.
                            records = []
                    elif callable(remote_schedule) and stage in {"reservation_first", "reservation_final", "entered"}:
                        reservation_records = await provider.reservation_records(day)
                    else:
                        reservation_records = await provider.reservation_records(day)
                except AuthenticationError as exc:
                    provider, reconnect_attempt, last_result = await _handle_authentication_failure(
                        service,
                        monitor,
                        day,
                        exc,
                        provider,
                        reconnect_attempt,
                        event_writer,
                        sleep,
                        target_period,
                        deadline,
                    )
                    continue
                except Exception as exc:
                    if callable(remote_schedule) and stage == "access":
                        access_error = f"门禁查询失败：{type(exc).__name__}"
                    else:
                        reservation_error = f"预约查询失败：{type(exc).__name__}"
                    _emit_event(
                        service,
                        event_writer,
                        "access_query_failure" if callable(remote_schedule) and stage == "access" else "reservation_query_failure",
                        day=day,
                        error_type=type(exc).__name__,
                        detail=exc,
                    )
                    if callable(remote_schedule):
                        provider, reconnect_attempt, last_result = await _handle_browser_session_failure(
                            service,
                            monitor,
                            day,
                            exc,
                            provider,
                            reconnect_attempt,
                            event_writer,
                            sleep,
                            target_period,
                            deadline,
                        )
                        continue
            if not callable(remote_schedule) and getattr(service.settings, "access_records_url", ""):
                try:
                    records = await provider.records(day)
                except AuthenticationError as exc:
                    provider, reconnect_attempt, last_result = await _handle_authentication_failure(
                        service,
                        monitor,
                        day,
                        exc,
                        provider,
                        reconnect_attempt,
                        event_writer,
                        sleep,
                        target_period,
                        deadline,
                    )
                    continue
                except Exception as exc:
                    access_error = f"门禁查询失败：{type(exc).__name__}"
                    _emit_event(
                        service,
                        event_writer,
                        "access_query_failure",
                        day=day,
                        error_type=type(exc).__name__,
                        detail=exc,
                    )
            if callable(remote_schedule) and provider is not None:
                await _close_provider(provider, service, event_writer)
                provider = None
                _emit_event(service, event_writer, "session_closed", day=day, reason="remote_check")
            if not access_error and not reservation_error:
                reconnect_attempt = 0
            operation_kwargs = {"target_period": target_period} if target_period is not None else {}
            if not callable(remote_schedule):
                needs_operation = monitor.requires_browser_operation(
                    day,
                    now,
                    records,
                    reservation_records,
                    **operation_kwargs,
                )
                if needs_operation and provider is not None:
                    await _close_provider(provider, service, event_writer)
                    provider = None
                    _emit_event(service, event_writer, "session_closed", day=day, reason="browser_operation")
            tick_kwargs = {"target_period": target_period} if target_period is not None else {}
            if callable(remote_schedule):
                tick_kwargs["remote_stage"] = stage
            last_result = await asyncio.to_thread(
                monitor.tick,
                day,
                now,
                records,
                access_error,
                reservation_records,
                reservation_error,
                **tick_kwargs,
            )
            _emit_event(
                service,
                event_writer,
                "monitor_tick",
                day=day,
                status="progress",
                summary=compact_message(
                    format_account_results(
                        {getattr(service, "account_id", "default"): last_result}
                    ),
                    limit=260,
                ),
            )
            if callable(remote_schedule):
                if access_error or reservation_error:
                    remote_retry_after = now + timedelta(
                        seconds=getattr(service.settings, "dynamic_boundary_poll_seconds", 120)
                    )
                else:
                    remote_retry_after = None
            if once:
                return last_result
    finally:
        _emit_event(
            service,
            event_writer,
            "monitor_exit",
            day=day,
            status=last_result.get("status", "progress") if isinstance(last_result, dict) else "progress",
            summary=compact_message(
                format_account_results({getattr(service, "account_id", "default"): last_result})
            ),
        )
        if provider is not None:
            await _close_provider(provider, service, event_writer)
            _emit_event(service, event_writer, "session_closed", day=day, reason="monitor_end")


async def _run_account_safely(service, day: str, kwargs: dict) -> tuple[str, dict]:
    account_id = getattr(service, "account_id", "default")
    try:
        result = await run_account(service, day, **kwargs)
    except Exception as exc:
        _emit_event(
            service,
            _default_event_writer,
            "monitor_failure",
            day=day,
            error_type=type(exc).__name__,
            detail=exc,
        )
        result = {
            "status": "error",
            "message": f"动态监控异常：{type(exc).__name__}",
        }
    return account_id, result


async def run_monitor(
    day: str,
    dry_run: bool = False,
    once: bool = False,
    period: str | None = None,
    run_for_minutes: int | None = None,
) -> dict:
    if run_for_minutes is not None and run_for_minutes <= 0:
        raise ValueError("run_for_minutes 必须大于 0")
    _, services = build_services(
        force_real=not dry_run,
        force_dry_run=dry_run,
        notify_reservation_results=True,
        notify_scheduler_summary=False,
    )
    deadline = (
        datetime.now() + timedelta(minutes=run_for_minutes)
        if run_for_minutes is not None
        else None
    )
    account_kwargs = []
    for service in services:
        kwargs = {"once": once}
        if period is not None:
            kwargs["target_period"] = period
        if deadline is not None:
            kwargs["deadline"] = deadline
        account_kwargs.append(_run_account_safely(service, day, kwargs))
    pairs = await asyncio.gather(*account_kwargs)
    return dict(pairs)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="运行共享动态预约补偿监控")
    parser.add_argument("--date", default=date.today().isoformat(), help="监测日期，默认今天")
    parser.add_argument("--dry-run", action="store_true", help="演练模式：不提交或取消真实预约")
    parser.add_argument("--once", action="store_true", help="只执行一轮查询后退出")
    parser.add_argument("--period", choices=PERIODS, help="只监测指定预约时段")
    parser.add_argument(
        "--run-for-minutes",
        type=int,
        help="运行指定分钟后退出；不指定时持续运行",
    )
    args = parser.parse_args(argv)
    if args.run_for_minutes is not None and args.run_for_minutes <= 0:
        parser.error("--run-for-minutes 必须大于 0")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    attach_file_log(prefix="dynamic-monitor")
    enable_event_log_file()
    keep_system_awake()
    try:
        result = asyncio.run(
            run_monitor(
                args.date,
                dry_run=args.dry_run,
                once=args.once,
                period=args.period,
                run_for_minutes=args.run_for_minutes,
            )
        )
    finally:
        allow_system_sleep()
    print(format_account_results(result, prefix="动态监控结束"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
