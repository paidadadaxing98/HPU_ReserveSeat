from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import inspect
import json
import logging
from pathlib import Path
import queue
import threading
import time
import uuid
from urllib.error import URLError
from urllib.request import Request, urlopen

from .commands import BOT_COMMAND_HELP, REMOTE_COMMAND_HELP, REMOTE_COMMAND_KINDS, parse_command
from .notifications import render_tweet_card, render_tweet_push


@dataclass(frozen=True)
class WeComBotMessage:
    message_id: str
    request_id: str
    sender: str
    text: str
    chat_id: str = ""
    chat_type: str = ""
    response_url: str = ""
    raw_frame: dict | None = None


@dataclass(frozen=True)
class Recipient:
    account_id: str
    user_id: str
    display_name: str


_PERIOD_LABELS = {
    "morning": "上午",
    "afternoon": "下午",
    "evening": "晚上",
    "period04": "第4段",
    "period05": "第5段",
    "manual": "手动",
}

_STATUS_LABELS = {
    "reserved": "已预约",
    "uncertain": "结果待核验",
    "failed": "预约失败",
    "cancelled": "已取消",
    "monitoring": "监控中",
    "entered": "履约中",
    "away_cancelled": "暂离超时已取消",
}


def render_local_status(day: str, reservations, dynamic_sessions=()) -> str:
    """Render the latest account state available in local SQLite."""
    rows = list(reservations or [])
    if not rows:
        return f"当前状态（{day}）：暂无本地预约记录。"
    sessions = {
        str(item.get("period")): item
        for item in (dynamic_sessions or [])
        if isinstance(item, dict)
    }
    lines = [f"当前状态（{day}）："]
    for row in rows:
        period, status, start, end, room, seat = row[:6]
        session = sessions.get(str(period))
        if session and session.get("status") == "entered":
            status = "entered"
        label = _STATUS_LABELS.get(str(status), str(status))
        details = [f"{_PERIOD_LABELS.get(str(period), period)}：{label}"]
        if start and end:
            details.append(f"{start}-{end}")
        if room:
            details.append(str(room))
        if seat:
            details.append(f"座位{seat}")
        lines.append("，".join(details) + "。")
    return "\n".join(lines)


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
        self._active_transport = None


    def can_start(self) -> bool:

        return bool(self.bot_id and self.secret and self.transport_factory)


    def stop(self) -> None:

        self._stopped = True
        interrupt = getattr(self._active_transport, "interrupt", None)
        if interrupt is not None:
            interrupt()


    def run(self, max_cycles: int | None = None) -> None:

        if not self.can_start():

            raise ValueError("企业微信机器人缺少 Bot ID、Secret 或传输层")

        delay = 1.0
        cycles = 0
        while not self._stopped and (max_cycles is None or cycles < max_cycles):
            transport = self.transport_factory()
            self._active_transport = transport
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
                self._active_transport = None
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
        self._senders = {}
        for account in accounts:
            user_id = str(getattr(account, "wecom_user_id", "") or "").strip()
            if not user_id:
                continue
            aliases = tuple(str(value).strip() for value in getattr(account, "wecom_aliases", ()) if str(value).strip())
            display_name = aliases[0] if aliases else account.id
            recipient = Recipient(account.id, user_id, display_name)
            self._senders[user_id] = recipient
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


    def resolve_sender(self, sender: str | None) -> Recipient | None:

        return self._senders.get(str(sender or "").strip())


class WeComCommandRouter:

    def __init__(
        self,
        resolver: AccountRecipientResolver,
        send_to_user,
        reply,
        command_submitter=None,
        status_reader=None,
        default_setter=None,
    ):

        self.resolver = resolver
        self.send_to_user = send_to_user
        self.reply = reply
        self.command_submitter = command_submitter
        self.status_reader = status_reader
        self.default_setter = default_setter


    def handle(self, message: WeComBotMessage, transport=None) -> bool:

        send_to_user = getattr(transport, "send_to_user", self.send_to_user)
        reply = getattr(transport, "reply", self.reply)
        command = parse_command(message.text)
        if command.kind == "help":
            reply(message, BOT_COMMAND_HELP)
            return True
        if command.kind == "set_default":
            recipient = self.resolver.resolve_sender(message.sender)
            if recipient is None:
                reply(message, "没有权限修改默认到馆时间。")
                return False
            if self.default_setter is None:
                reply(message, "默认到馆时间修改功能尚未配置。")
                return False
            try:
                updated = self.default_setter(
                    recipient.account_id,
                    command.period,
                    command.at,
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("默认到馆时间写入失败", exc_info=True)
                reply(message, f"默认到馆时间修改失败：{type(exc).__name__}。")
                return False
            if not updated:
                reply(message, "默认到馆时间写入失败，请检查账号数据库。")
                return False
            period_label = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}.get(
                command.period,
                command.period,
            )
            reply(message, f"已将{period_label}默认到馆时间改为 {command.at}，已写入本地数据库。")
            return True
        if command.kind in REMOTE_COMMAND_KINDS:
            recipient = self.resolver.resolve_sender(message.sender)
            if recipient is None:
                reply(message, "没有权限执行座位控制命令。")
                return False
            if command.kind == "status":
                if self.status_reader is None:
                    reply(message, "当前状态查询未配置。")
                    return False
                try:
                    status = self.status_reader(recipient.account_id)
                except Exception as exc:
                    reply(message, f"状态查询失败：{type(exc).__name__}。")
                    return False
                reply(message, str(status))
                return True
            if self.command_submitter is None:
                reply(message, "座位控制命令尚未配置执行队列。")
                return False
            request_id = message.request_id.strip() or message.message_id
            queued = self.command_submitter(
                recipient.account_id,
                request_id,
                message.sender,
                message.text.strip(),
            )
            reply(message, (
                f"已收到命令：{message.text.strip()}。已写入本地数据库，等待动态监控受理。"
                if queued else "该命令已收到，数据库中已有记录，未重复执行。"
            ))
            return True
        if command.kind != "push_tweet":
            reply(message, f"{REMOTE_COMMAND_HELP}；推文 <账号或别名> 标题 | 链接 [| 备注]")
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
        card = render_tweet_card(
            recipient.display_name,
            recipient.user_id,
            command.title or "",
            command.url or "",
            command.note,
        )
        reply_template_card = getattr(transport, "reply_template_card", None)
        send_template_card = getattr(transport, "send_template_card", None)
        if recipient.user_id == message.sender and message.response_url:
            if reply_template_card:
                return bool(reply_template_card(message, card))
            sent = reply(message, content)
            if sent:
                return True
        if send_template_card:
            sent = send_template_card(recipient.user_id, card)
        else:
            sent = send_to_user(recipient.user_id, content)
        if not sent:
            reply(message, f"推文发送失败：{recipient.user_id}")
            return False
        reply(message, f"已发送给 {recipient.user_id}")
        return True


def sdk_frame_to_message(frame: dict) -> WeComBotMessage | None:
    """Convert an official SDK text callback frame to the project message type."""
    if not isinstance(frame, dict) or frame.get("cmd") != "aibot_msg_callback":
        return None
    body = frame.get("body") or {}
    if body.get("msgtype") != "text":
        return None
    text = body.get("text") or {}
    sender = (body.get("from") or {}).get("userid")
    content = text.get("content")
    if not sender or not isinstance(content, str):
        return None
    headers = frame.get("headers") or {}
    request_id = str(headers.get("req_id") or "")
    message_id = str(body.get("msgid") or body.get("msg_id") or request_id)
    if not message_id:
        return None
    return WeComBotMessage(
        message_id=message_id,
        request_id=request_id,
        sender=str(sender),
        text=content,
        chat_id=str(body.get("chatid") or ""),
        chat_type=str(body.get("chattype") or ""),
        response_url=str(body.get("response_url") or ""),
        raw_frame=frame,
    )


class OfficialSdkTransport:
    """Adapt the official async SDK client to the existing sync transport contract."""

    def __init__(
        self,
        client=None,
        *,
        ws_url: str = "",
        heartbeat_interval: int = 30000,
        bot_outbox_dir: str | Path = "logs/wecom-bot-outbox",
    ):
        self._client = client
        self.ws_url = ws_url
        self.heartbeat_interval = heartbeat_interval
        self.bot_outbox_dir = Path(bot_outbox_dir)
        self._loop = None
        self._thread = None
        self._messages = queue.Queue()
        self._closed = threading.Event()

    def connect(self, bot_id: str, secret: str) -> None:
        if self._client is None:
            try:
                from wecom_aibot_sdk import WSClient
            except ImportError as exc:
                raise RuntimeError("缺少 wecom-aibot-sdk 依赖，请安装项目运行依赖") from exc
            self._client = WSClient(
                bot_id,
                secret,
                ws_url=self.ws_url,
                heartbeat_interval=self.heartbeat_interval,
                max_reconnect_attempts=-1,
            )
        self._closed.clear()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._run_sync(self._register_and_connect())

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _register_and_connect(self) -> None:
        self._client.on("message.text", self._on_sdk_text)
        await self._client.connect()

    def _on_sdk_text(self, frame: dict) -> None:
        message = sdk_frame_to_message(frame)
        if message is not None:
            self._messages.put(message)

    def _run_sync(self, coroutine):
        if self._loop is None:
            raise RuntimeError("官方 SDK 尚未连接")
        if self._thread is None or not self._thread.is_alive():
            return self._loop.run_until_complete(coroutine)
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=30)

    def iter_messages(self):
        while not self._closed.is_set():
            self.deliver_outbox_once()
            try:
                yield self._messages.get(timeout=0.5)
            except queue.Empty:
                continue

    def interrupt(self) -> None:
        self._closed.set()

    def send_to_user(self, user_id: str, text: str) -> bool:
        try:
            result = self._run_sync(self._client.send_message(
                user_id,
                {"msgtype": "markdown", "markdown": {"content": text}},
            ))
            return not isinstance(result, dict) or result.get("errcode", 0) == 0
        except Exception:
            logging.getLogger(__name__).warning("企业微信官方 SDK 主动发送失败", exc_info=True)
            return False

    def send_template_card(self, user_id: str, card: dict) -> bool:
        try:
            result = self._run_sync(self._client.send_message(user_id, card))
            return not isinstance(result, dict) or result.get("errcode", 0) == 0
        except Exception:
            logging.getLogger(__name__).warning("企业微信官方 SDK 卡片发送失败", exc_info=True)
            return False

    def deliver_outbox_once(self) -> bool:
        if self._client is None or self._loop is None or not self.bot_outbox_dir.exists():
            return False
        delivered = False
        for path in sorted(self.bot_outbox_dir.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                user_id = str(item.get("user_id") or "").strip()
                payload = item.get("payload")
                if not user_id or not isinstance(payload, dict):
                    path.unlink(missing_ok=True)
                    continue
                if self.send_template_card(user_id, payload):
                    path.unlink(missing_ok=True)
                    delivered = True
            except Exception:
                logging.getLogger(__name__).warning("企业微信机器人投递箱处理失败：%s", path, exc_info=True)
        return delivered

    def reply(self, message: WeComBotMessage, text: str) -> bool:
        if message.raw_frame is None:
            return self.send_to_user(message.sender, text)
        try:
            stream_id = f"stream_{uuid.uuid4().hex}"
            reply_stream = getattr(self._client, "reply_stream", None)
            if callable(reply_stream):
                result = self._run_sync(reply_stream(
                    message.raw_frame,
                    stream_id,
                    text,
                    True,
                ))
            else:
                result = self._run_sync(self._client.reply(
                    message.raw_frame,
                    {
                        "msgtype": "stream",
                        "stream": {
                            "id": stream_id,
                            "finish": True,
                            "content": text,
                        },
                    },
                ))
            return not isinstance(result, dict) or result.get("errcode", 0) == 0
        except Exception:
            logging.getLogger(__name__).warning("企业微信官方 SDK 回复失败", exc_info=True)
            return False

    def reply_template_card(self, message: WeComBotMessage, card: dict) -> bool:
        if message.raw_frame is None:
            return self.send_template_card(message.sender, card)
        try:
            if hasattr(self._client, "reply_template_card"):
                result = self._run_sync(self._client.reply_template_card(
                    message.raw_frame,
                    card.get("template_card", card),
                ))
            else:
                result = self._run_sync(self._client.reply(message.raw_frame, card))
            return not isinstance(result, dict) or result.get("errcode", 0) == 0
        except Exception:
            logging.getLogger(__name__).warning("企业微信官方 SDK 卡片回复失败", exc_info=True)
            return False

    def close(self) -> None:
        self.interrupt()
        if self._client is not None and self._loop is not None:
            try:
                self._run_sync(self._client.disconnect())
            except Exception:
                logging.getLogger(__name__).warning("关闭企业微信官方 SDK 连接失败", exc_info=True)
        if self._loop is not None and self._thread is not None and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
        if self._loop is not None and not self._loop.is_running():
            self._loop.close()
        self._loop = None
        self._thread = None


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
