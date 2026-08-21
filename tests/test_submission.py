from seat_assistant.submission import active_reservations_for_day, confirmation_required, day_reservations, end_time_response_matches_start, end_times_request_url, end_times_response_matches, find_matching_reservation, find_reservation_by_day_and_time, find_similar_reservation, history_page_records, local_reservation_blocks_retry, normalize_time_option, reservation_matches, requested_times_available, submission_settled, time_option_id, time_options, time_to_minutes, time_values


def test_submission_only_prompts_for_explicit_debug_confirmation():
    assert confirmation_required(False, False, "SUBMIT")
    assert not confirmation_required(True, False, "")
    assert confirmation_required(True, True, "submit")
    assert not confirmation_required(True, True, "SUBMIT")


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


def test_find_similar_reservation_accepts_75_percent_time_overlap():
    reservations = [{
        "date": "2026-08-20",
        "roomName": "4层计算机类借阅区",
        "startTime": "15:30",
        "endTime": "17:30",
        "seatNumber": "169",
        "status": "RESERVE",
    }]

    matched = find_similar_reservation(
        reservations,
        "2026-08-20",
        "4层计算机类借阅区",
        "15:00",
        "17:00",
    )

    assert matched == reservations[0]


def test_find_similar_reservation_rejects_low_overlap_or_other_room():
    reservation = {
        "date": "2026-08-20",
        "roomName": "4层计算机类借阅区",
        "startTime": "16:00",
        "endTime": "18:00",
    }
    assert find_similar_reservation([reservation], "2026-08-20", "4层计算机类借阅区", "15:00", "17:00") is None

    other_room = {**reservation, "roomName": "3层社会科学阅览区", "startTime": "15:00", "endTime": "17:00"}
    assert find_similar_reservation([other_room], "2026-08-20", "4层计算机类借阅区", "15:00", "17:00") is None


def test_find_similar_reservation_ignores_unparseable_or_other_date_records():
    reservations = [
        {"date": "2026-08-19", "startTime": "15:00", "endTime": "17:00"},
        {"date": "2026-08-20", "roomName": "4层计算机类借阅区", "startTime": "待定", "endTime": "待定"},
    ]

    assert find_similar_reservation(reservations, "2026-08-20", "4层计算机类借阅区", "15:00", "17:00") is None


def test_find_similar_reservation_reads_live_api_fields_and_combined_location():
    reservation = {
        "onDate": "2026-08-20",
        "location": "南校区第二图书馆4层4层计算机类 借阅区，座位号169",
        "begin": "15:00",
        "end": "17:00",
        "status": "RESERVE",
    }

    assert find_similar_reservation(
        [reservation],
        "2026-08-20",
        "4层计算机类借阅区",
        "15:00",
        "17:00",
    ) == reservation


def test_find_similar_reservation_ignores_cancelled_or_expired_records():
    base = {
        "onDate": "2026-08-20",
        "location": "南校区第二图书馆4层4层计算机类借阅区，座位号169",
        "begin": "15:00",
        "end": "17:00",
    }

    for status in ("CANCEL", "CANCELLED", "EXPIRED", "COMPLETED", "RELEASED"):
        assert find_similar_reservation(
            [{**base, "status": status}],
            "2026-08-20",
            "4层计算机类借阅区",
            "15:00",
            "17:00",
        ) is None


def test_find_similar_reservation_requires_an_explicit_active_status():
    reservation = {
        "onDate": "2026-08-20",
        "location": "南校区第二图书馆4层4层计算机类借阅区，座位号169",
        "begin": "15:00",
        "end": "17:00",
    }

    assert find_similar_reservation(
        [reservation],
        "2026-08-20",
        "4层计算机类借阅区",
        "15:00",
        "17:00",
    ) is None


def test_find_similar_reservation_reads_history_stat_and_loc_fields():
    reservation = {
        "date": "2026-08-20",
        "loc": "南校区第二图书馆4层4层计算机类借阅区，座位号169",
        "begin": "15:00",
        "end": "17:00",
        "stat": "RESERVE",
    }

    assert find_similar_reservation(
        [reservation],
        "2026-08-20",
        "4层计算机类借阅区",
        "15:00",
        "17:00",
    ) == reservation


def test_find_similar_reservation_ignores_history_records_not_reserved():
    reservation = {
        "date": "2026-08-20",
        "loc": "南校区第二图书馆4层4层计算机类借阅区，座位号169",
        "begin": "15:00",
        "end": "17:00",
        "stat": "CANCEL",
    }

    assert find_similar_reservation(
        [reservation],
        "2026-08-20",
        "4层计算机类借阅区",
        "15:00",
        "17:00",
    ) is None


def test_find_matching_reservation_verifies_history_record_and_seat():
    reservation = {
        "date": "2026-08-20",
        "loc": "南校区第二图书馆4层4层计算机类借阅区，座位号169",
        "begin": "15:00",
        "end": "17:00",
        "stat": "RESERVE",
    }

    assert find_matching_reservation(
        [reservation],
        "2026-08-20",
        "4层计算机类借阅区",
        "169",
        "15:00",
        "17:00",
    ) == reservation
    assert find_matching_reservation(
        [reservation],
        "2026-08-20",
        "4层计算机类借阅区",
        "168",
        "15:00",
        "17:00",
    ) is None


def test_find_matching_reservation_excludes_pre_submit_snapshot_record():
    reservation = {
        "id": "old",
        "date": "2026-08-20",
        "loc": "4层计算机类借阅区，座位号169",
        "begin": "09:00",
        "end": "12:00",
        "stat": "RESERVE",
    }

    assert find_matching_reservation(
        [reservation], "2026-08-20", "4层计算机类借阅区", "169", "09:00", "12:00", excluded=[reservation]
    ) is None


def test_find_reservation_by_day_and_time_allows_missing_location_when_unique():
    reservation = {
        "date": "2026-08-20",
        "begin": "09:00",
        "end": "12:00",
        "stat": "RESERVE",
    }

    assert find_reservation_by_day_and_time(
        [reservation], "2026-08-20", "09:00", "12:00"
    ) == reservation


def test_find_reservation_by_day_and_time_rejects_ambiguous_active_records():
    records = [
        {"date": "2026-08-20", "begin": "09:00", "end": "12:00", "stat": "RESERVE"},
        {"date": "2026-08-20", "begin": "09:00", "end": "12:00", "stat": "RESERVE"},
    ]

    assert find_reservation_by_day_and_time(records, "2026-08-20", "09:00", "12:00") is None


def test_find_reservation_by_day_and_time_rejects_full_record_when_location_does_not_match():
    records = [
        {"date": "2026-08-20", "begin": "09:00", "end": "12:00", "stat": "RESERVE"},
        {
            "date": "2026-08-20",
            "begin": "09:00",
            "end": "12:00",
            "loc": "其他阅览室，座位号001",
            "stat": "RESERVE",
        },
    ]

    assert find_reservation_by_day_and_time(records, "2026-08-20", "09:00", "12:00") is None


def test_find_reservation_by_day_and_time_excludes_submission_snapshot_records():
    old = {"id": "old", "date": "2026-08-20", "begin": "09:00", "end": "12:00", "stat": "RESERVE"}
    new = {"id": "new", "date": "2026-08-20", "begin": "09:00", "end": "12:00", "stat": "RESERVE"}

    assert find_reservation_by_day_and_time(
        [old, new], "2026-08-20", "09:00", "12:00", excluded=[old]
    ) == new


def test_local_reservation_blocks_retry_only_for_nonterminal_submission_states():
    assert local_reservation_blocks_retry({"status": "reserved"})
    assert local_reservation_blocks_retry({"status": "pending"})
    assert local_reservation_blocks_retry({"status": "uncertain"})
    assert not local_reservation_blocks_retry({"status": "failed"})
    assert not local_reservation_blocks_retry({"status": "cancelled"})
    assert not local_reservation_blocks_retry(None)


def test_active_reservations_for_day_does_not_require_time_overlap():
    reservations = [
        {
            "date": "2026-08-20",
            "loc": "南校区第二图书馆4层4层计算机类借阅区，座位号169",
            "begin": "20:00",
            "end": "21:00",
            "stat": "RESERVE",
        },
        {
            "date": "2026-08-20",
            "loc": "南校区第二图书馆4层4层计算机类借阅区，座位号168",
            "begin": "15:00",
            "end": "17:00",
            "stat": "CANCEL",
        },
    ]

    assert active_reservations_for_day(reservations, "2026-08-20") == [reservations[0]]


def test_day_reservations_keeps_all_statuses_for_reporting():
    reservations = [
        {"date": "2026-08-20", "stat": "RESERVE"},
        {"date": "2026-08-20", "stat": "CANCEL"},
        {"date": "2026-08-21", "stat": "RESERVE"},
    ]

    assert day_reservations(reservations, "2026-08-20") == reservations[:2]


def test_history_stat_only_reserve_is_active():
    base = {
        "date": "2026-08-20",
        "loc": "阅览室，座位号169",
        "begin": "20:00",
        "end": "21:00",
    }

    assert len(active_reservations_for_day([{**base, "stat": "RESERVE"}], "2026-08-20")) == 1
    assert active_reservations_for_day([{**base, "stat": "AWAY"}], "2026-08-20") == []
    assert active_reservations_for_day([{**base, "stat": "CHECK_IN"}], "2026-08-20") == []
    assert active_reservations_for_day([{**base, "stat": "IN_USE"}], "2026-08-20") == []
    assert active_reservations_for_day([{**base, "stat": "COMPLETE"}], "2026-08-20") == []


def test_history_page_records_unwraps_nested_data_records_and_total_count():
    record = {
        "date": "2026-08-20",
        "begin": "20:00",
        "end": "21:00",
        "loc": "4层计算机类借阅区，座位号169",
        "stat": "RESERVE",
    }

    records, total = history_page_records({
        "code": 0,
        "data": {"data": {"records": [record]}, "totalCount": 1},
    })

    assert records == [record]
    assert total == 1


def test_history_page_records_accepts_a_single_record_object():
    record = {
        "date": "2026-08-20",
        "begin": "20:00",
        "end": "21:00",
        "loc": "4层计算机类借阅区，座位号169",
        "stat": "RESERVE",
    }

    records, total = history_page_records({"code": 0, "data": record})

    assert records == [record]
    assert total is None
