"""Run the WeCom smart-bot long-connection service."""

import logging
from pathlib import Path

from seat_assistant.config import load_accounts, load_settings
from seat_assistant.wecom_bot import (
    AccountRecipientResolver,
    SingleInstanceLock,
    WebSocketTransport,
    WeComBotRunner,
    WeComCommandRouter,
)


def build_runner(settings=None, accounts=None, sleep=None):
    settings = settings or load_settings()
    accounts = accounts if accounts is not None else load_accounts()
    transport = WebSocketTransport(settings.wecom_bot_ws_url)
    resolver = AccountRecipientResolver(accounts, settings.wecom_bot_default_user)
    router = WeComCommandRouter(
        resolver,
        send_to_user=transport.send_to_user,
        reply=transport.reply,
    )
    return WeComBotRunner(
        bot_id=settings.wecom_bot_id,
        secret=settings.wecom_bot_secret,
        transport_factory=lambda: WebSocketTransport(settings.wecom_bot_ws_url),
        handler=router.handle,
        sleep=sleep or __import__("time").sleep,
    )


def main() -> int:
    settings = load_settings()
    if not settings.wecom_bot_id or not settings.wecom_bot_secret:
        print("未配置 SEAT_WECOM_BOT_ID 或 SEAT_WECOM_BOT_SECRET，机器人未启动。")
        return 2
    lock = SingleInstanceLock(Path(settings.wecom_bot_lock_file))
    if not lock.acquire():
        print("企业微信机器人已经在运行。")
        return 3
    try:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        build_runner(settings).run()
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
