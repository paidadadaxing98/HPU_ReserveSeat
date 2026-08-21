from dataclasses import dataclass
import asyncio


@dataclass(frozen=True)
class SeatResult:
    success: bool
    room: str = ""
    seat: str = ""
    message: str = ""
    conclusive: bool = True


class DryRunReservation:
    def reserve(self, date, period, start, end) -> SeatResult:
        return SeatResult(True, "演练阅览室", "演练座位", "dry-run；未提交真实预约")

    def cancel(self, date, period) -> SeatResult:
        return SeatResult(True, message="dry-run；未取消真实预约")

    def current_reservations(self, day) -> list[dict]:
        return []


class PlaywrightReservation:
    """Synchronous service boundary for the existing Playwright booking flow."""
    def __init__(self, settings=None, runner=None, current_runner=None):
        self.settings = settings
        self.runner = runner or _default_booking_runner if settings is not None else runner
        self.current_runner = current_runner or _default_current_reservation_runner if settings is not None else current_runner

    def reserve(self, date, period, start, end) -> SeatResult:
        if self.settings is None and self.runner is None:
            return SeatResult(False, message="真实网站适配器缺少账号配置，已安全停止", conclusive=False)
        if self.runner is None:
            return SeatResult(False, message="真实定时预约尚未配置阅览室执行器，已安全停止", conclusive=False)
        try:
            result = asyncio.run(self.runner(self.settings, date, period, start, end))
            return result if isinstance(result, SeatResult) else SeatResult(False, message="真实预约流程未返回有效结果", conclusive=False)
        except Exception as exc:
            return SeatResult(False, message=f"真实预约流程异常：{exc}", conclusive=False)

    def cancel(self, date, period) -> SeatResult:
        return SeatResult(False, message="真实网站适配器尚未校准，请先运行站点校准", conclusive=False)

    def current_reservations(self, day) -> list[dict]:
        if self.settings is None and self.current_runner is None:
            raise RuntimeError("真实网站适配器缺少账号配置，无法读取当前预约")
        if self.current_runner is None:
            raise RuntimeError("真实网站适配器缺少当前预约查询器，已安全停止")
        result = asyncio.run(self.current_runner(self.settings, day))
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise RuntimeError("当前预约查询器未返回有效记录")
        return result


async def _default_booking_runner(settings, day, period, start, end):
    from scripts.preview_reservation import run_scheduled_reservation

    return await run_scheduled_reservation(settings, day, period, start, end)


async def _default_current_reservation_runner(settings, day):
    from scripts.preview_reservation import fetch_scheduled_current_reservations

    return await fetch_scheduled_current_reservations(settings, day)
