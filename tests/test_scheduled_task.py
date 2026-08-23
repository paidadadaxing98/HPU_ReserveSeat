from datetime import datetime
from types import SimpleNamespace

from scripts import run_scheduled_task as scheduled_module
from scripts.run_scheduled_task import parse_args, scheduled_target


def test_morning_trigger_books_tomorrow_morning_during_previous_evening_window():
    assert scheduled_target("morning", datetime(2026, 8, 21, 22, 0)) == (
        "2026-08-22", "morning"
    )


def test_morning_trigger_books_today_morning_during_daytime_window():
    assert scheduled_target("morning", datetime(2026, 8, 22, 7, 0)) == (
        "2026-08-22", "morning"
    )


def test_morning_trigger_at_1930_books_tomorrow_morning():
    assert scheduled_target("morning", datetime(2026, 8, 22, 19, 30)) == (
        "2026-08-23", "morning"
    )


def test_morning_trigger_at_2230_books_today_morning():
    assert scheduled_target("morning", datetime(2026, 8, 22, 22, 30)) == (
        "2026-08-22", "morning"
    )


def test_all_triggers_are_rejected_outside_daily_booking_window():
    assert scheduled_target("morning", datetime(2026, 8, 22, 6, 59)) is None
    assert scheduled_target("evening", datetime(2026, 8, 22, 23, 30)) is None


def test_afternoon_trigger_books_today_afternoon():
    assert scheduled_target("afternoon", datetime(2026, 8, 21, 12, 30)) == (
        "2026-08-21", "afternoon"
    )


def test_evening_trigger_books_today_evening():
    assert scheduled_target("evening", datetime(2026, 8, 21, 19, 20)) == (
        "2026-08-21", "evening"
    )


def test_period04_trigger_books_today_period04():
    assert scheduled_target("period04", datetime(2026, 8, 21, 10, 5)) == (
        "2026-08-21", "period04"
    )


def test_period05_trigger_books_today_period05():
    assert scheduled_target("period05", datetime(2026, 8, 21, 13, 5)) == (
        "2026-08-21", "period05"
    )


def test_missed_morning_trigger_after_daily_booking_window_is_skipped_safely():
    assert scheduled_target("morning", datetime(2026, 8, 22, 23, 30)) is None


def test_scheduled_task_supports_explicit_dry_run_switch():
    args = parse_args(["--period", "evening", "--dry-run"])

    assert args.period == "evening"
    assert args.dry_run is True


def test_scheduled_task_accepts_remaining_periods():
    args = parse_args(["--period", "period05"])

    assert args.period == "period05"


def test_scheduled_task_defaults_to_small_notifications_only():
    args = parse_args(["--period", "evening"])

    assert args.period == "evening"
    assert getattr(args, "notify_scheduler_summary", False) is False


def test_scheduled_task_can_enable_scheduler_summary_for_debug():
    args = parse_args(["--period", "evening", "--notify-scheduler-summary"])

    assert args.notify_scheduler_summary is True


def test_run_trigger_does_not_start_bot_before_booking(monkeypatch):
    events = []
    build_calls = []

    monkeypatch.setattr(scheduled_module, "_load_dotenv", lambda: None)
    monkeypatch.setattr(
        scheduled_module,
        "build_services",
        lambda force_real, force_dry_run, notify_reservation_results, notify_scheduler_summary: (
            build_calls.append((force_real, force_dry_run, notify_reservation_results, notify_scheduler_summary)) or
            SimpleNamespace(account_interval_seconds=0, wecom_bot_id="bot", wecom_bot_secret="secret"),
            ["service-a"],
        ),
    )
    monkeypatch.setattr(
        scheduled_module,
        "run_accounts_once",
        lambda services, day, interval_seconds, now, target_period, persist_results: events.append(
            ("booking", tuple(services), day, target_period, persist_results)
        ) or {"alice": {"status": "reserved"}},
    )
    monkeypatch.setattr(
        scheduled_module,
        "_start_wecom_bot_if_configured",
        lambda settings: events.append("bot-start") or object(),
    )
    assert scheduled_module.run_trigger("evening", datetime(2026, 8, 22, 19, 20), dry_run=True) == 0
    assert events == [("booking", ("service-a",), "2026-08-22", "evening", False)]
    assert build_calls == [(False, True, True, False)]


def test_run_trigger_does_not_start_bot_even_when_credentials_exist(monkeypatch):
    events = []

    monkeypatch.setattr(scheduled_module, "_load_dotenv", lambda: None)
    monkeypatch.setattr(
        scheduled_module,
        "build_services",
        lambda force_real, force_dry_run, notify_reservation_results, notify_scheduler_summary=False: (
            SimpleNamespace(account_interval_seconds=0, wecom_bot_id="bot", wecom_bot_secret="secret"),
            ["service-a"],
        ),
    )
    monkeypatch.setattr(
        scheduled_module,
        "run_accounts_once",
        lambda services, day, interval_seconds, now, target_period, persist_results: events.append(
            ("booking", tuple(services), day, target_period, persist_results)
        ) or {"alice": {"status": "reserved"}},
    )
    monkeypatch.setattr(
        scheduled_module,
        "_start_wecom_bot_if_configured",
        lambda settings: events.append("bot-start") or object(),
    )

    assert scheduled_module.run_trigger("evening", datetime(2026, 8, 22, 19, 20), dry_run=True) == 0
    assert events == [("booking", ("service-a",), "2026-08-22", "evening", False)]
