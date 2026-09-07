"""Print the computed Windows schedule for one dynamic monitor period."""

import argparse
import json
import sys

from seat_assistant.config import _load_dotenv, load_accounts, load_settings
from seat_assistant.dynamic_schedule import aggregate_period_schedule, period_schedule
from seat_assistant.storage import Repository


PERIODS = ("morning", "afternoon", "evening", "period04", "period05")


def calculate_schedule(period_name: str) -> dict:
    _load_dotenv()
    settings = load_settings()
    accounts = load_accounts()
    enabled_names = {
        name
        for account in accounts
        for name, period in account.periods.items()
        if getattr(period, "enabled", True)
    }
    if not settings.dynamic_compensation_enabled or len(enabled_names) > settings.dynamic_max_periods:
        return {
            "enabled": False,
            "period": period_name,
            "message": "启用时段超过动态补偿上限，全部使用静态预约",
        }

    schedules = []
    for account in accounts:
        period = account.periods.get(period_name)
        if period is None or not getattr(period, "enabled", True):
            continue
        repository = Repository(str(account.db_path), account.id)
        expected = repository.default_override(period_name) or repository.learned_default(
            period_name,
            period.default_arrival,
        )
        # A default may carry an explicit end ("HH:MM-HH:MM"); the dynamic
        # window is anchored on the arrival-derived start only.
        expected = str(expected).partition("-")[0].strip()
        schedule = period_schedule(
            period,
            settings.dynamic_before_minutes,
            settings.dynamic_after_minutes,
            arrival_override=expected,
        )
        if schedule is not None:
            schedules.append(schedule)

    schedule = aggregate_period_schedule(period_name, schedules)
    if schedule is None:
        return {"enabled": False, "period": period_name, "message": "该时段未启用"}
    return {
        "enabled": True,
        "period": schedule.period,
        "start": schedule.start,
        "end": schedule.end,
        "duration_minutes": schedule.duration_minutes,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="计算动态监控计划任务时间")
    parser.add_argument("--period", choices=PERIODS, required=True)
    parser.add_argument("--json", action="store_true", help="保留 JSON 输出选项")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        print(json.dumps(calculate_schedule(args.period), ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        print(f"动态任务时间计算失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
