from datetime import date, datetime
import uuid

from .commands import Command
from .config import Settings
from .domain import build_reservation, parse_hhmm, reservation_start_for_arrival
from .notifications import send_reservation_notification
from .initialization import initialization_skip_message
from .reservation import SeatResult
from .storage import Repository
from .submission import active_reservation_interval, active_reservations_for_day


class AssistantService:
    def __init__(self, settings: Settings, repo: Repository, adapter, notifier=None):
        self.settings, self.repo, self.adapter, self.notifier = settings, repo, adapter, notifier
        self.account_id = settings.account_id

    def run_once(
        self,
        day: str,
        now: datetime | None = None,
        target_period: str | None = None,
    ):
        from .scheduler import run_once
        return run_once(self, day, now=now, target_period=target_period)

    def reserve_period(
        self,
        day: str,
        period_name: str,
        arrival_override: str | None = None,
        quota_day: str | None = None,
        now: datetime | None = None,
    ):
        if getattr(self.settings, "require_initialization", False):
            state = self.repo.initialization_state()
            if state["status"] != "ready":
                return SeatResult(False, message=initialization_skip_message(state))
        if period_name not in self.settings.periods:
            return SeatResult(False, message=f"未知学习时段：{period_name}")
        quota_day = quota_day or date.today().isoformat()
        period = self.settings.periods[period_name]
        existing = self.repo.get_reservation(day, period_name)
        if existing and existing["status"] == "reserved":
            return SeatResult(True, existing["room"], existing["seat"], "已存在预约")
        if existing and existing["status"] == "uncertain":
            return SeatResult(False, existing["room"], existing["seat"], "同一天已有一次预约，但上次结果不明确；已停止重试", conclusive=False)
        now = now or datetime.now()
        live_block = self._live_reservation_block(day, period_name, now)
        if live_block is not None:
            return live_block
        for record in self.repo.reservations(day):
            record_period, status, _, _, room, seat = record
            if record_period != period_name and status in {"reserved", "uncertain"}:
                if status == "uncertain":
                    return SeatResult(False, room, seat, "同一天已有一次预约，但上次结果不明确；已停止重试", conclusive=False)
                if not _reservation_has_ended(day, record[3], now):
                    return SeatResult(False, room, seat, "前一个预约尚未结束，当前账号暂不能预约下一时段", conclusive=True)
        if self.repo.successful_booking_count(quota_day) >= self.settings.daily_success_limit:
            return SeatResult(False, message=f"账号今日成功预约次数已达到 {self.settings.daily_success_limit} 次，跳过本次运行")
        expected = arrival_override or self.repo.default_override(period_name) or self.repo.learned_default(period_name, period.default_arrival)
        try:
            expected_time = parse_hhmm(expected)
            arrival_window = tuple(parse_hhmm(value) for value in period.arrival_window)
            departure = parse_hhmm(period.departure_window[0])
            start = reservation_start_for_arrival(expected_time, arrival_window).strftime("%H:%M")
            end = build_reservation(parse_hhmm(start), departure).end.strftime("%H:%M")
        except (TypeError, ValueError) as exc:
            return SeatResult(False, message=str(exc))
        try:
            result = self.adapter.reserve(day, period_name, start, end)
        except Exception as exc:
            result = SeatResult(False, message=f"预约适配器异常：{exc}", conclusive=False)
        status = "reserved" if result.success else "uncertain" if not result.conclusive else "failed"
        self.repo.save_reservation(day, period_name, status, start, end, result.room, result.seat, result.message)
        if result.success:
            self.repo.record_successful_booking(quota_day, f"{period_name}:{uuid.uuid4().hex}")
        send_reservation_notification(self.notifier, day, period_name, result, start, end)
        return result

    def _live_reservation_block(self, day: str, period_name: str, now: datetime) -> SeatResult | None:
        query = getattr(self.adapter, "current_reservations", None)
        if query is None:
            return None
        try:
            records = query(day)
        except Exception as exc:
            return SeatResult(False, message=f"无法读取‘我的预约’确认当前状态：{exc}；已停止后续预约", conclusive=False)
        for item in active_reservations_for_day(records, day):
            start_minutes, end_minutes = active_reservation_interval(item)
            if end_minutes is None:
                return SeatResult(False, message="当前有效预约缺少结束时间，无法安全判断是否能预约下一时段；已停止", conclusive=False)
            if not _reservation_has_ended(day, _format_minutes(end_minutes), now):
                return SeatResult(False, message="前一个预约尚未结束，当前账号暂不能预约下一时段", conclusive=True)
        return None

    def apply_command(self, command: Command, day: str | None = None):
        day = day or date.today().isoformat()
        if getattr(self.settings, "require_initialization", False) and self.repo.initialization_state()["status"] != "ready":
            return {"ok": False, "message": initialization_skip_message(self.repo.initialization_state())}
        if command.kind == "delay":
            if command.period not in self.settings.periods:
                return {"ok": False, "message": "未识别学习时段。请使用上午、下午或晚上。"}
            period = self.settings.periods[command.period]
            try:
                expected = parse_hhmm(command.at)
            except (TypeError, ValueError):
                return {"ok": False, "message": "时间格式无效，请使用 HH:MM。"}
            try:
                arrival_window = tuple(parse_hhmm(value) for value in period.arrival_window)
                reservation_start_for_arrival(expected, arrival_window)
            except ValueError as exc:
                return {"ok": False, "message": f"{command.at} 无法形成有效预约：{exc}，未修改预约。"}
            old = [row for row in self.repo.reservations(day) if row[0] == command.period]
            if old and old[0][1] == "reserved":
                cancelled = self.adapter.cancel(day, command.period)
                if not cancelled.success:
                    return {"ok": False, "message": f"原预约取消失败或结果不明确：{cancelled.message}，已停止修改，请手动检查网站。"}
                record = self.repo.get_reservation(day, command.period)
                self.repo.save_reservation(day, command.period, "cancelled", record["start"], record["end"], record["room"], record["seat"], "已为推迟重新规划")
            self.repo.event("delay", command.period, command.at)
            result = self.reserve_period(day, command.period, arrival_override=command.at)
            if not result.success:
                return {"ok": False, "message": f"已记录推迟到 {command.at}，但重新预约未成功：{result.message}"}
            return {"ok": True, "message": f"已将{command.period}预约调整为预计 {command.at} 到馆，座位：{result.room} {result.seat}。"}
        if command.kind == "set_default":
            if command.period not in self.settings.periods:
                return {"ok": False, "message": "未识别学习时段。请使用上午、下午或晚上。"}
            try:
                parse_hhmm(command.at)
            except (TypeError, ValueError):
                return {"ok": False, "message": "时间格式无效，请使用 HH:MM。"}
            self.settings.periods[command.period].default_arrival = command.at
            self.repo.set_default(command.period, command.at)
            self.repo.event("default_override", command.period, command.at)
            return {"ok": True, "message": f"已将{command.period}默认到馆时间改为 {command.at}。"}
        if command.kind in {"cancel", "cancel_day"}:
            periods = [command.period] if command.period else list(self.settings.periods)
            failures = []
            for period in periods:
                record = self.repo.get_reservation(day, period)
                if not record or record["status"] != "reserved":
                    continue
                result = self.adapter.cancel(day, period)
                if not result.success:
                    failures.append(f"{period}: {result.message or '结果不明确'}")
                    continue
                self.repo.save_reservation(day, period, "cancelled", record["start"], record["end"], record["room"], record["seat"], "用户取消")
                self.repo.event("cancel", period, "user")
            if failures:
                return {"ok": False, "message": "；".join(failures)}
            return {"ok": True, "message": "已取消指定预约。"}
        if command.kind == "record_arrival":
            if command.period not in self.settings.periods:
                return {"ok": False, "message": "未识别学习时段。请使用上午、下午或晚上。"}
            try:
                parse_hhmm(command.at)
            except (TypeError, ValueError):
                return {"ok": False, "message": "时间格式无效，请使用 HH:MM。"}
            self.repo.event("arrival", command.period, command.at)
            return {"ok": True, "message": f"已记录{command.period}到馆时间 {command.at}。"}
        if command.kind == "ask_delay":
            return {"ok": True, "message": "你预计几点到馆？请回复例如：09:20。"}
        if command.kind == "ask_period":
            return {"ok": True, "message": f"已收到预计时间 {command.at}，请说明时段，例如：上午推迟到 {command.at}。"}
        if command.kind == "status":
            return {"ok": True, "reservations": self.repo.reservations(day)}
        return {"ok": True, "message": "支持：状态、上午推迟、上午推迟到 09:20、取消上午、今天不去了、以后上午默认到馆时间为 09:05。"}


def _clock_minutes(value):
    return value.hour * 60 + value.minute


def _reservation_has_ended(day: str, end: str, now: datetime) -> bool:
    try:
        end_at = datetime.combine(date.fromisoformat(day), parse_hhmm(end))
    except (TypeError, ValueError):
        return False
    return now >= end_at


def _format_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"
