"""Run the shared low-frequency dynamic compensation monitor."""

import argparse
import asyncio
import json
from datetime import date, datetime

from seat_assistant.access_records import AuthenticationError, BrowserAccessRecordProvider
from seat_assistant.dynamic_monitor import DynamicMonitor
from seat_assistant.main import build_services


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


async def _record_authentication_hold(monitor, day: str, error: Exception) -> dict:
    message = f"动态监控认证失效，暂停预约操作：{type(error).__name__}"
    try:
        return await asyncio.to_thread(
            monitor.tick,
            day,
            datetime.now(),
            [],
            message,
            None,
            message,
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
    last_result = await _record_authentication_hold(monitor, day, error)
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
    await sleep(delay)
    return None, failed_attempt, last_result, False


async def run_account(
    service,
    day: str,
    once: bool = False,
    provider_factory=BrowserAccessRecordProvider,
    sleep=asyncio.sleep,
    event_writer=None,
) -> dict:
    event_writer = _default_event_writer if event_writer is None else event_writer
    monitor = DynamicMonitor(service)
    _emit_event(service, event_writer, "monitor_start", day=day)
    prepared = monitor.prepare(day)
    if prepared.get("status") == "disabled":
        _emit_event(service, event_writer, "monitor_disabled", day=day, status="disabled")
        return prepared
    if not prepared:
        _emit_event(service, event_writer, "monitor_idle", day=day, status="idle")
        return {"status": "idle", "message": "当天没有可监测的有效预约"}
    dry_run = bool(getattr(service.settings, "dry_run", False))
    provider = None
    last_result = prepared
    reconnect_attempt = 0
    try:
        while True:
            now = datetime.now()
            delay = monitor.next_poll_delay(day, now)
            if delay is None:
                return last_result
            seconds = max(0.0, delay.total_seconds())
            if seconds:
                await sleep(seconds)
            now = datetime.now()
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
            needs_operation = monitor.requires_browser_operation(day, now, records, reservation_records)
            if needs_operation and provider is not None:
                await _close_provider(provider, service, event_writer)
                provider = None
                _emit_event(service, event_writer, "session_closed", day=day, reason="browser_operation")
            last_result = await asyncio.to_thread(
                monitor.tick,
                day,
                now,
                records,
                access_error,
                reservation_records,
                reservation_error,
            )
            if once:
                return last_result
    finally:
        if provider is not None:
            await _close_provider(provider, service, event_writer)
            _emit_event(service, event_writer, "session_closed", day=day, reason="monitor_end")


async def run_monitor(day: str, dry_run: bool = False, once: bool = False) -> dict:
    _, services = build_services(
        force_real=not dry_run,
        force_dry_run=dry_run,
        notify_reservation_results=True,
        notify_scheduler_summary=False,
    )
    results = {}
    for service in services:
        account_id = getattr(service, "account_id", "default")
        results[account_id] = await run_account(service, day, once=once)
    return results


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="运行共享动态预约补偿监控")
    parser.add_argument("--date", default=date.today().isoformat(), help="监测日期，默认今天")
    parser.add_argument("--dry-run", action="store_true", help="演练模式：不提交或取消真实预约")
    parser.add_argument("--once", action="store_true", help="只执行一轮查询后退出")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    result = asyncio.run(run_monitor(args.date, dry_run=args.dry_run, once=args.once))
    print(f"动态补偿监控已启动/结束：{result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
