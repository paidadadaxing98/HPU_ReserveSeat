"""Shared dynamic compensation controller for one account and its periods."""

from datetime import date, datetime, timedelta

from .access_records import first_entry_time
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

    def tick(
        self,
        day: str,
        now: datetime,
        records: list[dict] | None = None,
        access_error: str = "",
        reservation_records: list[dict] | None = None,
        reservation_error: str = "",
    ) -> dict:
        enabled = [
            (name, period)
            for name, period in self.settings.periods.items()
            if getattr(period, "enabled", True)
        ]
        if not self.settings.dynamic_compensation_enabled or len(enabled) > self.settings.dynamic_max_periods:
            return {
                "status": "disabled",
                "message": "启用时段超过动态补偿上限，全部使用静态预约",
            }
        entry_at = first_entry_time(records or [], day)
        results = {}
        for period_name, _period in enabled:
            session = self._ensure_session(day, period_name)
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
        for period_name in enabled:
            session = self._ensure_session(day, period_name)
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

    def _ensure_session(self, day: str, period_name: str):
        session = self.service.repo.get_dynamic_session(day, period_name)
        if session is not None:
            return session
        record = self.service.repo.get_reservation(day, period_name)
        if not record or record["status"] != "reserved":
            return None
        try:
            anchor_start = datetime.combine(date.fromisoformat(day), parse_hhmm(record["start"]))
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
