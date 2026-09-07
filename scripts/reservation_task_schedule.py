"""Print the computed unattended reservation task schedule."""

import argparse
import json
import sys

from seat_assistant.config import _load_dotenv, load_accounts
from seat_assistant.reservation_task_schedule import BOOKING_PERIODS, booking_task_schedule


def calculate_schedule(
    period_name: str,
    repeat_minutes: int = 10,
    fallback_start: str | None = None,
    fallback_duration_minutes: int = 20,
) -> dict:
    _load_dotenv()
    accounts = load_accounts()
    schedule = booking_task_schedule(
        period_name,
        accounts,
        repeat_minutes=repeat_minutes,
        fallback_start=fallback_start,
        fallback_duration_minutes=fallback_duration_minutes,
    )
    if schedule is None:
        return {
            "enabled": False,
            "period": period_name,
            "triggers": [],
            "message": "该时段没有启用账号",
        }
    return {
        "enabled": True,
        "period": schedule.period,
        "triggers": list(schedule.triggers),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="计算无人值守预约计划任务时间")
    parser.add_argument("--period", choices=BOOKING_PERIODS, required=True)
    parser.add_argument("--repeat-minutes", type=int, default=10)
    parser.add_argument("--fallback-start", help="第4、5段沿用的备用起点")
    parser.add_argument("--fallback-duration-minutes", type=int, default=20)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        print(json.dumps(calculate_schedule(
            args.period,
            repeat_minutes=args.repeat_minutes,
            fallback_start=args.fallback_start,
            fallback_duration_minutes=args.fallback_duration_minutes,
        ), ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        print(f"预约任务时间计算失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
