"""Run one unattended reservation trigger and exit."""

import argparse
import contextlib
from datetime import datetime, time, timedelta
from pathlib import Path

from seat_assistant.config import _load_dotenv
from seat_assistant.main import build_services
from seat_assistant.scheduler import run_accounts_once


TRIGGER_WINDOWS = {
    "morning": (time(19, 30), time(22, 30)),
    "afternoon": (time(12, 0), time(18, 30)),
    "evening": (time(19, 0), time(22, 0)),
}


def scheduled_target(period: str, now: datetime) -> tuple[str, str] | None:
    """Return the safe booking date for a trigger, or None after its window."""
    if period not in TRIGGER_WINDOWS:
        raise ValueError(f"未知定时预约时段：{period}")
    start, end = TRIGGER_WINDOWS[period]
    current = now.time().replace(second=0, microsecond=0)
    if not start <= current < end:
        return None
    day = now.date() + timedelta(days=1) if period == "morning" else now.date()
    return day.isoformat(), period


def run_trigger(period: str, now: datetime | None = None) -> int:
    _load_dotenv()
    now = now or datetime.now()
    target = scheduled_target(period, now)
    if target is None:
        print(f"定时任务 {period} 当前不在安全预约窗口，跳过。")
        return 0

    day, target_period = target
    base, services = build_services()
    print(f"开始无人值守预约：账号数量={len(services)}，日期={day}，时段={target_period}。")
    results = run_accounts_once(
        services,
        day,
        interval_seconds=base.account_interval_seconds,
        now=now,
        target_period=target_period,
    )
    print(f"无人值守预约结束：{results}")
    return 0


def log_path(now: datetime | None = None) -> Path:
    root = Path(__file__).resolve().parents[1]
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d")
    return logs / f"scheduled-{stamp}.log"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="运行一次无感 Windows 定时预约任务")
    parser.add_argument("--period", choices=tuple(TRIGGER_WINDOWS), required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    path = log_path()
    try:
        with path.open("a", encoding="utf-8") as stream:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                print(f"\n[{datetime.now().isoformat(timespec='seconds')}] 收到定时任务：{args.period}")
                return run_trigger(args.period)
    except Exception as exc:
        with path.open("a", encoding="utf-8") as stream:
            print(f"[{datetime.now().isoformat(timespec='seconds')}] 定时任务异常：{exc}", file=stream)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
