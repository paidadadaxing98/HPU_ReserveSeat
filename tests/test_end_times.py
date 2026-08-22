from seat_assistant.end_times import parse_native_end_times


def test_native_end_times_keep_server_options_and_accept_requested_end():
    result = parse_native_end_times(
        "https://seatlib.hpu.edu.cn/rest/v2/endTimesForSeat/14090/2026-08-19/now",
        {
            "code": "0",
            "message": "",
            "data": {"endTimes": [{"id": "1110", "value": "18:30"}, {"id": "1320", "value": "22:00"}]},
        },
    )
    assert result.ok
    assert result.options == ("18:30", "22:00")
    assert result.supports("22:00")


def test_native_end_times_unwraps_nested_end_times_payload():
    result = parse_native_end_times(
        "https://seatlib.hpu.edu.cn/rest/v2/endTimesForSeat/14090/2026-08-19/900",
        {
            "code": 0,
            "message": "",
            "data": {
                "data": {
                    "endTimes": [
                        {"id": "1110", "value": "18:30"},
                        {"id": "1320", "value": "22:00"},
                    ]
                }
            },
        },
    )

    assert result.ok
    assert result.options == ("18:30", "22:00")
    assert result.supports("18:30")


def test_native_end_times_reject_failed_response_even_if_body_has_no_options():
    result = parse_native_end_times(
        "https://seatlib.hpu.edu.cn/rest/v2/endTimesForSeat/20008/2026-08-20/540",
        {"code": 401, "message": "unauthorized", "data": {"endTimes": []}},
    )
    assert not result.ok
    assert not result.supports("12:00")
