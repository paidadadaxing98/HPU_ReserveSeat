from seat_assistant.date_selection import date_option_matches, normalize_date


def test_normalize_date_accepts_iso_and_chinese_date():
    assert normalize_date("2026-08-20") == "2026-08-20"
    assert normalize_date("2026年08月20日") == "2026-08-20"


def test_date_option_matches_only_exact_target():
    assert date_option_matches("2026-08-20", "2026-08-20")
    assert not date_option_matches("2026-08-19", "2026-08-20")
