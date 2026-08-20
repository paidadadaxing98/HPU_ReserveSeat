import seat_assistant.auth_flow as auth_flow
from seat_assistant.auth_flow import api_auth_headers, auth_header_names, browser_api_headers, captcha_input_selectors, credentials_available, is_seat_app_url, is_cas_url, library_selected, normalize_library
from scripts.preview_reservation import record_api_auth, request_token


def test_blank_credentials_fall_back_to_existing_session():
    assert not credentials_available("", "")
    assert credentials_available("student", "secret")


def test_library_name_normalization():
    assert normalize_library(" 南校区  第二图书馆 ") == "南校区第二图书馆"


def test_seat_app_url_detects_existing_authenticated_session():
    assert is_seat_app_url("https://seatlib.hpu.edu.cn/libseat/#/home")
    assert not is_seat_app_url("https://seatlib.hpu.edu.cn/libseat/#/login")
    assert not is_seat_app_url("https://uia.hpu.edu.cn/cas/login")


def test_cas_url_is_distinguished_from_seat_app_routes():
    assert is_cas_url("https://uia.hpu.edu.cn/cas/login")
    assert not is_cas_url("https://seatlib.hpu.edu.cn/libseat/#/login")


def test_captcha_selectors_cover_common_cas_fields():
    selectors = captcha_input_selectors()
    assert "input[name='captcha']" in selectors
    assert "input[placeholder*='验证码']" in selectors


def test_api_auth_headers_keep_credentials_but_drop_transport_headers():
    headers = api_auth_headers({
        "authorization": "Bearer secret",
        "token": "secret-token",
        "accept": "application/json",
        "cookie": "session=secret",
        "host": "seatlib.hpu.edu.cn",
        "content-length": "123",
    })
    assert headers["authorization"] == "Bearer secret"
    assert headers["token"] == "secret-token"
    assert headers["accept"] == "application/json"
    assert "host" not in headers
    assert "content-length" not in headers


def test_browser_api_headers_drop_browser_forbidden_headers_but_keep_custom_auth():
    headers = browser_api_headers({
        "authorization": "Bearer secret",
        "x-hmac-request-key": "hmac-secret",
        "x-request-date": "2026-08-20T00:00:00Z",
        "cookie": "session=secret",
        "host": "seatlib.hpu.edu.cn",
        "referer": "https://seatlib.hpu.edu.cn/libseat/",
        "user-agent": "browser",
    })
    assert headers["authorization"] == "Bearer secret"
    assert headers["x-hmac-request-key"] == "hmac-secret"
    assert headers["x-request-date"] == "2026-08-20T00:00:00Z"
    assert "cookie" not in headers
    assert "host" not in headers
    assert "referer" not in headers
    assert "user-agent" not in headers


def test_auth_header_names_are_redacted_and_focus_on_auth_fields():
    assert auth_header_names({"Authorization": "secret", "token": "secret", "accept": "json"}) == ["authorization", "token"]


def test_end_time_request_variants_preserve_custom_token_header():
    variants_fn = getattr(auth_flow, "request_header_variants", None)
    assert callable(variants_fn)
    variants = variants_fn({"token": "secret-token", "accept": "application/json"})
    assert any(item.get("token") == "secret-token" for item in variants)


def test_library_selected_ignores_spacing_but_not_different_library():
    assert library_selected("南校区 第二图书馆", "南校区第二图书馆")
    assert not library_selected("南校区第一图书馆", "南校区第二图书馆")


def test_request_token_is_case_insensitive_and_decoded():
    assert request_token("https://seatlib.hpu.edu.cn/rest/v2/layout?ToKeN=a%2Bb") == "a+b"
    assert request_token("https://seatlib.hpu.edu.cn/rest/v2/layout") == ""


def test_record_api_auth_keeps_only_replayable_rest_authentication():
    state = {"headers": {}, "token": ""}
    record_api_auth(
        state,
        "https://seatlib.hpu.edu.cn/rest/v2/layout?token=page-token",
        {
            "Authorization": "Bearer secret",
            "X-Hmac-Request-Key": "hmac-secret",
            "Cookie": "session=secret",
            "Host": "seatlib.hpu.edu.cn",
        },
    )
    assert state["token"] == "page-token"
    assert state["headers"]["Authorization"] == "Bearer secret"
    assert "Cookie" not in state["headers"]
    assert "Host" not in state["headers"]

    unchanged = dict(state)
    record_api_auth(state, "https://seatlib.hpu.edu.cn/static/app.js?token=wrong", {"Authorization": "wrong"})
    assert state == unchanged
