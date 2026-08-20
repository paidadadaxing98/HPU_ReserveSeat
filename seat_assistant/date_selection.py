from datetime import datetime


def normalize_date(value: str) -> str:
    value = value.strip().replace("年", "-").replace("月", "-").replace("日", "")
    return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")


def date_option_matches(option_text: str, target_date: str) -> bool:
    try:
        return normalize_date(option_text) == normalize_date(target_date)
    except ValueError:
        return False
