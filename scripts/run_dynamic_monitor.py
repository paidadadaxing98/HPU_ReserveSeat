"""Run the shared low-frequency dynamic compensation monitor."""

import argparse
import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path
import subprocess
import sys

from seat_assistant.access_records import AuthenticationError, BrowserAccessRecordProvider
from seat_assistant.dynamic_monitor import DynamicMonitor
from seat_assistant.main import build_services


PERIODS = ("morning", "afternoon", "evening", "period04", "period05")


def reconnect_delay(attempt: int, base: int, maximum: int) -> int:
    return min(maximum, base * (2 ** max(0, attempt)))


def reconnect_attempt_exhausted(attempt: int, maximum_attempts: int) -> bool:
    return attempt >= maximum_attempts


def _default_event_writer(line: str) -> None:
    print(line, flush=True)


def _emit_event(service, event_writer, event: str, **fields) -> None:
    settings = getattr(service, "settings", None)
    account_id = getattr(service, "account_id", None) or getattr(settings, "account_id", "default")
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "event": event,
        "account_id": str(account_id),
    }
    for key in ("day", "attempt", "delay_seconds", "error_type", "reason", "status"):
        if key in fields and fields[key] is not None:
            payload[key] = fields[key]
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
            _emit_event(service, event_writer, "session_close_failure", error_type=type(exc).__name__)
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
        print(
            f"[{datetime.now().isoformat(timespec='seconds')}] 动态监控无法写入安全保持状态：{type(exc).__name__}",
            flush=True,
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
) -> tuple[None, int, dict, bool]:
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
    )
    last_result = await _record_authentication_hold(monitor, day, error, target_period)
    _emit_event(service, event_writer, "safe_hold", day=day, attempt=failed_attempt, status="safe_hold")
    if reconnect_attempt_exhausted(
        failed_attempt,
        getattr(service.settings, "dynamic_reconnect_max_attempts", 5),
    ):
        _emit_event(
            service,
            event_writer,
            "safe_stop",
            day=day,
            attempt=failed_attempt,
            status="safe_hold",
        )
        return None, failed_attempt, last_result, True
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
            return None, failed_attempt, last_result, True
        delay = min(delay, remaining)
    await sleep(delay)
    return None, failed_attempt, last_result, False


def start_managed_bot(settings, minutes: int):
    """Start one bounded WeCom bot owned by the dynamic monitor process."""
    if not getattr(settings, "wecom_bot_id", "") or not getattr(settings, "wecom_bot_secret", ""):
        return None
    project_root = Path(__file__).resolve().parents[1]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "scripts.run_wecom_bot",
                "--run-for-minutes",
                str(minutes),
            ],
            cwd=str(project_root),
            creationflags=creationflags,
        )
    except Exception as exc:
        print(f"企业微信机器人启动失败：{exc}", flush=True)
        return None


def stop_managed_bot(process) -> None:
    """Stop a monitor-owned bot without leaving a child process behind."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


async def run_account(
    service,
    day: str,
    once: bool = False,
    provider_factory=BrowserAccessRecordProvider,
    sleep=asyncio.sleep,
    event_writer=None,
    target_period: str | None = None,
    deadline: datetime | None = None,
) -> dict:
    event_writer = _default_event_writer if event_writer is None else event_writer
    monitor = DynamicMonitor(service)
    _emit_event(service, event_writer, "monitor_start", day=day)
    prepare_kwargs = {"target_period": target_period} if target_period is not None else {}
    prepared = monitor.prepare(day, **prepare_kwargs)
    if prepared.get("status") == "disabled" and not monitor.has_pending_commands(day):
        _emit_event(service, event_writer, "monitor_disabled", day=day, status="disabled")
        return prepared
    if not prepared and not monitor.has_pending_commands(day):
        _emit_event(service, event_writer, "monitor_idle", day=day, status="idle")
        return {"status": "idle", "message": "当天没有可监测的有效预约"}
    dry_run = bool(getattr(service.settings, "dry_run", False))
    provider = None
    last_result = prepared
    reconnect_attempt = 0
    try:
        while True:
            now = datetime.now()
            if deadline is not None and now >= deadline:
                return last_result
            delay_kwargs = {"target_period": target_period} if target_period is not None else {}
            delay = monitor.next_poll_delay(day, now, **delay_kwargs)
            if delay is None:
                return last_result
            seconds = max(0.0, delay.total_seconds())
            if deadline is not None:
                seconds = min(seconds, max(0.0, (deadline - datetime.now()).total_seconds()))
                if seconds <= 0:
                    return last_result
            if seconds:
                await sleep(seconds)
            now = datetime.now()
            if deadline is not None and now >= deadline:
                return last_result
            access_error = ""
            reservation_error = ""
            records = []
            reservation_records = None
            if not dry_run:
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
                        reconnect_attempt = 0
                    except AuthenticationError as exc:
                        await _close_provider(candidate, service, event_writer)
                        provider, reconnect_attempt, last_result, exhausted = await _handle_authentication_failure(
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
                        if exhausted:
                            return last_result
                        continue
                try:
                    reservation_records = await provider.reservation_records(day)
                except AuthenticationError as exc:
                    provider, reconnect_attempt, last_result, exhausted = await _handle_authentication_failure(
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
                    if exhausted:
                        return last_result
                    continue
                except Exception as exc:
                    reservation_error = f"预约查询失败：{type(exc).__name__}"
                    _emit_event(
                        service,
                        event_writer,
                        "reservation_query_failure",
                        day=day,
                        error_type=type(exc).__name__,
                    )
                if getattr(service.settings, "access_records_url", ""):
                    try:
                        records = await provider.records(day)
                    except AuthenticationError as exc:
                        provider, reconnect_attempt, last_result, exhausted = await _handle_authentication_failure(
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
                        if exhausted:
                            return last_result
                        continue
                    except Exception as exc:
                        access_error = f"门禁查询失败：{type(exc).__name__}"
                        _emit_event(
                            service,
                            event_writer,
                            "access_query_failure",
                            day=day,
                            error_type=type(exc).__name__,
                        )
            operation_kwargs = {"target_period": target_period} if target_period is not None else {}
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
            if once:
                return last_result
    finally:
        if provider is not None:
            await _close_provider(provider, service, event_writer)
            _emit_event(service, event_writer, "session_closed", day=day, reason="monitor_end")


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
    results = {}
    managed_bot = None
    deadline = (
        datetime.now() + timedelta(minutes=run_for_minutes)
        if run_for_minutes is not None
        else None
    )
    if run_for_minutes is not None and services and _has_monitor_work(services, day, period):
        managed_bot = start_managed_bot(services[0].settings, run_for_minutes)
    try:
        for service in services:
            account_id = getattr(service, "account_id", "default")
            kwargs = {"once": once}
            if period is not None:
                kwargs["target_period"] = period
            if deadline is not None:
                kwargs["deadline"] = deadline
            results[account_id] = await run_account(service, day, **kwargs)
        return results
    finally:
        stop_managed_bot(managed_bot)


def _has_monitor_work(services, day: str, period: str | None) -> bool:
    """Preflight local state so empty dynamic tasks do not start a bot."""
    for service in services:
        repository = getattr(service, "repo", None)
        if repository is None:
            # Keep the entrypoint testable with lightweight service doubles.
            return True
        if repository.pending_bot_commands(day, limit=1):
            return True
        try:
            monitor = DynamicMonitor(service)
            kwargs = {"target_period": period} if period is not None else {}
            prepared = monitor.prepare(day, **kwargs)
        except Exception:
            return True
        if prepared and prepared.get("status") != "disabled":
            return True
    return False


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
    result = asyncio.run(
        run_monitor(
            args.date,
            dry_run=args.dry_run,
            once=args.once,
            period=args.period,
            run_for_minutes=args.run_for_minutes,
        )
    )
    print(f"动态补偿监控已启动/结束：{result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
