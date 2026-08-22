from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import inspect
import json
import logging
from pathlib import Path
import threading
import time
import uuid
from urllib.error import URLError
from urllib.request import Request, urlopen

from .commands import parse_command
from .notifications import render_tweet_push


@dataclass(frozen=True)
class WeComBotMessage:
    message_id: str
    request_id: str
    sender: str
    text: str
    chat_id: str = ""
    chat_type: str = ""
    response_url: str = ""


@dataclass(frozen=True)
class Recipient:
    account_id: str
    user_id: str
    display_name: str


class MessageDeduplicator:

    def __init__(self, max_items: int = 1024):

        if max_items <= 0:

            raise ValueError("max_items must be positive")

        self.max_items = max_items

        self._items = OrderedDict()


    def seen(self, message_id: str) -> bool:

        if message_id in self._items:

            self._items.move_to_end(message_id)

            return True

        self._items[message_id] = None

        while len(self._items) > self.max_items:

            self._items.popitem(last=False)

        return False


class SingleInstanceLock:

    def __init__(self, path):

        self.path = path

        self._handle = None


    def acquire(self) -> bool:

        try:

            self.path.parent.mkdir(parents=True, exist_ok=True)

            self._handle = self.path.open("x", encoding="utf-8")

        except FileExistsError:

            return False

        self._handle.write("locked")

        self._handle.flush()

        return True


    def release(self) -> None:

        if self._handle is not None:

            self._handle.close()

            self._handle = None

        try:

            self.path.unlink()

        except FileNotFoundError:

            pass


    def __enter__(self):

        if not self.acquire():

            raise RuntimeError("企业微信机器人已经在运行")

        return self


    def __exit__(self, exc_type, exc, tb):

        self.release()


class WeComBotRunner:

    def __init__(
        self,
        bot_id: str,
        secret: str,
        transport_factory,
        handler=None,
        sleep=time.sleep,
        max_reconnect_delay: float = 60.0,
        deduplicator: MessageDeduplicator | None = None,
        heartbeat_interval: float = 20.0,
    ):

        self.bot_id = bot_id

        self.secret = secret

        self.transport_factory = transport_factory
        self.handler = handler
        self.sleep = sleep
        self.max_reconnect_delay = max_reconnect_delay
        self.deduplicator = deduplicator or MessageDeduplicator()
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_delays = []
        self._stopped = False


    def can_start(self) -> bool:

        return bool(self.bot_id and self.secret and self.transport_factory)


    def stop(self) -> None:

        self._stopped = True


    def run(self, max_cycles: int | None = None) -> None:

        if not self.can_start():

            raise ValueError("企业微信机器人缺少 Bot ID、Secret 或传输层")

        delay = 1.0
        cycles = 0
        while not self._stopped and (max_cycles is None or cycles < max_cycles):
            transport = self.transport_factory()
            heartbeat_stop = threading.Event()
            heartbeat_thread = None
            try:
                transport.connect(self.bot_id, self.secret)
                if hasattr(transport, "heartbeat"):
                    heartbeat_thread = threading.Thread(
                        target=self._heartbeat_loop,
                        args=(transport, heartbeat_stop),
                        daemon=True,
                    )
                    heartbeat_thread.start()
                for message in transport.iter_messages():
                    if self._stopped:
                        break
                    if self.deduplicator.seen(message.message_id):
                        continue
                    self._invoke_handler(message, transport)
            except Exception:
                logging.getLogger(__name__).warning("企业微信机器人连接断开，准备重连", exc_info=True)
                if self._stopped:
                    break
            finally:
                heartbeat_stop.set()
                if heartbeat_thread is not None:
                    heartbeat_thread.join(timeout=1)
                close = getattr(transport, "close", None)
                if close:
                    close()
            cycles += 1
            if self._stopped or max_cycles is not None and cycles >= max_cycles:
                break
            self.reconnect_delays.append(delay)
            self.sleep(delay)
            delay = min(self.max_reconnect_delay, delay * 2)


    def _heartbeat_loop(self, transport, stop_event: threading.Event) -> None:

        while not self._stopped and not stop_event.is_set():
            self.sleep(self.heartbeat_interval)
            if self._stopped or stop_event.is_set():
                break
            try:
                transport.heartbeat()
            except Exception:
                break


    def _invoke_handler(self, message, transport) -> None:

        if self.handler is None:
            return
        parameters = inspect.signature(self.handler).parameters
        if len(parameters) >= 2:
            self.handler(message, transport)
        else:
            self.handler(message)


class AccountRecipientResolver:

    def __init__(self, accounts, default_user: str = ""):

        self.default_user = default_user.strip()
        self._recipients = {}
        for account in accounts:
            user_id = str(getattr(account, "wecom_user_id", "") or "").strip()
            if not user_id:
                continue
            aliases = tuple(str(value).strip() for value in getattr(account, "wecom_aliases", ()) if str(value).strip())
            display_name = aliases[0] if aliases else account.id
            recipient = Recipient(account.id, user_id, display_name)
            keys = [account.id, user_id, *getattr(account, "wecom_aliases", ())]
            for key in keys:
                normalized = str(key).strip().lstrip("@")
                if normalized:
                    self._recipients[normalized] = recipient


    def resolve(self, target: str | None) -> Recipient | None:

        key = str(target or "").strip().lstrip("@")
        if key:
            return self._recipients.get(key)
        if self.default_user:
            for recipient in self._recipients.values():
                if recipient.user_id == self.default_user:
                    return recipient
        return None


class WeComCommandRouter:

    def __init__(self, resolver: AccountRecipientResolver, send_to_user, reply):

        self.resolver = resolver
        self.send_to_user = send_to_user
        self.reply = reply


    def handle(self, message: WeComBotMessage, transport=None) -> bool:

        send_to_user = getattr(transport, "send_to_user", self.send_to_user)
        reply = getattr(transport, "reply", self.reply)
        command = parse_command(message.text)
        if command.kind != "push_tweet":
            reply(message, "支持命令：推文 <账号或别名> 标题 | 链接 [| 备注]")
            return False
        recipient = self.resolver.resolve(command.target)
        if recipient is None:
            reply(message, f"未找到推文接收人：{command.target}")
            return False
        content = render_tweet_push(
            recipient.display_name,
            recipient.user_id,
            command.title or "",
            command.url or "",
            command.note,
        )
        if recipient.user_id == message.sender and message.response_url:
            sent = reply(message, content)
            if sent:
                return True
        else:
            sent = send_to_user(recipient.user_id, content)
        if not sent:
            reply(message, f"推文发送失败：{recipient.user_id}")
            return False
        reply(message, f"已发送给 {recipient.user_id}")
        return True


class WebSocketTransport:

    def __init__(self, ws_url: str = "wss://openws.work.weixin.qq.com", timeout: float = 30.0):

        self.ws_url = ws_url
        self.timeout = timeout
        self._socket = None


    def connect(self, bot_id: str, secret: str) -> None:

        try:
            from websockets.sync.client import connect
        except ImportError as exc:
            raise RuntimeError("缺少 websockets 依赖，请安装项目运行依赖") from exc
        self._socket = connect(self.ws_url, open_timeout=self.timeout)
        self._socket.send(json.dumps({
            "cmd": "aibot_subscribe",
            "headers": {"req_id": uuid.uuid4().hex},
            "body": {"bot_id": bot_id, "secret": secret},
        }, ensure_ascii=False))


    def iter_messages(self):

        if self._socket is None:
            return
        for raw in self._socket:
            payload = json.loads(raw)
            if payload.get("cmd") != "aibot_msg_callback":
                continue
            body = payload.get("body") or {}
            text = body.get("text") or {}
            yield WeComBotMessage(
                message_id=str(body.get("msgid") or body.get("msg_id") or payload.get("headers", {}).get("req_id") or ""),
                request_id=str(payload.get("headers", {}).get("req_id") or ""),
                sender=str((body.get("from") or {}).get("userid") or ""),
                text=str(text.get("content") or ""),
                chat_id=str(body.get("chatid") or ""),
                chat_type=str(body.get("chattype") or ""),
                response_url=str(
                    body.get("response_url")
                    or payload.get("response_url")
                    or ""
                ),
            )


    def send_to_user(self, user_id: str, text: str) -> bool:

        if self._socket is None:
            return False
        self._socket.send(json.dumps({
            "cmd": "aibot_send_msg",
            "headers": {"req_id": uuid.uuid4().hex},
            "body": {
                "to_user": user_id,
                "msgtype": "markdown",
                "markdown": {"content": text},
            },
        }, ensure_ascii=False))
        return True


    def reply(self, message: WeComBotMessage, text: str) -> bool:

        if message.response_url:
            try:
                body = json.dumps({
                    "msgtype": "text",
                    "text": {"content": text},
                }, ensure_ascii=False).encode("utf-8")
                request = Request(
                    message.response_url,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=10) as response:
                    return getattr(response, "status", 200) == 200
            except (URLError, TimeoutError, ValueError, json.JSONDecodeError):
                pass
        return self.send_to_user(message.sender, text)


    def heartbeat(self) -> bool:

        if self._socket is None:
            return False
        self._socket.send(json.dumps({
            "cmd": "ping",
            "headers": {"req_id": uuid.uuid4().hex},
        }, ensure_ascii=False))
        return True


    def close(self) -> None:

        if self._socket is not None:
            self._socket.close()
            self._socket = None
