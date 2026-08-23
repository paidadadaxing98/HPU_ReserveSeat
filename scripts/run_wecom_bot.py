"""Run the WeCom smart-bot long-connection service."""

import argparse
import logging
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seat_assistant.config import load_accounts, load_settings
from seat_assistant.wecom_bot import (
    AccountRecipientResolver,
    OfficialSdkTransport,
    SingleInstanceLock,
    WeComBotRunner,
    WeComCommandRouter,
)


def build_runner(settings=None, accounts=None, sleep=None):
    settings = settings or load_settings()
    accounts = accounts if accounts is not None else load_accounts()
    transport = OfficialSdkTransport(
        ws_url=settings.wecom_bot_ws_url,
        bot_outbox_dir=getattr(settings, "wecom_bot_outbox_dir", "logs/wecom-bot-outbox"),
    )
    resolver = AccountRecipientResolver(accounts, settings.wecom_bot_default_user)
    router = WeComCommandRouter(
        resolver,
        send_to_user=transport.send_to_user,
        reply=transport.reply,
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
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
