from seat_assistant.submission import confirmation_required, end_time_response_matches_start, end_times_request_url, end_times_response_matches, normalize_time_option, reservation_matches, requested_times_available, submission_settled, time_option_id, time_options, time_to_minutes, time_values


def test_submission_requires_explicit_flag_and_phrase():
    assert confirmation_required(False, "SUBMIT")
    assert confirmation_required(True, "submit")
    assert not confirmation_required(True, "SUBMIT")


def test_reservation_matches_expected_fields():
    text = "日期：2026-08-20\n阅览室：4层计算机类借阅区\n座位：169\n时间：09:00 - 12:00"
    assert reservation_matches(text, "2026-08-20", "4层计算机类借阅区", "169", "09:00", "12:00")
    assert not reservation_matches(text, "2026-08-21", "4层计算机类借阅区", "169", "09:00", "12:00")


def test_submission_settled_when_loading_message_is_gone():
    assert not submission_settled("正在玩命预约中")
    assert submission_settled("预约成功")
    assert submission_settled("座位已被占用")


def test_requested_times_must_be_present_in_server_options():
    assert requested_times_available(["08:00", "08:30", "12:00"], ["08:00", "12:00"])
    assert not requested_times_available(["12:00", "12:30"], ["08:00", "12:00"])


def test_time_option_normalizes_whitespace_and_colon():
    assert normalize_time_option(" 09:00 ") == "09:00"
    assert normalize_time_option("09：00") == "09:00"


def test_time_values_read_server_options():
    response = {"data": {"startTimes": [{"id": "480", "value": "08:00"}, {"id": "540", "value": "09:00"}]}}
    assert time_values(response, "startTimes") == ["08:00", "09:00"]


def test_end_time_response_matches_selected_start_minutes():
    assert time_to_minutes("15:00") == "900"
    assert end_times_response_matches("https://seatlib.hpu.edu.cn/rest/v2/endTimesForSeat/20008/2026-08-20/900", "15:00")
    assert not end_times_response_matches("https://seatlib.hpu.edu.cn/rest/v2/endTimesForSeat/20008/2026-08-20/480", "15:00")


def test_end_times_request_url_uses_selected_seat_date_and_start():
    assert end_times_request_url(20008, "2026-08-20", "15:00") == "rest/v2/endTimesForSeat/20008/2026-08-20/900"


def test_time_options_preserve_native_server_ids():
    response = {"data": {"startTimes": [{"id": "now", "value": "现在"}, {"id": "540", "value": "09:00"}]}}
    assert time_options(response, "startTimes") == [{"id": "now", "value": "现在"}, {"id": "540", "value": "09:00"}]
    assert time_option_id(response, "startTimes", "09:00") == "540"
    assert time_option_id(response, "startTimes", "现在") == "now"


def test_end_times_request_url_can_use_native_start_id():
    assert end_times_request_url(20008, "2026-08-20", "09:00", start_id="540") == "rest/v2/endTimesForSeat/20008/2026-08-20/540"
    assert end_times_request_url(20008, "2026-08-20", "现在", start_id="now") == "rest/v2/endTimesForSeat/20008/2026-08-20/now"


def test_end_time_response_must_match_selected_start_id_not_just_endpoint():
    initial = "https://seatlib.hpu.edu.cn/rest/v2/endTimesForSeat/20008/2026-08-20/now?id=20008&date=2026-08-20&start=now"
    selected = "https://seatlib.hpu.edu.cn/rest/v2/endTimesForSeat/20008/2026-08-20/570?id=20008&date=2026-08-20&start=570"
    assert not end_time_response_matches_start(initial, "570")
    assert end_time_response_matches_start(selected, "570")
