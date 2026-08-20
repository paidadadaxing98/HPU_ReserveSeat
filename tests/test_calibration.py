from seat_assistant.calibration import sanitize_url


def test_sanitize_url_removes_token_and_query_values():
    value = "https://seatlib.hpu.edu.cn/libseat/?token=secret#/home"
    assert sanitize_url(value) == "https://seatlib.hpu.edu.cn/libseat/#/home"
