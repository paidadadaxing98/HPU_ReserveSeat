from datetime import datetime
from types import SimpleNamespace

from scripts import run_scheduled_task as scheduled_module
from scripts.run_scheduled_task import parse_args, scheduled_target


def test_morning_trigger_books_tomorrow_morning():
    assert scheduled_target("morning", datetime(2026, 8, 21, 22, 0)) == (
        "2026-08-22", "morning"
    )


def test_afternoon_trigger_books_today_afternoon():
    assert scheduled_target("afternoon", datetime(2026, 8, 21, 12, 30)) == (
        "2026-08-21", "afternoon"
    )


def test_evening_trigger_books_today_evening():
    assert scheduled_target("evening", datetime(2026, 8, 21, 19, 30)) == (
        "2026-08-21", "evening"
    )


def test_missed_morning_trigger_outside_booking_window_is_skipped_safely():
    assert scheduled_target("morning", datetime(2026, 8, 22, 23, 0)) is None


def test_scheduled_task_supports_explicit_dry_run_switch():
    args = parse_args(["--period", "evening", "--dry-run"])

    assert args.period == "evening"
    assert args.dry_run is True


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
    assert scheduled_module.run_trigger("evening", datetime(2026, 8, 22, 20, 0), dry_run=True) == 0
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

    assert scheduled_module.run_trigger("evening", datetime(2026, 8, 22, 20, 0), dry_run=True) == 0
    assert events == [("booking", ("service-a",), "2026-08-22", "evening", False)]
