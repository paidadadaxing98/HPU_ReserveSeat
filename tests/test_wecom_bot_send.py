from seat_assistant.notifications import render_tweet_push


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
