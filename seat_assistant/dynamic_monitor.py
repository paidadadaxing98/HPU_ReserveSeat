"""Shared dynamic compensation controller for one account and its periods."""

from datetime import date, datetime, timedelta
import logging

from .access_records import first_entry_time
from .commands import REMOTE_COMMAND_KINDS, parse_command
from .dynamic_compensation import (
    Decision,
    DecisionKind,
    compensation_window,
    evaluate,
    next_poll_delay,
    parse_away_interval,
    temporary_leave_decision,
)
from .domain import parse_hhmm
from .submission import find_reservation_record, reservation_state


class DynamicMonitor:
    def __init__(self, service):
        self.service = service
        self.settings = service.settings

    def has_pending_commands(self, day: str) -> bool:
        return bool(self.service.repo.pending_bot_commands(day, limit=1))

    def tick(
        self,
        day: str,
        now: datetime,
        records: list[dict] | None = None,
        access_error: str = "",
        reservation_records: list[dict] | None = None,
        reservation_error: str = "",
    ) -> dict:
        command_results = self._process_pending_commands(day, now)
        enabled = [
            (name, period)
            for name, period in self.settings.periods.items()
            if getattr(period, "enabled", True)
        ]
        if not self.settings.dynamic_compensation_enabled or len(enabled) > self.settings.dynamic_max_periods:
            result = {
                "status": "disabled",
                "message": "启用时段超过动态补偿上限，全部使用静态预约",
            }
            if command_results:
                result["commands"] = command_results
            return result
        entry_at = first_entry_time(records or [], day)
        results = {"commands": command_results} if command_results else {}
        for period_name, _period in enabled:
            session = self._ensure_session(day, period_name, now)
            if session is None:
                continue
            if session["status"] not in {"monitoring", "entered"}:
                results[period_name] = session["status"]
                continue
            reservation = self.service.repo.get_reservation(day, period_name)
            if reservation_error:
                self.service.repo.update_dynamic_session(
                    day,
                    period_name,
                    last_checked_at=now.isoformat(timespec="seconds"),
                    message="我的预约查询失败，保留当前预约",
                )
                results[period_name] = "error_hold"
                continue
            if access_error:
                self.service.repo.update_dynamic_session(
                    day,
                    period_name,
                    last_checked_at=now.isoformat(timespec="seconds"),
                    message="门禁查询失败，保留当前预约",
                )
                results[period_name] = "error_hold"
                continue
            if session["status"] == "entered" and reservation_records is None:
                self.service.repo.update_dynamic_session(
                    day,
                    period_name,
                    last_checked_at=now.isoformat(timespec="seconds"),
                    message="已检测到履约，等待下一次我的预约状态查询",
                )
                results[period_name] = "entered"
                continue
            if reservation_records is not None:
                live_record = find_reservation_record(reservation_records, day, reservation or {})
                state = reservation_state(live_record) if live_record is not None else None
                if state == "in_use":
                    changes = {
                        "status": "entered",
                        "last_checked_at": now.isoformat(timespec="seconds"),
                        "message": "我的预约状态为履约中，继续监测暂离",
                    }
                    if session["status"] != "entered":
                        changes["entered_at"] = now.isoformat(timespec="seconds")
                    leave_decision, away_begin = self._temporary_leave_decision(
                        day,
                        reservation or {},
                        live_record,
                        now,
                    )
                    if leave_decision is None:
                        self.service.repo.update_dynamic_session(day, period_name, **changes)
                        results[period_name] = "entered"
                        continue
                    if leave_decision.kind == DecisionKind.AWAY_CANCEL:
                        operation_key = f"{period_name}:away:{away_begin.isoformat()}"
                        cancel_result = self.service.dynamic_cancel_period(
                            day,
                            period_name,
                            operation_key,
                            leave_decision.reason,
                        )
                        if cancel_result.success:
                            changes.update(
                                status="away_cancelled",
                                last_action_at=now.isoformat(timespec="seconds"),
                                cancel_count=self.service.repo.dynamic_cancellation_count(day),
                                message=leave_decision.reason,
                            )
                            results[period_name] = "away_cancel"
                        else:
                            changes.update(status="error_hold", message=cancel_result.message or leave_decision.reason)
                            results[period_name] = "error_hold"
                        self.service.repo.update_dynamic_session(day, period_name, **changes)
                        continue
                    changes["message"] = leave_decision.reason
                    self.service.repo.update_dynamic_session(day, period_name, **changes)
                    results[period_name] = (
                        "error_hold"
                        if leave_decision.kind == DecisionKind.AWAY_HOLD
                        else leave_decision.kind.value
                    )
                    continue
                if state in {"completed", "missed", "cancelled"}:
                    self.service.repo.update_dynamic_session(
                        day,
                        period_name,
                        status=state,
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message=f"我的预约状态为{state}，停止动态调整",
                    )
                    results[period_name] = state
                    continue
                if session["status"] == "entered":
                    self.service.repo.update_dynamic_session(
                        day,
                        period_name,
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message="已入馆会话未读取到履约中状态，保留预约并等待下一轮",
                    )
                    results[period_name] = "error_hold"
                    continue
                if state == "unknown" or state is None:
                    self.service.repo.update_dynamic_session(
                        day,
                        period_name,
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message="我的预约记录未形成唯一状态，保留当前预约并继续按时间规则监测",
                    )
                    results[period_name] = "error_hold"
                    continue
            anchor = datetime.fromisoformat(session["anchor_start"])
            decision = evaluate(
                now,
                anchor,
                entry_at=entry_at,
                action_index=int(session["action_index"]),
                boundary_lead_minutes=self.settings.dynamic_boundary_lead_minutes,
                interval_minutes=self.settings.dynamic_late_reschedule_minutes,
                before_minutes=self.settings.dynamic_before_minutes,
                after_minutes=self.settings.dynamic_after_minutes,
            )
            changes = {"last_checked_at": now.isoformat(timespec="seconds")}
            if decision.kind == DecisionKind.WAIT:
                self.service.repo.update_dynamic_session(day, period_name, **changes)
                results[period_name] = "wait"
                continue
            if decision.kind == DecisionKind.ENTERED:
                changes.update(status="entered", entered_at=entry_at.isoformat(timespec="seconds"), message=decision.reason)
                self.service.repo.update_dynamic_session(day, period_name, **changes)
                results[period_name] = "entered"
                continue
            if decision.kind == DecisionKind.EARLY_RESCHEDULE:
                operation_key = f"{period_name}:early:{entry_at.isoformat()}"
                result = self.service.dynamic_reschedule_period(day, period_name, now, operation_key)
                if result.success:
                    changes.update(
                        status="entered",
                        entered_at=entry_at.isoformat(timespec="seconds"),
                        last_action_at=now.isoformat(timespec="seconds"),
                        cancel_count=self.service.repo.dynamic_cancellation_count(day),
                        message="早到已按当前时间重新预约，停止动态调整",
                    )
                    results[period_name] = "early_reschedule"
                else:
                    changes.update(status="error_hold", message=result.message or "早到动态重约失败")
                    results[period_name] = "error_hold"
                self.service.repo.update_dynamic_session(day, period_name, **changes)
                continue
            if decision.kind == DecisionKind.RESCHEDULE:
                point = f"{period_name}:late:{session['action_index']}"
                result = self.service.dynamic_reschedule_period(day, period_name, now, point)
                if result.success:
                    changes.update(
                        action_index=int(session["action_index"]) + 1,
                        last_action_at=now.isoformat(timespec="seconds"),
                        cancel_count=self.service.repo.dynamic_cancellation_count(day),
                        message=decision.reason,
                    )
                    results[period_name] = "reschedule"
                else:
                    changes.update(status="error_hold", message=result.message or "迟到动态重约失败")
                    results[period_name] = "error_hold"
                self.service.repo.update_dynamic_session(day, period_name, **changes)
                continue
            if decision.kind == DecisionKind.CANCEL_UNENTERED:
                operation_key = f"{period_name}:cutoff:{session['action_index']}"
                result = self.service.dynamic_cancel_period(day, period_name, operation_key, decision.reason)
                changes.update(status="cancelled" if result.success else "error_hold", message=result.message or decision.reason)
                self.service.repo.update_dynamic_session(day, period_name, **changes)
                results[period_name] = "cancel_unentered" if result.success else "error_hold"
        return results

    def prepare(self, day: str) -> dict:
        """Create restart-safe sessions without querying the access provider."""
        enabled = [
            name for name, period in self.settings.periods.items()
            if getattr(period, "enabled", True)
        ]
        if not self.settings.dynamic_compensation_enabled or len(enabled) > self.settings.dynamic_max_periods:
            return {"status": "disabled"}
        sessions = {}
        now = datetime.now()
        for period_name in enabled:
            session = self._ensure_session(day, period_name, now)
            if session is not None:
                sessions[period_name] = session["status"]
        return sessions

    def requires_browser_operation(
        self,
        day: str,
        now: datetime,
        records: list[dict] | None = None,
        reservation_records: list[dict] | None = None,
    ) -> bool:
        if self.has_pending_commands(day):
            return True
        entry_at = first_entry_time(records or [], day)
        for session in self.service.repo.dynamic_sessions(day):
            if session["status"] == "entered":
                if reservation_records is None:
                    continue
                reservation = self.service.repo.get_reservation(day, session["period"])
                live_record = find_reservation_record(reservation_records, day, reservation or {})
                if live_record is None or reservation_state(live_record) != "in_use":
                    continue
                decision, _ = self._temporary_leave_decision(day, reservation or {}, live_record, now)
                if decision is not None and decision.kind == DecisionKind.AWAY_CANCEL:
                    return True
                continue
            if session["status"] != "monitoring":
                continue
            decision = evaluate(
                now,
                datetime.fromisoformat(session["anchor_start"]),
                entry_at=entry_at,
                action_index=int(session["action_index"]),
                boundary_lead_minutes=self.settings.dynamic_boundary_lead_minutes,
                interval_minutes=self.settings.dynamic_late_reschedule_minutes,
                before_minutes=self.settings.dynamic_before_minutes,
                after_minutes=self.settings.dynamic_after_minutes,
            )
            if decision.kind in {
                DecisionKind.EARLY_RESCHEDULE,
                DecisionKind.RESCHEDULE,
                DecisionKind.CANCEL_UNENTERED,
            }:
                return True
        return False

    def next_poll_delay(self, day: str, now: datetime):
        delays = []
        if self.has_pending_commands(day):
            delays.append(timedelta(seconds=self.settings.dynamic_boundary_poll_seconds))
        for session in self.service.repo.dynamic_sessions(day):
            if session["status"] == "entered":
                delays.append(timedelta(seconds=self.settings.dynamic_boundary_poll_seconds))
                continue
            if session["status"] != "monitoring":
                continue
            delays.append(
                next_poll_delay(
                    now,
                    datetime.fromisoformat(session["anchor_start"]),
                    action_index=int(session["action_index"]),
                    normal_poll_seconds=self.settings.dynamic_normal_poll_seconds,
                    boundary_poll_seconds=self.settings.dynamic_boundary_poll_seconds,
                    boundary_lead_minutes=self.settings.dynamic_boundary_lead_minutes,
                    interval_minutes=self.settings.dynamic_late_reschedule_minutes,
                    before_minutes=self.settings.dynamic_before_minutes,
                    after_minutes=self.settings.dynamic_after_minutes,
                )
            )
        return min(delays) if delays else None

    def _process_pending_commands(self, day: str, now: datetime) -> list[str]:
        results = []
        for item in self.service.repo.pending_bot_commands(day):
            if not self.service.repo.claim_bot_command(item["request_id"]):
                continue
            command = None
            try:
                command = parse_command(item["text"])
                if command.kind not in REMOTE_COMMAND_KINDS:
                    response = {"ok": False, "message": "该命令不在手机控制白名单中。"}
                else:
                    response = self.service.apply_command(command, day)
            except Exception as exc:
                response = {"ok": False, "message": f"命令执行异常：{exc}"}
            status = "completed" if response.get("ok") else "failed"
            self.service.repo.complete_bot_command(item["request_id"], status, response)
            text = _format_bot_command_result(item["text"], response)
            results.append(text)
            self._notify_command_result(text)
            if response.get("ok") and command is not None and command.kind in {"cancel", "cancel_day"}:
                periods = [command.period] if command.period else list(self.settings.periods)
                for period_name in periods:
                    if self.service.repo.get_dynamic_session(day, period_name) is not None:
                        self.service.repo.update_dynamic_session(
                            day,
                            period_name,
                            status="cancelled",
                            last_action_at=now.isoformat(timespec="seconds"),
                            message="已通过企业微信命令取消预约",
                        )
        return results

    def _notify_command_result(self, text: str) -> None:
        notifier = getattr(self.service, "notifier", None)
        send = getattr(notifier, "send", None)
        if send is None:
            return
        try:
            send(text)
        except Exception:
            logging.getLogger(__name__).warning("企业微信命令结果通知失败", exc_info=True)

    def _temporary_leave_decision(
        self,
        day: str,
        reservation: dict,
        live_record: dict,
        now: datetime,
    ) -> tuple[Decision | None, datetime | None]:
        away_begin_value = live_record.get("awayBegin")
        away_end_value = live_record.get("awayEnd")
        if away_end_value not in (None, "") or away_begin_value in (None, ""):
            return None, None
        parsed = parse_away_interval(day, away_begin_value, away_end_value)
        if parsed is None:
            return Decision(DecisionKind.AWAY_HOLD, "暂离时间无法解析，保留当前预约"), None
        away_begin, _ = parsed
        end_value = live_record.get("end") or live_record.get("endTime") or reservation.get("end")
        try:
            reservation_end = datetime.combine(date.fromisoformat(day), parse_hhmm(end_value))
        except (TypeError, ValueError):
            return Decision(DecisionKind.AWAY_HOLD, "预约结束时间无法解析，保留当前预约"), None
        return temporary_leave_decision(
            now,
            away_begin,
            reservation_end,
            self.settings.dynamic_boundary_lead_minutes,
        ), away_begin

    def _ensure_session(self, day: str, period_name: str, now: datetime | None = None):
        now = now or datetime.now()
        session = self.service.repo.get_dynamic_session(day, period_name)
        if session is not None:
            if session["status"] in {"monitoring", "entered"}:
                return session
        record = self.service.repo.get_reservation(day, period_name)
        if not record or record["status"] != "reserved":
            manual = self.service.repo.get_reservation(day, "manual")
            if self._manual_period_for_record(day, manual, now) == period_name:
                record = self._adopt_manual_reservation(day, period_name, manual)
                session = None
        if session is not None:
            return session
        if not record or record["status"] != "reserved":
            return None
        try:
            target_day = date.fromisoformat(day)
            try:
                start_time = parse_hhmm(record["start"])
            except (TypeError, ValueError):
                start_value = str(record.get("start") or "").strip()
                if target_day != now.date() or start_value.lower() not in {"now", "current"} and start_value not in {"当前", "现在"}:
                    raise
                start_time = now.time().replace(second=0, microsecond=0)
            anchor_start = datetime.combine(target_day, start_time)
            anchor_end = datetime.combine(date.fromisoformat(day), parse_hhmm(record["end"]))
        except (TypeError, ValueError):
            self.service.repo.save_dynamic_session(
                day, period_name, "", "", "", "", status="error_hold", message="原预约时间无法作为动态窗口锚点"
            )
            return self.service.repo.get_dynamic_session(day, period_name)
        window_start, window_end = compensation_window(
            anchor_start,
            self.settings.dynamic_before_minutes,
            self.settings.dynamic_after_minutes,
        )
        self.service.repo.save_dynamic_session(
            day,
            period_name,
            anchor_start.isoformat(timespec="seconds"),
            anchor_end.isoformat(timespec="seconds"),
            window_start.isoformat(timespec="seconds"),
            window_end.isoformat(timespec="seconds"),
        )
        return self.service.repo.get_dynamic_session(day, period_name)

    def _manual_period_for_record(self, day: str, record: dict | None, now: datetime) -> str | None:
        """Map one successful manual booking to its configured study period."""
        if not record or record.get("status") != "reserved":
            return None
        try:
            target_day = date.fromisoformat(day)
            end = parse_hhmm(record.get("end"))
        except (TypeError, ValueError):
            return None
        start_value = str(record.get("start") or "").strip()
        try:
            start = parse_hhmm(start_value)
        except (TypeError, ValueError):
            if start_value.lower() not in {"now", "current"} and start_value not in {"当前", "现在"}:
                return None
            if now.date() != target_day:
                return None
            start = now.time().replace(second=0, microsecond=0)

        candidates = []
        for name, period in self.settings.periods.items():
            if not getattr(period, "enabled", True):
                continue
            try:
                arrival_start = parse_hhmm(period.arrival_window[0])
                arrival_end = parse_hhmm(period.arrival_window[1])
                period_end = parse_hhmm(period.departure_window[0])
            except (TypeError, ValueError, IndexError):
                continue
            if end != period_end or start >= end:
                continue
            if arrival_start <= start < arrival_end:
                score = 2
            elif start < arrival_end and end > arrival_start:
                score = 1
            else:
                continue
            candidates.append((score, name))
        if not candidates:
            return None
        best_score = max(score for score, _name in candidates)
        best = [name for score, name in candidates if score == best_score]
        return best[0] if len(best) == 1 else None

    def _adopt_manual_reservation(self, day: str, period_name: str, record: dict) -> dict:
        """Transfer a manual booking into the period state used by monitoring."""
        self.service.repo.save_reservation(
            day,
            period_name,
            "reserved",
            record["start"],
            record["end"],
            record.get("room", ""),
            record.get("seat", ""),
            "已将手动预约自动纳入动态监控",
        )
        self.service.repo.save_reservation(
            day,
            "manual",
            "cancelled",
            record["start"],
            record["end"],
            record.get("room", ""),
            record.get("seat", ""),
            "已自动归入" + period_name + "动态监控",
        )
        return self.service.repo.get_reservation(day, period_name)


def _format_bot_command_result(command_text: str, response: dict) -> str:
    message = str(response.get("message") or "").strip()
    if message:
        return f"{command_text}：{message}"
    reservations = response.get("reservations")
    if isinstance(reservations, list):
        if not reservations:
            return f"{command_text}：当前没有预约记录。"
        lines = []
        for row in reservations:
            if not isinstance(row, (list, tuple)) or len(row) < 4:
                continue
            lines.append(f"{row[0]} {row[1]} {row[2]}-{row[3]}")
        return f"{command_text}：" + ("；".join(lines) if lines else "当前没有可显示的预约记录。")
    return f"{command_text}：已处理。"
