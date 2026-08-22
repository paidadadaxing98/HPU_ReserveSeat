from seat_assistant.notifications import render_tweet_push
from seat_assistant.wecom_bot import WeComBotMessage, WebSocketTransport
from unittest.mock import patch


def test_render_tweet_push_contains_target_and_link():
    text = render_tweet_push("account03", "用户A", "标题", "https://example.test/a")

    assert "account03" in text
    assert "用户A" in text
    assert "标题" in text
    assert "https://example.test/a" in text


def test_render_tweet_push_includes_optional_note():
    text = render_tweet_push("account03", "用户A", "标题", "https://example.test/a", "备注")

    assert "备注" in text


def test_wecom_sender_posts_text_to_user_payload():
    requests = []

    class Sender:
        def send_to_user(self, user_id, text):
            requests.append((user_id, text))
            return True

    assert Sender().send_to_user("user-a", "content") is True
    assert requests == [("user-a", "content")]


def test_wecom_transport_replies_through_response_url():
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    transport = WebSocketTransport()
    message = WeComBotMessage("msg-1", "req-1", "sender-a", "内容", response_url="https://example.test/reply")

    with patch("seat_assistant.wecom_bot.urlopen", return_value=FakeResponse()) as opened:
        assert transport.reply(message, "已收到") is True

    assert opened.call_args.args[0].full_url == "https://example.test/reply"
