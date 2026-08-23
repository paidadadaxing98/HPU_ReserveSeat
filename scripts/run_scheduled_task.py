"""Run one unattended reservation trigger and exit."""

import argparse
import contextlib
from datetime import datetime, time, timedelta
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seat_assistant.config import _load_dotenv
from seat_assistant.main import build_services
from seat_assistant.scheduler import run_accounts_once

TRIGGER_WINDOWS = {
    "morning": (time(7, 0), time(23, 30)),
    "afternoon": (time(7, 0), time(23, 30)),
    "evening": (time(7, 0), time(23, 30)),
    "period04": (time(7, 0), time(23, 30)),
    "period05": (time(7, 0), time(23, 30)),
}

def scheduled_target(period: str, now: datetime) -> tuple[str, str] | None:
    """Return the safe booking date for a trigger, or None after its window."""
    if period not in TRIGGER_WINDOWS:
        raise ValueError(f"未知定时预约时段：{period}")
    start, end = TRIGGER_WINDOWS[period]
    current = now.time().replace(second=0, microsecond=0)
    if not start <= current < end:
        return None
    morning_next_day = (
        period == "morning"
        and time(19, 30) <= current < time(22, 30)
    )
    day = now.date() + timedelta(days=1) if morning_next_day else now.date()
    return day.isoformat(), period


def run_trigger(
    period: str,
    now: datetime | None = None,
    dry_run: bool = False,
    notify_scheduler_summary: bool = False,
) -> int:
    _load_dotenv()
    now = now or datetime.now()
    target = scheduled_target(period, now)
    if target is None:
        print(f"定时任务 {period} 当前不在安全预约窗口，跳过。")
        return 0

    day, target_period = target
    # Scheduled execution is an explicit real-booking path. The normal
    # SEAT_DRY_RUN=true default remains available for manual/service testing.
    base, services = build_services(
        force_real=not dry_run,
        force_dry_run=dry_run,
        notify_reservation_results=True,
        notify_scheduler_summary=notify_scheduler_summary,
    )
    print(f"开始无人值守预约：账号数量={len(services)}，日期={day}，时段={target_period}。")
    results = run_accounts_once(
        services,
        day,
        interval_seconds=base.account_interval_seconds,
        now=now,
        target_period=target_period,
        persist_results=not dry_run,
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
    parser.add_argument("--dry-run", action="store_true", help="演练模式：不提交真实预约")
    parser.add_argument(
        "--notify-scheduler-summary",
        action="store_true",
        help="调试模式：额外发送账号定时任务汇总通知",
    )
    return parser.parse_args(argv)


def _sleep(seconds: int | float) -> None:
    import time

    time.sleep(seconds)


def _start_wecom_bot_if_configured(settings):
    if not getattr(settings, "wecom_bot_id", "") or not getattr(settings, "wecom_bot_secret", ""):
        return None
    root = Path(__file__).resolve().parents[1]
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "scripts.run_wecom_bot"],
            cwd=str(root),
        )
    except Exception as exc:
        print(f"企业微信机器人启动失败：{exc}")
        return None


def _stop_wecom_bot(process) -> None:
    if process is None:
        return
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main(argv=None) -> int:
    args = parse_args(argv)
    path = log_path()
    try:
        with path.open("a", encoding="utf-8") as stream:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                print(f"\n[{datetime.now().isoformat(timespec='seconds')}] 收到定时任务：{args.period}")
                return run_trigger(
                    args.period,
                    dry_run=args.dry_run,
                    notify_scheduler_summary=args.notify_scheduler_summary,
                )
    except Exception as exc:
        with path.open("a", encoding="utf-8") as stream:
            print(f"[{datetime.now().isoformat(timespec='seconds')}] 定时任务异常：{exc}", file=stream)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
