from .config import load_account_settings, load_accounts, load_settings
from .notifications import WeComNotifier
from .reservation import DryRunReservation, PlaywrightReservation
from .service import AssistantService
from .storage import Repository


def build_service(account_id=None, force_real: bool = False, notify_reservation_results: bool = True):
    """Build one isolated service, optionally selecting an account by id."""
    settings = load_account_settings(account_id)
    if force_real:
        settings.dry_run = False
    settings.notify_reservation_results = notify_reservation_results
    adapter = DryRunReservation() if settings.dry_run else PlaywrightReservation(settings)
    notifier = WeComNotifier(settings.wecom_webhook)
    return settings, AssistantService(settings, Repository(settings.db_path, settings.account_id), adapter, notifier)


def build_services(
    force_real: bool = False,
    force_dry_run: bool = False,
    notify_reservation_results: bool = True,
):
    """Build one isolated service per configured account."""
    base = load_settings()
    if force_real and force_dry_run:
        raise ValueError("force_real 和 force_dry_run 不能同时启用")
    if force_real:
        base.dry_run = False
    elif force_dry_run:
        base.dry_run = True
    services = []
    for account in load_accounts():
        settings = load_account_settings(account.id)
        if force_real:
            settings.dry_run = False
        elif force_dry_run:
            settings.dry_run = True
        settings.notify_reservation_results = notify_reservation_results
        adapter = DryRunReservation() if settings.dry_run else PlaywrightReservation(settings)
        notifier = WeComNotifier(settings.wecom_webhook)
        services.append(AssistantService(settings, Repository(settings.db_path, settings.account_id), adapter, notifier))
    return base, services


if __name__ == "__main__":
    import threading
    import time
    from datetime import datetime, timedelta
    from .api import serve
    from .scheduler import run_accounts_once
    settings, services = build_services()
    service = services[0]
    def scheduler_loop():
        last_run = None
        while True:
            now = datetime.now()
            if now.hour == 19 and now.minute == 30 and last_run != now.date():
                tomorrow = (now + timedelta(days=1)).date().isoformat()
                run_accounts_once(services, tomorrow, settings.account_interval_seconds)
                last_run = now.date()
            time.sleep(20)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    print("Seat Assistant 已启动", flush=True)
    print("本机控制页: http://127.0.0.1:8765/?token=你的SEAT_CONTROL_TOKEN", flush=True)
    print("手机请使用这台电脑的局域网IP替换 127.0.0.1，且确保手机与电脑在同一Wi-Fi。", flush=True)
    print("当前模式: " + ("演练模式，不提交真实预约" if settings.dry_run else "真实预约模式"), flush=True)
    print("按 Ctrl+C 停止服务", flush=True)
    serve(service, settings.control_token, host=settings.control_host)
