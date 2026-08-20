import seat_assistant.auth_flow as auth_flow
from seat_assistant.auth_flow import api_auth_headers, auth_header_names, captcha_input_selectors, credentials_available, is_seat_app_url, is_cas_url, library_selected, normalize_library


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
