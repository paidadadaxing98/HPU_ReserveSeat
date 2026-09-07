"""Run the WeCom smart-bot long-connection service."""

import argparse
from datetime import date
import logging
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seat_assistant.commands import parse_command
from seat_assistant.config import load_account_settings, load_accounts, load_settings, set_account_enabled
from seat_assistant.domain import build_reservation_for_arrival, parse_hhmm
from seat_assistant.notifications import WeComNotifier
from seat_assistant.reservation import DryRunReservation, PlaywrightReservation
from seat_assistant.runtime_logging import attach_file_log, compact_message, configure_runtime_logging
from seat_assistant.service import AssistantService
from seat_assistant.storage import Repository
from seat_assistant.wecom_bot import (
    AccountRecipientResolver,
    OfficialSdkTransport,
    SingleInstanceLock,
    WeComBotRunner,
    WeComCommandRouter,
    render_local_status,
)


# A pending command older than this is executed by the bot process itself, so
# a cancel or delay still runs when no dynamic monitor is active to claim it.
COMMAND_FALLBACK_SECONDS = 120


def build_runner(
    settings=None,
    accounts=None,
    sleep=None,
    fallback_delay_seconds=COMMAND_FALLBACK_SECONDS,
    command_service_factory=None,
):
    settings = settings or load_settings()
    accounts = accounts if accounts is not None else load_accounts()
    transport = OfficialSdkTransport(
        ws_url=settings.wecom_bot_ws_url,
        bot_outbox_dir=getattr(settings, "wecom_bot_outbox_dir", "logs/wecom-bot-outbox"),
    )
    resolver = AccountRecipientResolver(accounts, settings.wecom_bot_default_user)
    repositories = {
        account.id: Repository(str(account.db_path), account.id)
        for account in accounts
        if getattr(account, "db_path", None)
    }
    command_services: dict[str, AssistantService] = {}

    def get_command_service(account_id):
        """Build (once) a real service used only by the fallback executor."""
        service = command_services.get(account_id)
        if service is not None:
            return service
        if command_service_factory is not None:
            service = command_service_factory(account_id)
        else:
            account_settings = load_account_settings(account_id)
            adapter = DryRunReservation() if account_settings.dry_run else PlaywrightReservation(account_settings)
            notifier = WeComNotifier(
                account_settings.wecom_webhook,
                login_url=account_settings.login_url,
                bot_outbox_dir=account_settings.wecom_bot_outbox_dir,
                bot_user_id=account_settings.wecom_user_id,
                bot_enabled=bool(account_settings.wecom_bot_id and account_settings.wecom_bot_secret),
            )
            service = AssistantService(
                account_settings,
                Repository(str(account_settings.db_path), account_settings.account_id),
                adapter,
                notifier,
            )
        if service is not None:
            command_services[account_id] = service
        return service

    def execute_fallback(account, request_id):
        def run():
            time.sleep(max(0.0, fallback_delay_seconds))
            try:
                repo = Repository(str(account.db_path), account.id)
                item = repo.get_bot_command(request_id)
                if item is None or item["status"] != "pending":
                    return
                command = parse_command(item["text"])
                # 取消只由动态监控受理：取消要同步该时段的动态会话状态，
                # 两个进程各自主导会互相覆盖，因此认领之前先判类型，避免把
                # 命令留在无人处理的 processing 状态。推迟命令才兜底执行。
                if command.kind != "delay":
                    return
                if not repo.claim_bot_command(request_id):
                    return
                service = get_command_service(account.id)
                if service is None:
                    repo.complete_bot_command(request_id, "failed", {"ok": False, "message": "未找到可执行命令的账号配置。"})
                    return
                response = service.apply_command(command, item["day"])
                status = "completed" if response.get("ok") else "failed"
                repo.complete_bot_command(request_id, status, response)
                message = f"{item['text']}：{response.get('message') or '已处理。'}"
                logging.getLogger(__name__).info(
                    "机器人兜底执行%s：%s",
                    "成功" if response.get("ok") else "失败",
                    compact_message(message, limit=200),
                )
                send = getattr(service.notifier, "send", None)
                if callable(send):
                    try:
                        send(message)
                    except Exception as exc:
                        logging.getLogger(__name__).warning("兜底执行结果通知失败：%s", compact_message(exc))
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "机器人兜底执行异常：%s：%s",
                    type(exc).__name__,
                    compact_message(exc),
                )

        threading.Thread(target=run, daemon=True, name=f"bot-command-fallback-{request_id}").start()

    def submit_command(account_id, request_id, sender, text):
        repository = repositories.get(account_id)
        if repository is None:
            return False
        queued = repository.enqueue_bot_command(date.today().isoformat(), request_id, sender, text)
        if queued and fallback_delay_seconds is not None:
            account = next((item for item in accounts if item.id == account_id), None)
            if account is not None and getattr(account, "enabled", True):
                execute_fallback(account, request_id)
        return queued

    def read_status(account_id):
        repository = repositories.get(account_id)
        if repository is None:
            return "当前账号数据库不存在。"
        day = date.today().isoformat()
        return render_local_status(day, repository.reservations(day), repository.dynamic_sessions(day))

    def set_default(account_id, period, value):
        repository = repositories.get(account_id)
        if repository is None:
            return False
        account = next((item for item in accounts if item.id == account_id), None)
        period_config = getattr(account, "periods", {}).get(period) if account is not None else None
        if period_config is None:
            return False
        arrival, _, end = str(value).partition("-")
        arrival = arrival.strip()
        end = end.strip() or period_config.departure_window[0]
        build_reservation_for_arrival(
            parse_hhmm(arrival),
            tuple(parse_hhmm(item) for item in period_config.arrival_window),
            parse_hhmm(end),
        )
        repository.set_default(period, value)
        repository.event("default_override", period, value)
        return True

    def toggle_account(account_id, enabled):
        set_account_enabled(account_id, enabled)
        # Keep this process's view consistent; other processes pick the flag
        # up when they next start.
        for item in accounts:
            if item.id == account_id:
                # AccountSettings is frozen; keep the same object so the
                # resolver and command handlers see the new flag.
                object.__setattr__(item, "enabled", enabled)
        return True

    router = WeComCommandRouter(
        resolver,
        send_to_user=transport.send_to_user,
        reply=transport.reply,
        command_submitter=submit_command,
        status_reader=read_status,
        default_setter=set_default,
        account_toggler=toggle_account,
    )
    return WeComBotRunner(
        bot_id=settings.wecom_bot_id,
        secret=settings.wecom_bot_secret,
        transport_factory=lambda: transport,
        handler=router.handle,
        sleep=sleep or __import__("time").sleep,
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="运行企业微信智能机器人")
    parser.add_argument(
        "--run-for-minutes",
        type=int,
        default=0,
        help="运行指定分钟后自动退出；0 表示持续运行",
    )
    args = parser.parse_args(argv)
    if args.run_for_minutes < 0:
        parser.error("--run-for-minutes 必须大于等于 0")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    if not settings.wecom_bot_id or not settings.wecom_bot_secret:
        print("未配置 SEAT_WECOM_BOT_ID 或 SEAT_WECOM_BOT_SECRET，企业微信机器人已禁用。")
        return 0
    lock = SingleInstanceLock(Path(settings.wecom_bot_lock_file))
    if not lock.acquire():
        print("企业微信机器人已经在运行。")
        return 3
    try:
        configure_runtime_logging()
        attach_file_log(prefix="wecom-bot")
        runner = build_runner(settings)
        stop_timer = None
        if args.run_for_minutes:
            stop_timer = threading.Timer(args.run_for_minutes * 60, runner.stop)
            stop_timer.daemon = True
            stop_timer.start()
        try:
            runner.run()
        finally:
            if stop_timer is not None:
                stop_timer.cancel()
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
