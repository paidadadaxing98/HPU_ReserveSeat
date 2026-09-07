from datetime import date, datetime
import re
import uuid

from .commands import Command
from .config import Settings
from .domain import build_reservation, build_reservation_for_arrival, parse_hhmm, reservation_start_for_arrival
from .dynamic_compensation import next_half_hour_start
from .notifications import send_reservation_notification
from .initialization import initialization_skip_message
from .reservation import SeatResult
from .runtime_logging import compact_message
from .storage import Repository
from .submission import (
    active_reservation_interval,
    active_reservations_for_day,
    day_reservations,
    find_cancelable_reservation,
    find_confirmed_reservation,
    find_matching_reservation,
    find_reservation_record,
    reservation_state,
    validate_half_hour_time,
)


class AssistantService:
    def __init__(self, settings: Settings, repo: Repository, adapter, notifier=None):
        self.settings, self.repo, self.adapter, self.notifier = settings, repo, adapter, notifier
        self.account_id = settings.account_id

    def run_once(
        self,
        day: str,
        now: datetime | None = None,
        target_period: str | None = None,
        persist_results: bool = True,
    ):
        from .scheduler import run_once
        return run_once(
            self,
            day,
            now=now,
            target_period=target_period,
            persist_results=persist_results,
        )

    def reserve_period(
        self,
        day: str,
        period_name: str,
        arrival_override: str | None = None,
        quota_day: str | None = None,
        now: datetime | None = None,
        persist_results: bool = True,
        interval_override: tuple[str, str] | None = None,
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
        if (
            existing
            and existing["status"] == "failed"
            and existing.get("start")
            and callable(getattr(self.adapter, "current_reservations", None))
            and not getattr(self.adapter, "is_dry_run", False)
        ):
            # A previous trigger may have booked this slot while its own
            # verification could not see it yet. Adopt the live booking
            # instead of failing again and never notifying.
            try:
                records = self.adapter.current_reservations(day)
            except Exception:
                records = None
            if isinstance(records, list):
                live = [
                    item for item in active_reservations_for_day(records or [], day)
                    if _same_local_interval(item, existing)
                ]
                if len(live) == 1:
                    message = "已在‘我的预约’确认预约成功（此前核验未同步）"
                    room = _remote_room(live[0]) or existing.get("room", "")
                    seat = _remote_seat(live[0]) or existing.get("seat", "")
                    self.repo.save_reservation(
                        day, period_name, "reserved",
                        existing["start"], existing["end"], room, seat, message,
                    )
                    self._notify_dynamic_result(
                        day, period_name,
                        SeatResult(True, room, seat, message),
                        existing["start"], existing["end"],
                    )
                    return SeatResult(True, room, seat, "已存在预约")
        if existing and existing["status"] == "reserved":
            if getattr(self.adapter, "is_dry_run", False):
                return SeatResult(True, existing["room"], existing["seat"], "已存在预约")
            if not callable(getattr(self.adapter, "current_reservations", None)):
                return SeatResult(True, existing["room"], existing["seat"], "已存在预约")
            presence = self._reservation_presence(day, period_name)
            if presence is True:
                return SeatResult(True, existing["room"], existing["seat"], "已存在预约")
            if presence is None:
                resolved = self._resolve_inconclusive_result(
                    day,
                    existing.get("start", ""),
                    existing.get("end", ""),
                    SeatResult(
                        False,
                        existing.get("room", ""),
                        existing.get("seat", ""),
                        "本地已有预约，但远端无法唯一确认",
                        conclusive=False,
                    ),
                )
                if resolved.success:
                    return SeatResult(True, resolved.room, resolved.seat, "已存在预约")
                # Fresh-window queries proved the booking is gone; the local
                # row is stale and must not block a new submission.
                self.repo.save_reservation(
                    day,
                    period_name,
                    "failed",
                    existing.get("start", ""),
                    existing.get("end", ""),
                    existing.get("room", ""),
                    existing.get("seat", ""),
                    "已在‘我的预约’核验远端无该预约，本地记录过期，准备重新预约",
                )
        if existing and existing["status"] in {"pending", "uncertain"}:
            reconciliation, detail = self._reconcile_saved_reservation(day, existing)
            if reconciliation == "recovered":
                return detail
            if reconciliation == "hold":
                return detail
            # The remote query completed and proved that the previous
            # submission is no longer active.  Mark it retryable before the
            # browser flow reads the local record again.
            self.repo.save_reservation(
                day,
                period_name,
                "failed",
                existing.get("start", ""),
                existing.get("end", ""),
                existing.get("room", ""),
                existing.get("seat", ""),
                "上次提交已确认远端无有效预约，准备重试",
            )
        now = now or datetime.now()
        live_block = self._live_reservation_block(day, period_name, now)
        if live_block is not None:
            return live_block
        for record in self.repo.reservations(day):
            record_period, status, _, _, room, seat = record
            if record_period != period_name and status in {"reserved", "uncertain"}:
                if status == "uncertain":
                    # Uncertainty is scoped to the same period. A later period
                    # may still be booked because no confirmed overlap exists.
                    continue
                if not _reservation_has_ended(day, record[3], now):
                    return SeatResult(False, room, seat, "前一个预约尚未结束，当前账号暂不能预约下一时段", conclusive=True)
        if self.repo.successful_booking_count(quota_day) >= self.settings.daily_success_limit:
            return SeatResult(False, message=f"账号今日成功预约次数已达到 {self.settings.daily_success_limit} 次，跳过本次运行")
        expected = arrival_override or self.repo.default_override(period_name) or self.repo.learned_default(period_name, period.default_arrival)
        default_end = None
        expected = str(expected or "").strip()
        if "-" in expected:
            # A default may carry an explicit end time: "HH:MM-HH:MM".
            expected, default_end = (part.strip() for part in expected.split("-", 1))
        try:
            if interval_override is not None:
                # Explicit interval (bot delay command): book the requested
                # half-hour slot exactly instead of mapping an arrival time.
                start = validate_half_hour_time(interval_override[0])
                end = validate_half_hour_time(interval_override[1])
                if _clock_minutes(start) >= _clock_minutes(end):
                    raise ValueError("预约开始时间必须早于结束时间")
                max_minutes = int(getattr(self.settings, "reservation_max_hours", 4)) * 60
                if _clock_minutes(end) - _clock_minutes(start) > max_minutes:
                    raise ValueError(f"预约时长最多 {max_minutes // 60} 小时")
            else:
                expected_time = parse_hhmm(expected)
                arrival_window = tuple(parse_hhmm(value) for value in period.arrival_window)
                departure = parse_hhmm(default_end or period.departure_window[0])
                reservation = build_reservation_for_arrival(expected_time, arrival_window, departure)
                if default_end:
                    max_minutes = int(getattr(self.settings, "reservation_max_hours", 4)) * 60
                    if _clock_minutes(reservation.end) - _clock_minutes(reservation.start) > max_minutes:
                        raise ValueError(f"预约时长最多 {max_minutes // 60} 小时")
                start = reservation.start.strftime("%H:%M")
                end = reservation.end.strftime("%H:%M")
        except (TypeError, ValueError) as exc:
            return SeatResult(False, message=str(exc))
        try:
            result = self.adapter.reserve(day, period_name, start, end)
        except Exception as exc:
            result = SeatResult(False, message=f"预约适配器异常：{exc}", conclusive=False)
        if not result.success and not result.conclusive:
            result = self._resolve_inconclusive_result(day, start, end, result)
        status = (
            "reserved"
            if result.success
            else "pending"
            if not result.conclusive and _is_pending_submission_message(result.message)
            else "uncertain"
            if not result.conclusive
            else "failed"
        )
        if persist_results:
            self.repo.save_reservation(day, period_name, status, start, end, result.room, result.seat, result.message)
        if result.success and persist_results:
            self.repo.record_successful_booking(quota_day, f"{period_name}:{uuid.uuid4().hex}")
        if getattr(self.settings, "notify_reservation_results", True):
            previous = existing or {}
            unchanged_failure = (
                not result.success
                and previous.get("status") == status
                and str(previous.get("message") or "") == str(result.message or "")
            )
            # A retried period that fails the exact same way must not spam a
            # notification on every scheduled trigger.
            if not unchanged_failure:
                account_label = (self.settings.wecom_aliases[0] if getattr(self.settings, "wecom_aliases", ()) else self.account_id)
                send_reservation_notification(self.notifier, day, period_name, result, start, end, account_label)
        return result

    def reconcile_existing_submission(self, day: str, period_name: str):
        """Resolve a locally pending or uncertain submission before scheduling."""
        record = self.repo.get_reservation(day, period_name)
        if not record or record.get("status") not in {"pending", "uncertain"}:
            return None
        return self._reconcile_saved_reservation(day, record)

    def dynamic_reschedule_period(
        self,
        day: str,
        period_name: str,
        now: datetime,
        operation_key: str,
        quota_day: str | None = None,
        preferred_interval: tuple[str, str] | None = None,
    ) -> SeatResult:
        """Cancel the current booking and replace it at an explicit half-hour node.

        ``preferred_interval`` retries the exact saved interval of an earlier
        inconclusive submit while it is still in the future; only after that
        time has passed does the booking move to the next half-hour node.
        """
        quota_day = quota_day or day
        record = self.repo.get_reservation(day, period_name)
        start = None
        end = None
        if preferred_interval:
            try:
                preferred_start = parse_hhmm(preferred_interval[0])
                preferred_end = parse_hhmm(preferred_interval[1])
                start_at = datetime.combine(date.fromisoformat(day), preferred_start)
            except (TypeError, ValueError):
                start_at = None
            if start_at is not None and start_at > now:
                start = preferred_start.strftime("%H:%M")
                end = preferred_end.strftime("%H:%M")
        if start is None:
            start = next_half_hour_start(now)
            end = _current_reservation_end(now, record.get("end") if record else None)
        try:
            if _clock_minutes(start) >= _clock_minutes(end):
                return SeatResult(False, message=f"动态预约时间无效：{start}-{end}", conclusive=True)
        except (TypeError, ValueError):
            return SeatResult(False, message=f"动态预约时间无效：{start}-{end}", conclusive=True)
        cancelled = self._cancel_dynamic_reservation(day, period_name, operation_key)
        if not cancelled.success:
            return cancelled
        try:
            result = self.adapter.reserve(day, period_name, start, end)
        except Exception as exc:
            result = SeatResult(False, message=f"动态预约适配器异常：{exc}", conclusive=False)
        if not result.success and not result.conclusive:
            result = self._resolve_inconclusive_result(day, start, end, result)
        status = "reserved" if result.success else "uncertain" if not result.conclusive else "failed"
        self.repo.save_reservation(
            day,
            period_name,
            status,
            start,
            end,
            result.room,
            result.seat,
            result.message or ("动态预约成功" if result.success else "动态预约失败"),
        )
        if result.success:
            self.repo.record_successful_booking(quota_day, f"{period_name}:dynamic:{operation_key}")
        self._notify_dynamic_result(day, period_name, result, start, end)
        return result

    def dynamic_cancel_period(
        self,
        day: str,
        period_name: str,
        operation_key: str,
        reason: str,
    ) -> SeatResult:
        """Cancel an unentered booking at the hard dynamic-window cutoff."""
        result = self._cancel_dynamic_reservation(day, period_name, operation_key)
        if result.success:
            if "无需取消" in str(result.message or ""):
                # The fresh query already proved nothing is booked remotely.
                # Retire the stale local row instead of recording a cancel.
                current = self.repo.get_reservation(day, period_name)
                if current is not None and current.get("status") in {"reserved", "pending", "uncertain"}:
                    self.repo.save_reservation(
                        day,
                        period_name,
                        "cancelled",
                        current.get("start", ""),
                        current.get("end", ""),
                        current.get("room", ""),
                        current.get("seat", ""),
                        reason,
                    )
                return result
            cancellation = self.repo.dynamic_cancellation(day, operation_key)
            current = self.repo.get_reservation(day, period_name)
            target = _dynamic_cancellation_target(
                self.repo,
                day,
                period_name,
                cancellation,
                current,
            )
            if target is None:
                return SeatResult(False, message="取消记录缺少原预约信息，已停止更新本地状态", conclusive=False)
            # A later manual/dynamic booking may already occupy this period.
            # Never overwrite that newer local record with the old cancellation.
            if current is not None and not _same_reservation_snapshot(current, target):
                return result
            self.repo.save_reservation(
                day,
                period_name,
                "cancelled",
                target.get("start", ""),
                target.get("end", ""),
                target.get("room", ""),
                target.get("seat", ""),
                reason,
            )
        return result

    def _cancel_dynamic_reservation(self, day: str, period_name: str, operation_key: str) -> SeatResult:
        record = self.repo.get_reservation(day, period_name)
        if self.repo.has_dynamic_cancellation(day, operation_key):
            cancellation = self.repo.dynamic_cancellation(day, operation_key)
            target = _dynamic_cancellation_target(
                self.repo,
                day,
                period_name,
                cancellation,
                record,
            )
            if target is None:
                return SeatResult(False, message="取消记录缺少原预约信息，无法安全复核", conclusive=False)
            presence = self._reservation_presence_for_record(day, target)
            if presence is True:
                return SeatResult(False, message="此前取消记录存在，但远端仍有有效预约", conclusive=False)
            if presence is None:
                return SeatResult(False, message="此前取消记录存在，但无法读取‘我的预约’确认状态", conclusive=False)
            return SeatResult(
                True,
                target.get("room", ""),
                target.get("seat", ""),
                "动态取消操作已处理",
            )
        if not record or record["status"] not in {"reserved", "pending", "uncertain"}:
            return SeatResult(False, message="当前没有可取消的有效预约", conclusive=True)
        if self.repo.dynamic_cancellation_count(day) >= self.settings.max_cancel_per_day:
            return SeatResult(False, message=f"账号今日动态取消次数已达到 {self.settings.max_cancel_per_day} 次", conclusive=True)
        if (
            callable(getattr(self.adapter, "current_reservations", None))
            and not getattr(self.adapter, "is_dry_run", False)
        ):
            presence = self._reservation_presence_for_record(day, record)
            if presence is False:
                # A fresh query proved the booking is already gone; driving
                # the site's cancel flow would only fail and mask the state.
                return SeatResult(True, record.get("room", ""), record.get("seat", ""), "远端已无该预约，无需取消")
        try:
            result = self.adapter.cancel(day, period_name)
        except Exception as exc:
            result = SeatResult(False, message=f"动态取消适配器异常：{exc}", conclusive=False)
        if not result.success:
            return result
        presence = self._reservation_presence(day, period_name)
        if presence is not False:
            message = "取消后仍能读取到有效预约，结果不明确" if presence is True else "取消后无法读取‘我的预约’，结果不明确"
            return SeatResult(False, message=message, conclusive=False)
        snapshot = dict(record)
        snapshot["period"] = period_name
        if not self.repo.record_dynamic_cancellation(day, operation_key, snapshot):
            return SeatResult(True, record["room"], record["seat"], "动态取消操作已处理")
        self.repo.save_reservation(
            day,
            period_name,
            "cancelled",
            record["start"],
            record["end"],
            record["room"],
            record["seat"],
            "动态补偿已取消原预约",
        )
        return SeatResult(True, record["room"], record["seat"], "动态取消成功")

    def _notify_dynamic_result(self, day: str, period_name: str, result: SeatResult, start: str, end: str) -> None:
        if not getattr(self.settings, "notify_reservation_results", True):
            return
        account_label = (
            self.settings.wecom_aliases[0]
            if getattr(self.settings, "wecom_aliases", ())
            else self.account_id
        )
        send_reservation_notification(self.notifier, day, period_name, result, start, end, account_label)

    def notify_reconciled_reservation(self, day: str, period_name: str, reservation: dict | None) -> None:
        """Tell the user a previously inconclusive booking is now confirmed."""
        record = reservation or {}
        self._notify_dynamic_result(
            day,
            period_name,
            SeatResult(
                True,
                record.get("room", ""),
                record.get("seat", ""),
                "此前结果不明确，已在‘我的预约’中确认预约成功",
            ),
            record.get("start", ""),
            record.get("end", ""),
        )

    def _resolve_inconclusive_result(self, day: str, start: str, end: str, result: SeatResult) -> SeatResult:
        """Verify an inconclusive submission in fresh windows before reporting.

        Each attempt opens a new ‘我的预约’ query.  A successful query that
        finds no matching active booking is authoritative: the submission did
        not go through, and the failure is reported with its reasons instead
        of an open-ended 待核验.  Only query failures and seat-ambiguous rows
        consume another attempt.
        """
        query = getattr(self.adapter, "current_reservations", None)
        if query is None:
            return result
        attempts = max(1, int(getattr(self.settings, "reservation_verify_attempts", 3)))
        reasons: list[str] = []
        absent_streak = 0
        for attempt in range(1, attempts + 1):
            try:
                records = query(day)
            except Exception as exc:
                reasons.append(f"第{attempt}次核验失败：{compact_message(exc)}")
                continue
            if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
                reasons.append(f"第{attempt}次核验返回格式无效")
                continue
            confirmed = find_confirmed_reservation(
                records or [],
                day,
                {"start": start, "end": end, "room": result.room, "seat": result.seat},
            )
            if confirmed is not None and reservation_state(confirmed) in {"reserved", "in_use"}:
                return SeatResult(
                    True,
                    _remote_room(confirmed) or result.room,
                    _remote_seat(confirmed) or result.seat,
                    "预约结果不明确，已在‘我的预约’中确认预约成功",
                )
            # The site lists this account's own bookings, so an active row at
            # the exact interval is ours with a different seat display.  Adopt
            # it instead of reporting a failure that is not real.
            same_interval = [
                item for item in active_reservations_for_day(records or [], day)
                if _same_local_interval(item, {"start": start, "end": end})
            ]
            if same_interval:
                item = same_interval[0]
                return SeatResult(
                    True,
                    _remote_room(item) or result.room,
                    _remote_seat(item) or result.seat,
                    "存在相同时段的有效预约（座位显示不一致），已在‘我的预约’中确认",
                )
            absent_streak += 1
            if absent_streak >= 2:
                detail = "；".join(reasons)
                return SeatResult(
                    False,
                    result.room,
                    result.seat,
                    "已在‘我的预约’核验未找到该预约，判定未预约成功" + (f"（此前：{detail}）" if detail else ""),
                    conclusive=True,
                )
        return SeatResult(
            False,
            result.room,
            result.seat,
            "多次重开窗口核验‘我的预约’未成功，按未预约成功处理：" + "；".join(reasons),
            conclusive=True,
        )

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
                start_time = parse_hhmm(command.at)
            except (TypeError, ValueError):
                return {"ok": False, "message": "时间格式无效，请使用 HH:MM。"}
            end_value = command.end or period.departure_window[0]
            try:
                end_time = parse_hhmm(end_value)
                arrival_window = tuple(parse_hhmm(value) for value in period.arrival_window)
                departure = parse_hhmm(period.departure_window[0])
            except (TypeError, ValueError, IndexError) as exc:
                return {"ok": False, "message": f"时段配置无法解析：{exc}"}
            start_minutes = start_time.hour * 60 + start_time.minute
            end_minutes = end_time.hour * 60 + end_time.minute
            departure_minutes = departure.hour * 60 + departure.minute
            earliest_minutes = arrival_window[0].hour * 60 + arrival_window[0].minute - 30
            max_minutes = int(getattr(self.settings, "reservation_max_hours", 4)) * 60
            if start_minutes >= end_minutes:
                return {"ok": False, "message": "预约开始时间必须早于结束时间，未修改预约。"}
            if end_minutes - start_minutes > max_minutes:
                return {"ok": False, "message": f"预约时长最多 {max_minutes // 60} 小时，未修改预约。"}
            if end_minutes > departure_minutes:
                return {"ok": False, "message": f"结束时间不能晚于该时段结束 {period.departure_window[0]}，未修改预约。"}
            if start_minutes < earliest_minutes:
                return {
                    "ok": False,
                    "message": f"开始时间早于该时段允许范围（最早 {earliest_minutes // 60:02d}:{earliest_minutes % 60:02d}），未修改预约。",
                }
            if start_minutes % 30 or end_minutes % 30:
                return {"ok": False, "message": "预约开始和结束时间必须是半小时节点（如 12:30），未修改预约。"}
            interval_text = f"{command.at}-{end_value}"
            old_record = self.repo.get_reservation(day, command.period)
            has_query = (
                callable(getattr(self.adapter, "current_reservations", None))
                and not getattr(self.adapter, "is_dry_run", False)
            )
            should_cancel = False
            if old_record and has_query:
                presence = self._reservation_presence(day, command.period)
                if presence is None:
                    return {"ok": False, "message": "无法读取‘我的预约’确认原预约状态，已停止修改，请手动检查网站。"}
                should_cancel = presence is True
            elif old_record:
                should_cancel = old_record["status"] in {"reserved", "pending", "uncertain"}
            if should_cancel:
                try:
                    cancelled = self.adapter.cancel(day, command.period)
                except Exception as exc:
                    cancelled = SeatResult(False, message=f"原预约取消适配器异常：{exc}", conclusive=False)
                if not cancelled.success:
                    return {"ok": False, "message": f"原预约取消失败或结果不明确：{cancelled.message}，已停止修改，请手动检查网站。"}
                if has_query:
                    presence = self._reservation_presence(day, command.period)
                    if presence is not False:
                        detail = (
                            "取消后仍能读取到有效预约"
                            if presence is True
                            else "取消后无法读取‘我的预约’"
                        )
                        return {"ok": False, "message": f"原预约{detail}，已停止修改，请手动检查网站。"}
                record = self.repo.get_reservation(day, command.period)
                if record is not None:
                    self.repo.save_reservation(day, command.period, "cancelled", record["start"], record["end"], record["room"], record["seat"], "已为推迟重新规划")
            self.repo.event("delay", command.period, interval_text)
            result = self.reserve_period(day, command.period, interval_override=(command.at, end_value))
            if not result.success:
                return {"ok": False, "message": f"已按 {interval_text} 重新预约，但未成功：{result.message}"}
            return {"ok": True, "message": f"已将{command.period}预约调整为 {interval_text}，座位：{result.room} {result.seat}。"}
        if command.kind == "set_default":
            if command.period not in self.settings.periods:
                return {"ok": False, "message": "未识别学习时段。请使用上午、下午或晚上。"}
            period = self.settings.periods[command.period]
            end_value = command.end or period.departure_window[0]
            try:
                expected = parse_hhmm(command.at)
                end_time = parse_hhmm(end_value)
                arrival_window = tuple(parse_hhmm(value) for value in period.arrival_window)
                departure = parse_hhmm(period.departure_window[0])
                if command.end:
                    if end_time.hour * 60 + end_time.minute > departure.hour * 60 + departure.minute:
                        raise ValueError(f"结束时间不能晚于该时段结束 {period.departure_window[0]}")
                    if (end_time.hour * 60 + end_time.minute) % 30:
                        raise ValueError("结束时间必须是半小时节点")
                reservation = build_reservation_for_arrival(expected, arrival_window, end_time)
                max_minutes = int(getattr(self.settings, "reservation_max_hours", 4)) * 60
                if command.end and _clock_minutes(reservation.end) - _clock_minutes(reservation.start) > max_minutes:
                    raise ValueError(f"预约时长最多 {max_minutes // 60} 小时")
            except (TypeError, ValueError) as exc:
                if "时间格式" in str(exc) or "does not match format" in str(exc):
                    return {"ok": False, "message": "时间格式无效，请使用 HH:MM 或 HH:MM-HH:MM。"}
                return {"ok": False, "message": f"{command.at} 无法形成有效预约：{exc}。"}
            stored = f"{command.at}-{command.end}" if command.end else command.at
            self.settings.periods[command.period].default_arrival = command.at
            self.repo.set_default(command.period, stored)
            self.repo.event("default_override", command.period, stored)
            if command.end:
                return {"ok": True, "message": f"已将{command.period}默认到馆时间改为 {command.at}，默认结束时间 {command.end}（实际预约 {reservation.start.strftime('%H:%M')}-{reservation.end.strftime('%H:%M')}）。"}
            return {"ok": True, "message": f"已将{command.period}默认到馆时间改为 {command.at}。"}
        if command.kind in {"cancel", "cancel_day"}:
            periods = [command.period] if command.period else list(self.settings.periods)
            failures = []
            cancelled = 0
            for period in periods:
                record = self.repo.get_reservation(day, period)
                if record is None:
                    continue
                has_query = (
                    callable(getattr(self.adapter, "current_reservations", None))
                    and not getattr(self.adapter, "is_dry_run", False)
                )
                if record is not None and has_query:
                    presence = self._reservation_presence(day, period)
                    if presence is None:
                        failures.append(f"{period}:无法读取‘我的预约’，未执行取消")
                        continue
                    if presence is False:
                        if record["status"] in {"reserved", "pending", "uncertain"}:
                            cancelled += 1
                            self.repo.save_reservation(
                                day, period, "cancelled", record["start"], record["end"],
                                record["room"], record["seat"], "远端已不存在预约",
                            )
                            self.repo.event("cancel", period, "user")
                        continue
                elif record and record["status"] not in {"reserved", "pending", "uncertain"}:
                    # Without a remote query, a terminal local row cannot
                    # safely identify a booking that might still exist.
                    continue
                try:
                    result = self.adapter.cancel(day, period)
                except Exception as exc:
                    result = SeatResult(False, message=f"取消适配器异常：{exc}", conclusive=False)
                if not result.success:
                    if has_query:
                        presence = self._reservation_presence(day, period)
                        if presence is False:
                            cancelled += 1
                            if record is not None:
                                self.repo.save_reservation(
                                    day, period, "cancelled", record["start"], record["end"],
                                    record["room"], record["seat"], "远端已确认预约不存在",
                                )
                            self.repo.event("cancel", period, "user")
                            continue
                    failures.append(f"{period}: {result.message or '结果不明确'}")
                    continue
                presence = self._reservation_presence(day, period)
                if has_query and presence is not False:
                    detail = (
                        "取消后仍能读取到有效预约"
                        if presence is True
                        else "取消后无法读取‘我的预约’"
                    )
                    failures.append(f"{period}: {detail}，结果不明确")
                    continue
                cancelled += 1
                if record is not None:
                    self.repo.save_reservation(
                        day,
                        period,
                        "cancelled",
                        record["start"],
                        record["end"],
                        record["room"],
                        record["seat"],
                        "用户取消",
                    )
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
            interval = command.at if not command.end else f"{command.at}-{command.end}"
            return {"ok": True, "message": f"已收到时间 {interval}，请说明时段，例如：上午推迟到 {interval}。"}
        if command.kind == "status":
            return {"ok": True, "reservations": self.repo.reservations(day)}
        return {"ok": True, "message": "支持：状态、上午推迟、上午推迟到 09:20、取消上午、今天不去了、以后上午默认到馆时间为 09:05。"}

    def _reservation_still_active(self, day: str, period_name: str) -> bool:
        return self._reservation_presence(day, period_name) is True

    def _reservation_presence(self, day: str, period_name: str) -> bool | None:
        """Return remote presence; ``None`` means the confirmation failed."""
        query = getattr(self.adapter, "current_reservations", None)
        if query is None:
            return False
        try:
            records = query(day)
        except Exception:
            return None
        if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
            return None
        record = self.repo.get_reservation(day, period_name)
        if record is None:
            return False
        return self._reservation_presence_for_record(day, record, records=records)

    def _reservation_presence_for_record(
        self,
        day: str,
        record: dict | None,
        records: list[dict] | None = None,
    ) -> bool | None:
        if record is None:
            return False
        if records is None:
            query = getattr(self.adapter, "current_reservations", None)
            if not callable(query):
                return False
            try:
                records = query(day)
            except Exception:
                return None
            if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
                return None
        active = [
            item for item in day_reservations(records or [], day)
            if isinstance(item, dict) and reservation_state(item) in {"reserved", "in_use"}
        ]
        if find_cancelable_reservation(active, day, record) is not None:
            return True
        # A same-time row with another seat/location is not proof that the
        # local booking disappeared.  Treat it as ambiguous so cancellation
        # and resubmission stop safely instead of touching the wrong booking.
        if any(_same_local_interval(item, record) for item in active):
            return None
        return False

    def _reconcile_saved_reservation(self, day: str, record: dict) -> tuple[str, SeatResult]:
        """Resolve a prior pending submission before allowing another submit."""
        query = getattr(self.adapter, "current_reservations", None)
        if not callable(query):
            return (
                "hold",
                SeatResult(
                    False,
                    record.get("room", ""),
                    record.get("seat", ""),
                    "同一天已有结果不明确的待核验提交，当前无法读取‘我的预约’，已停止重复提交",
                    conclusive=False,
                ),
            )
        try:
            records = query(day)
        except Exception as exc:
            return (
                "hold",
                SeatResult(
                    False,
                    record.get("room", ""),
                    record.get("seat", ""),
                    f"同一天已有待核验提交，读取‘我的预约’失败：{exc}",
                    conclusive=False,
                ),
            )
        if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
            return (
                "hold",
                SeatResult(False, message="同一天已有待核验提交，‘我的预约’返回格式无效", conclusive=False),
            )
        matched = find_confirmed_reservation(records, day, record)
        if matched is not None and reservation_state(matched) in {"reserved", "in_use"}:
            adopted_message = (
                "检测到账号已有预约，已纳入本地状态，自动任务未重复提交"
                if not record.get("room") and not record.get("seat")
                else "已从‘我的预约’确认上次提交成功"
            )
            self.repo.save_reservation(
                day,
                record["period"],
                "reserved",
                record.get("start", ""),
                record.get("end", ""),
                _remote_room(matched) or record.get("room", ""),
                _remote_seat(matched) or record.get("seat", ""),
                adopted_message,
            )
            return (
                "recovered",
                SeatResult(
                    True,
                    _remote_room(matched) or record.get("room", ""),
                    _remote_seat(matched) or record.get("seat", ""),
                    adopted_message,
                ),
            )
        active_same_interval = any(
            _same_local_interval(item, record)
            for item in active_reservations_for_day(records, day)
        )
        if active_same_interval:
            return (
                "hold",
                SeatResult(False, message="存在相同时间的有效预约，但无法匹配座位，已停止重复提交", conclusive=False),
            )
        return "retry", SeatResult(False, message="", conclusive=True)


def _clock_minutes(value):
    if hasattr(value, "hour") and hasattr(value, "minute"):
        return value.hour * 60 + value.minute
    return parse_hhmm(str(value)).hour * 60 + parse_hhmm(str(value)).minute


def _reservation_has_ended(day: str, end: str, now: datetime) -> bool:
    try:
        end_at = datetime.combine(date.fromisoformat(day), parse_hhmm(end))
    except (TypeError, ValueError):
        return False
    return now >= end_at


def _format_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _same_local_interval(remote: dict, local: dict) -> bool:
    try:
        local_start = parse_hhmm(local.get("start")).strftime("%H:%M")
        local_end = parse_hhmm(local.get("end")).strftime("%H:%M")
    except (TypeError, ValueError):
        return False
    remote_start = _remote_time_text(remote, ("begin", "start", "startTime", "beginTime"))
    remote_end = _remote_time_text(remote, ("end", "endTime", "finishTime"))
    return remote_start == local_start and remote_end == local_end


def _is_pending_submission_message(message: str) -> bool:
    text = str(message or "").strip()
    return text.startswith(("已提交", "提交请求超过", "预约提交确认"))


def _remote_time_text(remote: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(remote.get(key) or "").strip()
        if value:
            try:
                return parse_hhmm(value).strftime("%H:%M")
            except (TypeError, ValueError):
                return value[-5:]
    return ""


def _remote_room(remote: dict) -> str:
    direct = str(remote.get("roomName") or remote.get("room_name") or remote.get("room") or "").strip()
    if direct:
        return direct
    location = str(remote.get("location") or remote.get("loc") or "").strip()
    return re.split(r"[，,]", location, maxsplit=1)[0].strip()


def _remote_seat(remote: dict) -> str:
    direct = str(remote.get("seatNumber") or remote.get("seatNo") or remote.get("seat") or "").strip()
    if direct:
        return direct
    location = str(remote.get("location") or remote.get("loc") or "")
    match = re.search(r"(?:座位号|座位|seat)\s*([0-9A-Za-z-]+)", location, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"([0-9]+)\s*号\s*$", location)
    return match.group(1) if match else ""


def _dynamic_cancellation_target(
    repo: Repository,
    day: str,
    period_name: str,
    cancellation: dict | None,
    current: dict | None,
) -> dict | None:
    """Return the original booking targeted by an idempotent cancellation.

    New rows contain a complete snapshot. Older databases only have the
    operation key and cannot prove which booking was cancelled, so they must
    remain in the protection state instead of guessing from current data.
    """
    if not cancellation or not cancellation.get("start") or not cancellation.get("end"):
        return None
    target = dict(cancellation)
    target["period"] = period_name
    return target


def _same_reservation_snapshot(current: dict, target: dict) -> bool:
    if str(current.get("start") or "") != str(target.get("start") or ""):
        return False
    if str(current.get("end") or "") != str(target.get("end") or ""):
        return False
    target_seat = "".join(str(target.get("seat") or "").split()).upper()
    current_seat = "".join(str(current.get("seat") or "").split()).upper()
    return not target_seat or not current_seat or target_seat == current_seat


def _find_strict_reservation(records: list[dict], day: str, expected: dict) -> dict | None:
    """Find the exact booking before a destructive operation.

    ``find_reservation_record`` intentionally falls back to a unique time-only
    match for monitoring and post-submit reconciliation.  That fallback is not
    safe for cancellation: another user's booking can occupy the same
    interval.  A seat identifier is therefore required here when the local
    record has one, and the normal room/seat matching rules must both pass.
    """
    seat = str(expected.get("seat") or "").strip()
    if not seat:
        return None
    return find_matching_reservation(
        records,
        day,
        str(expected.get("room") or ""),
        seat,
        str(expected.get("start") or ""),
        str(expected.get("end") or ""),
    )


def _current_reservation_end(now: datetime, latest_end: str | None = None) -> str:
    total_minutes = now.hour * 60 + now.minute + 240
    rounded = (total_minutes // 30) * 30
    if latest_end:
        try:
            rounded = min(rounded, _clock_minutes(parse_hhmm(latest_end)))
        except (TypeError, ValueError):
            pass
    return f"{rounded // 60:02d}:{rounded % 60:02d}"
