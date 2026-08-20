"""Small OpenAI-compatible vision client for login captcha fallback.

The client is deliberately dependency-free. It accepts one image, asks for a
strict JSON answer, and validates the answer locally before returning it to the
browser automation layer.
"""

import base64
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class CaptchaVisionError(RuntimeError):
    """Raised when the configured vision service cannot produce an answer."""


def parse_captcha_answer(raw: str, kind: str) -> str | None:
    """Extract and validate the exact answer from a model JSON response."""
    if kind not in {"arithmetic", "letters", "auto"}:
        raise ValueError(f"未知验证码类型：{kind}")
    candidate = (raw or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    answer = payload.get("answer")
    if not isinstance(answer, str):
        return None
    answer = answer.strip()
    if kind in {"arithmetic", "auto"} and re.fullmatch(r"(?:0|[1-9]|1[0-9]|20)", answer):
        return answer if re.fullmatch(r"(?:0|[1-9]|1[0-9]|20)", answer) else None
    if kind in {"letters", "auto"}:
        return answer if re.fullmatch(r"[A-Za-z]{4}", answer) else None
    return None


class QwenCaptchaClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen3.7-flash",
        timeout_seconds: float = 15.0,
        opener=urlopen,
    ):
        if not api_key.strip():
            raise ValueError("验证码视觉模型 API Key 不能为空")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("验证码视觉模型 Base URL 必须是 http:// 或 https:// 地址")
        if timeout_seconds <= 0:
            raise ValueError("验证码视觉模型超时时间必须大于 0")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.opener = opener

    def solve(self, image_bytes: bytes, mime_type: str, kind: str) -> str:
        if not image_bytes:
            raise CaptchaVisionError("验证码图片为空")
        if kind not in {"arithmetic", "letters", "auto"}:
            raise ValueError(f"未知验证码类型：{kind}")
        encoded = base64.b64encode(image_bytes).decode("ascii")
        task = {
            "arithmetic": "20以内的加减法，只返回计算结果",
            "letters": "图片中的4个英文字母，只返回4个字母",
            "auto": "判断验证码是20以内加减法或4个英文字母；数字只返回0到20的结果，字母只返回4个英文字母",
        }[kind]
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 32,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": f"识别这张登录验证码。任务：{task}。严格只返回 JSON：{{\"answer\":\"...\"}}，不要返回解释。"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
                ],
            }],
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise CaptchaVisionError(f"验证码视觉模型请求失败（HTTP {exc.code}）") from exc
        except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise CaptchaVisionError("验证码视觉模型请求失败") from exc
        try:
            content = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise CaptchaVisionError("验证码视觉模型返回格式无法识别") from exc
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        answer = parse_captcha_answer(content, kind)
        if answer is None:
            raise CaptchaVisionError("验证码视觉模型返回了不合规答案")
        return answer
