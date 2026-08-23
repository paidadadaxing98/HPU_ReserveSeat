from seat_assistant.commands import parse_command


def test_parse_push_tweet_command_with_account_target():
    command = parse_command("推文 account03 标题 | https://example.test/a")

    assert command.kind == "push_tweet"
    assert command.target == "account03"
    assert command.title == "标题"
    assert command.url == "https://example.test/a"
    assert command.note is None


def test_parse_push_tweet_command_accepts_at_target_and_note():
    command = parse_command("推文 @account03 标题 | https://example.test/a | 备注")

    assert command.kind == "push_tweet"
    assert command.target == "account03"
    assert command.title == "标题"
    assert command.url == "https://example.test/a"
    assert command.note == "备注"


def test_parse_push_tweet_command_unwraps_markdown_link():
    command = parse_command("推文 account03 标题 | [https://example.test/a](https://example.test/a)")

    assert command.url == "https://example.test/a"


def test_parse_push_tweet_command_requires_title_and_url():
    assert parse_command("推文 account03 标题").kind == "help"
    assert parse_command("推文").kind == "help"
