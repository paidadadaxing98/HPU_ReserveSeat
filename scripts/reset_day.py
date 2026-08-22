"""Reset one account's local reservation execution state for one date."""

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seat_assistant.config import load_account_settings
from seat_assistant.storage import Repository


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="清理指定账号指定日期的本地预约状态，恢复为未执行"
    )
    parser.add_argument("--account", required=True, help="accounts.json 中的账号 ID")
    parser.add_argument("--date", required=True, help="日期，格式 YYYY-MM-DD")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过交互确认；删除当天本地预约记录、成功次数和调度记录",
    )
    return parser.parse_args(argv)


def reset_account_day(account_id: str, day: str, confirmed: bool = False) -> dict[str, int]:
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError("日期格式必须是 YYYY-MM-DD") from exc
    if not confirmed:
        answer = input(
            f"将清理账号 {account_id} 在 {day} 的本地预约状态。"
            "输入 RESET 确认："
        ).strip()
        if answer != "RESET":
            print("未执行清理。")
            return {"reservations": 0, "successful_bookings": 0, "scheduler_runs": 0}
    settings = load_account_settings(account_id)
    result = Repository(str(settings.db_path), account_id).reset_day(day)
    print(
        f"账号 {account_id} 已恢复 {day} 的未执行状态："
        f"预约记录 {result['reservations']} 条，"
        f"成功次数 {result['successful_bookings']} 条，"
        f"调度记录 {result['scheduler_runs']} 条。"
    )
    return result


def main(argv=None) -> int:
    args = parse_args(argv)
    reset_account_day(args.account, args.date, confirmed=args.yes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
