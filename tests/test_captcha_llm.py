import json

import pytest

from seat_assistant.captcha_llm import (
    CaptchaVisionError,
    QwenCaptchaClient,
    parse_captcha_answer,
)


def test_parse_arithmetic_answer_accepts_json_and_returns_numeric_result():
    assert parse_captcha_answer('{"answer":"17"}', "arithmetic") == "17"


def test_parse_letter_answer_requires_exactly_four_ascii_letters():
    assert parse_captcha_answer('{"answer":"aB9d"}', "letters") is None
    assert parse_captcha_answer('{"answer":"aBcd"}', "letters") == "aBcd"


def test_parse_auto_answer_accepts_either_supported_captcha_family():
    assert parse_captcha_answer('{"answer":"20"}', "auto") == "20"
    assert parse_captcha_answer('{"answer":"aBcd"}', "auto") == "aBcd"
    assert parse_captcha_answer('{"answer":"123"}', "auto") is None


def test_parse_captcha_answer_rejects_model_prose_and_invalid_values():
    assert parse_captcha_answer("I think the answer is 17", "arithmetic") is None
    assert parse_captcha_answer('{"answer":"99"}', "arithmetic") is None
    assert parse_captcha_answer('{"answer":""}', "letters") is None


def test_parse_captcha_answer_accepts_single_json_code_fence_from_model():
    raw = '```json\n{"answer":"fmqV"}\n```'
    assert parse_captcha_answer(raw, "letters") == "fmqV"


def test_qwen_client_sends_only_image_and_structured_prompt_without_logging_secret():
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": '{"answer":"5"}'}}]
                }).encode()

        return Response()

    client = QwenCaptchaClient(
        api_key="secret-key",
        base_url="https://example.test/v1",
        model="qwen3.7-flash",
        timeout_seconds=3,
        opener=opener,
    )

    assert client.solve(b"png-bytes", "image/png", "arithmetic") == "5"
    request, timeout = calls[0]
    assert timeout == 3
    assert request.full_url == "https://example.test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer secret-key"
    payload = json.loads(request.data)
    assert payload["model"] == "qwen3.7-flash"
    assert "png-bytes" not in payload["messages"][0]["content"][1]["image_url"]["url"]
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_qwen_client_auto_mode_accepts_either_supported_answer_family():
    def opener(request, timeout):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"{\\"answer\\":\\"aBcd\\"}"}}]}'

        return Response()

    client = QwenCaptchaClient("secret-key", opener=opener)

    assert client.solve(b"png-bytes", "image/png", "auto") == "aBcd"


def test_qwen_client_converts_http_failures_to_safe_error():
    def opener(request, timeout):
        raise OSError("network down")

    client = QwenCaptchaClient(
        api_key="secret-key",
        base_url="https://example.test/v1",
        model="qwen3.7-flash",
        opener=opener,
    )

    with pytest.raises(CaptchaVisionError, match="验证码视觉模型请求失败"):
        client.solve(b"png-bytes", "image/png", "letters")
