from dataclasses import dataclass


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


class PlaywrightReservation:
    """Placeholder boundary. Real selectors must be captured after local login."""
    def reserve(self, date, period, start, end) -> SeatResult:
        return SeatResult(False, message="真实网站适配器尚未校准，请先运行站点校准", conclusive=False)

    def cancel(self, date, period) -> SeatResult:
        return SeatResult(False, message="真实网站适配器尚未校准，请先运行站点校准", conclusive=False)
