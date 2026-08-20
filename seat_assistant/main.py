from .config import load_settings
from .reservation import DryRunReservation, PlaywrightReservation
from .service import AssistantService
from .storage import Repository


def build_service():
    settings = load_settings()
    adapter = DryRunReservation() if settings.dry_run else PlaywrightReservation()
    return settings, AssistantService(settings, Repository(settings.db_path), adapter)


if __name__ == "__main__":
    import threading
    import time
    from datetime import datetime, timedelta
    from .api import serve
    from .scheduler import run_once
    settings, service = build_service()
    def scheduler_loop():
        last_run = None
        while True:
            now = datetime.now()
            if now.hour == 19 and now.minute == 30 and last_run != now.date():
                tomorrow = (now + timedelta(days=1)).date().isoformat()
                run_once(service, tomorrow)
                last_run = now.date()
            time.sleep(20)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    print("Seat Assistant 已启动", flush=True)
    print("本机控制页: http://127.0.0.1:8765/?token=你的SEAT_CONTROL_TOKEN", flush=True)
    print("手机请使用这台电脑的局域网IP替换 127.0.0.1，且确保手机与电脑在同一Wi-Fi。", flush=True)
    print("当前模式: " + ("演练模式，不提交真实预约" if settings.dry_run else "真实预约模式"), flush=True)
    print("按 Ctrl+C 停止服务", flush=True)
    serve(service, settings.control_token, host=settings.control_host)
