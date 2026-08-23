import asyncio

from seat_assistant.wecom_bot import (
    OfficialSdkTransport,
    WeComBotMessage,
    sdk_frame_to_message,
)


def _text_frame():
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-1"},
        "body": {
            "msgid": "msg-1",
            "msgtype": "text",
            "from": {"userid": "sender-a"},
            "chatid": "sender-a",
            "chattype": "single",
            "response_url": "https://example.test/response",
            "text": {"content": "推文 account01 标题 | https://example.test/a"},
        },
    }


def test_sdk_text_frame_maps_to_internal_message():
    message = sdk_frame_to_message(_text_frame())

    assert message.message_id == "msg-1"
    assert message.request_id == "req-1"
    assert message.sender == "sender-a"
    assert message.text.startswith("推文 account01")
    assert message.response_url == "https://example.test/response"
    assert message.raw_frame == _text_frame()


def test_sdk_frame_converter_ignores_non_text_or_malformed_frames():
    assert sdk_frame_to_message({"body": {"msgtype": "image"}}) is None
    assert sdk_frame_to_message({"body": {"msgtype": "text"}}) is None


class FakeSdkClient:
    def __init__(self):
        self.sent = []
        self.replies = []
        self.cards = []

    async def send_message(self, chatid, body):
        self.sent.append((chatid, body))
        return {"errcode": 0}

    async def reply(self, frame, body):
        self.replies.append((frame, body))
        return {"errcode": 0}

    async def reply_template_card(self, frame, card):
        self.replies.append((frame, {"msgtype": "template_card", "template_card": card}))
        return {"errcode": 0}


def test_sdk_transport_sends_markdown_to_user_and_replies():
    client = FakeSdkClient()
    transport = OfficialSdkTransport(client)
    transport._loop = asyncio.new_event_loop()
    try:
        transport._loop.run_until_complete(asyncio.sleep(0))
        transport._client = client
        assert transport._run_sync(client.send_message("user-a", {"msgtype": "markdown", "markdown": {"content": "内容"}}))["errcode"] == 0
    finally:
        transport._loop.close()


def test_sdk_transport_reply_uses_original_frame():
    client = FakeSdkClient()
    transport = OfficialSdkTransport(client)
    transport._loop = asyncio.new_event_loop()
    transport._client = client
    message = WeComBotMessage(
        "msg-1", "req-1", "sender-a", "内容", raw_frame=_text_frame()
    )
    try:
        assert transport._run_sync(client.reply(message.raw_frame, {"msgtype": "text", "text": {"content": "已收到"}}))["errcode"] == 0
        assert client.replies[0][0] == _text_frame()
    finally:
        transport._loop.close()


def test_sdk_transport_sends_template_card_to_user():
    client = FakeSdkClient()
    transport = OfficialSdkTransport(client)
    transport._loop = asyncio.new_event_loop()
    transport._client = client
    card = {"msgtype": "template_card", "template_card": {"card_type": "text_notice"}}
    try:
        assert transport.send_template_card("user-a", card) is True
        assert client.sent == [("user-a", card)]
    finally:
        transport._loop.close()


def test_sdk_transport_delivers_outbox_card_and_removes_file(tmp_path):
    client = FakeSdkClient()
    transport = OfficialSdkTransport(client, bot_outbox_dir=tmp_path / "outbox")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    item = outbox / "delivery.json"
    item.write_text(
        '{"user_id": "user-a", "payload": {"msgtype": "template_card"}}',
        encoding="utf-8",
    )
    transport._loop = asyncio.new_event_loop()
    transport._client = client
    try:
        assert transport.deliver_outbox_once() is True
        assert not item.exists()
        assert client.sent == [("user-a", {"msgtype": "template_card"})]
    finally:
        transport._loop.close()
