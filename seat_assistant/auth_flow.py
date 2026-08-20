from dataclasses import dataclass


@dataclass(frozen=True)
class Credentials:
    account: str
    password: str


def credentials_available(account: str, password: str) -> bool:
    return bool(account.strip() and password.strip())


def normalize_library(value: str) -> str:
    return "".join(value.split())


def library_selected(current: str, target: str) -> bool:
    return normalize_library(current) == normalize_library(target)


def is_seat_app_url(value: str) -> bool:
    return "/libseat/" in value and "#/home" in value


def is_cas_url(value: str) -> bool:
    return "uia.hpu.edu.cn/cas/" in value


def captcha_input_selectors() -> tuple[str, ...]:
    return (
        "input[name='captcha']",
        "input[name='verifyCode']",
        "input[name='verificationCode']",
        "input[placeholder*='验证码']",
        "input[aria-label*='验证码']",
    )


def api_auth_headers(headers: dict[str, str]) -> dict[str, str]:
    blocked = {"host", "content-length", "connection", "accept-encoding"}
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in blocked and not key.lower().startswith("sec-")
    }


def browser_api_headers(headers: dict[str, str]) -> dict[str, str]:
    """Keep replayable authentication headers for same-origin browser fetch.

    Playwright can read browser-managed headers from a real request, but page
    JavaScript cannot set transport or browser identity headers such as Cookie,
    Host, Referer, or User-Agent. Cookies remain available to same-origin
    ``fetch`` automatically, so only custom authentication/application headers
    are copied into the replay request.
    """
    blocked = {
        "accept-encoding",
        "connection",
        "content-length",
        "cookie",
        "host",
        "origin",
        "referer",
        "user-agent",
    }
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in blocked and not key.lower().startswith("sec-")
    }


def request_header_variants(headers: dict[str, str]) -> list[dict[str, str]]:
    """Build replay variants while retaining custom token headers from the page request."""
    base = dict(headers)
    variants = [base]
    for key in ("authorization", "token", "x-auth-token", "x-token", "access-token"):
        value = next((item for name, item in headers.items() if name.lower() == key), None)
        if not value:
            continue
        candidate = dict(base)
        candidate[key] = value
        if candidate not in variants:
            variants.append(candidate)
    return variants


def auth_header_names(headers: dict[str, str]) -> list[str]:
    markers = ("auth", "token", "cookie", "ticket", "session")
    return sorted(
        key.lower()
        for key in headers
        if any(marker in key.lower() for marker in markers)
    )
