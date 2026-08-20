from datetime import datetime, timedelta


def next_booking_time(now: datetime) -> datetime:
    target = now.replace(hour=19, minute=30, second=0, microsecond=0)
    return target if now <= target else target + timedelta(days=1)


def run_once(service, day: str):
    previous = service.repo.scheduler_run(day)
    if previous and previous["status"] == "completed":
        return previous["summary"]

    results = {}
    reserved_count = 0
    stop_after_result = False
    for period in service.settings.periods:
        if stop_after_result or reserved_count >= service.settings.max_reservations_per_run:
            results[period] = {
                "status": "skipped",
                "success": False,
                "message": "本次运行已达到每日预约数量限制",
                "room": "",
                "seat": "",
            }
            continue
        result = service.reserve_period(day, period)
        results[period] = {
            "status": "reserved" if result.success else "uncertain" if not result.conclusive else "failed",
            "success": result.success,
            "message": result.message,
            "room": result.room,
            "seat": result.seat,
        }
        if result.success:
            reserved_count += 1
            stop_after_result = True
        elif not result.conclusive:
            stop_after_result = True
    service.repo.save_scheduler_run(day, "completed", results)
    return results
