"""Shared dynamic compensation controller for one account and its periods."""

from datetime import date, datetime, timedelta
import logging
import re

from .access_records import first_entry_time
from .commands import REMOTE_COMMAND_KINDS, parse_command
from .dynamic_compensation import (
    Decision,
    DecisionKind,
    compensation_window,
    evaluate,
    finalize_start,
    last_action_deadline,
    next_late_action_index,
    next_poll_delay,
    parse_away_interval,
    reservation_checkpoints,
    temporary_leave_decision,
)
from .domain import parse_hhmm
from .submission import (
    _extract_room,
    _extract_seat,
    _normalize_room,
    _normalize_seat,
    _room_matches,
    _seats_match,
    find_confirmed_reservation,
    find_reservation_record,
    reservation_state,
)
from .runtime_logging import compact_message


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
        target_period: str | None = None,
        remote_stage: str | None = None,
    ) -> dict:
        command_results = self._process_pending_commands(day, now)
        if remote_stage == "commands":
            return {"commands": command_results} if command_results else {}
        if remote_stage is not None:
            return self._tick_remote_stage(
                day,
                now,
                remote_stage,
                command_results,
                records=records,
                access_error=access_error,
                reservation_records=reservation_records,
                reservation_error=reservation_error,
                target_period=target_period,
            )
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
        if target_period is not None:
            enabled = [(name, period) for name, period in enabled if name == target_period]
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
            anchor = datetime.fromisoformat(session["anchor_start"])
            reschedule_context = str(session.get("message") or "").startswith("动态重约未完成：")
            retrying_reschedule = bool(
                reservation and reservation.get("status") in {"failed", "uncertain"}
            )
            if reservation_records is not None and retrying_reschedule:
                # A timeout can happen after the site accepted the submit.
                # Reconcile the exact saved interval before attempting another
                # cancel/reserve cycle.
                recovered = find_confirmed_reservation(reservation_records, day, reservation)
                recovered_state = reservation_state(recovered) if recovered is not None else None
                if recovered_state in {"reserved", "in_use"}:
                    self.service.repo.save_reservation(
                        day,
                        period_name,
                        "reserved",
                        reservation.get("start", ""),
                        reservation.get("end", ""),
                        reservation.get("room", ""),
                        reservation.get("seat", ""),
                        "已从‘我的预约’确认动态重约结果",
                    )
                    self.service.notify_reconciled_reservation(day, period_name, reservation)
                    if recovered_state == "in_use" or entry_at is not None:
                        self.service.repo.update_dynamic_session(
                            day,
                            period_name,
                            status="entered",
                            entered_at=(entry_at or now).isoformat(timespec="seconds"),
                            last_checked_at=now.isoformat(timespec="seconds"),
                            message=(
                                "动态重约后已从‘我的预约’确认预约，并结合门禁记录判定已入馆"
                                if entry_at is not None
                                else "动态重约后已在‘我的预约’中确认履约中"
                            ),
                        )
                        results[period_name] = "entered"
                    else:
                        anchor = datetime.fromisoformat(session["anchor_start"])
                        self.service.repo.update_dynamic_session(
                            day,
                            period_name,
                            status="monitoring",
                            action_index=next_late_action_index(
                                now,
                                anchor,
                                int(session["action_index"]),
                                self.settings.dynamic_boundary_lead_minutes,
                                self.settings.dynamic_late_reschedule_minutes,
                                self.settings.dynamic_before_minutes,
                                self.settings.dynamic_after_minutes,
                            ),
                            last_checked_at=now.isoformat(timespec="seconds"),
                            message="已从‘我的预约’确认动态重约成功，继续监测",
                        )
                        results[period_name] = "reconciled"
                    continue
                if recovered_state in {"completed", "missed", "cancelled"}:
                    self.service.repo.update_dynamic_session(
                        day,
                        period_name,
                        status=recovered_state,
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message=f"已从‘我的预约’确认动态重约后的状态为{recovered_state}",
                    )
                    results[period_name] = recovered_state
                    continue
            if reservation_records is not None and not retrying_reschedule:
                live_record = find_reservation_record(reservation_records, day, reservation or {})
                state = reservation_state(live_record) if live_record is not None else None
                if state == "in_use":
                    self._sync_remote_display(day, period_name, reservation or {}, live_record)
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
                    if reservation and reservation.get("status") != state:
                        self.service.repo.save_reservation(
                            day,
                            period_name,
                            state,
                            reservation.get("start", ""),
                            reservation.get("end", ""),
                            reservation.get("room", ""),
                            reservation.get("seat", ""),
                            f"‘我的预约’状态为{state}，已同步到本地",
                        )
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
                        message="我的预约快照未匹配本地记录，按时间规则继续并在操作前再次核验",
                    )
            if session["status"] == "monitoring":
                self._reconcile_action_progress(day, period_name, session, reservation, anchor)
                session = self.service.repo.get_dynamic_session(day, period_name) or session
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
                    changes.update(status="error_hold", message=f"动态重约未完成：{result.message or '早到动态重约失败'}")
                    results[period_name] = "error_hold"
                self.service.repo.update_dynamic_session(day, period_name, **changes)
                continue
            if decision.kind == DecisionKind.RESCHEDULE:
                point = f"{period_name}:late:{session['action_index']}"
                result = self.service.dynamic_reschedule_period(day, period_name, now, point)
                if result.success:
                    changes.update(
                        action_index=next_late_action_index(
                            now,
                            datetime.fromisoformat(session["anchor_start"]),
                            int(session["action_index"]),
                            self.settings.dynamic_boundary_lead_minutes,
                            self.settings.dynamic_late_reschedule_minutes,
                            self.settings.dynamic_before_minutes,
                            self.settings.dynamic_after_minutes,
                        ),
                        last_action_at=now.isoformat(timespec="seconds"),
                        cancel_count=self.service.repo.dynamic_cancellation_count(day),
                        message=decision.reason,
                    )
                    results[period_name] = "reschedule"
                else:
                    changes.update(status="error_hold", message=f"动态重约未完成：{result.message or '迟到动态重约失败'}")
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

    def _gate_enabled(self) -> bool:
        """Gate records are optional; the monitor must work without them."""
        return bool(getattr(self.settings, "access_records_url", ""))

    def _required_check_stages(self) -> tuple[str, ...]:
        """Stages that must complete before a late rebook is allowed."""
        if self._gate_enabled():
            return ("access", "reservation_first", "reservation_final")
        return ("reservation_first", "reservation_final")

    def _attempt_throttled(self, session: dict, now: datetime) -> bool:
        """True while the last remote attempt is too recent to repeat.

        A failed or unmatched query never consumes its checkpoint, so this
        throttle is what keeps the retry cadence at one poll instead of a
        tight browser-open loop.
        """
        checked = session.get("last_checked_at")
        if not checked:
            return False
        try:
            last = datetime.fromisoformat(checked)
        except (TypeError, ValueError):
            return False
        throttle = timedelta(seconds=self.settings.dynamic_boundary_poll_seconds)
        return now < last + throttle

    def _throttled_next_at(self, session: dict) -> datetime:
        checked = session.get("last_checked_at")
        try:
            last = datetime.fromisoformat(checked)
        except (TypeError, ValueError):
            return datetime.now()
        return last + timedelta(seconds=self.settings.dynamic_boundary_poll_seconds)

    def remote_query_plan(
        self,
        day: str,
        now: datetime,
        target_period: str | None = None,
    ) -> dict:
        """Return the next remote check without opening a browser.

        Unentered sessions deliberately expose one checkpoint at a time.  If
        the process was restarted after a checkpoint, the remaining checks are
        performed in order instead of treating one late snapshot as three
        independent observations.
        """
        candidates: list[datetime] = []
        enabled = [
            name for name, period in self.settings.periods.items()
            if getattr(period, "enabled", True)
        ]
        if not self.settings.dynamic_compensation_enabled or len(enabled) > self.settings.dynamic_max_periods:
            return {"stage": None, "due": False, "next_at": None}
        if target_period is not None:
            enabled = [name for name in enabled if name == target_period]
        for period_name in enabled:
            self._ensure_session(day, period_name, now)
        for session in self.service.repo.dynamic_sessions(day):
            if target_period is not None and session["period"] != target_period:
                continue
            if session["status"] == "entered":
                checked = session.get("last_checked_at")
                if not checked:
                    return {"stage": "entered", "due": True, "next_at": now}
                try:
                    next_at = datetime.fromisoformat(checked) + timedelta(
                        seconds=self.settings.dynamic_boundary_poll_seconds
                    )
                except (TypeError, ValueError):
                    return {"stage": "entered", "due": True, "next_at": now}
                if now >= next_at:
                    return {"stage": "entered", "due": True, "next_at": next_at}
                candidates.append(next_at)
                continue
            if session["status"] != "monitoring":
                continue
            try:
                window_end = datetime.fromisoformat(session["window_end"])
                current_start = datetime.fromisoformat(
                    session.get("current_start") or session["anchor_start"]
                )
            except (TypeError, ValueError):
                continue
            if now >= window_end:
                continue
            finalize_at = finalize_start(window_end)
            if now >= finalize_at:
                if self._attempt_throttled(session, now):
                    candidates.append(self._throttled_next_at(session))
                    continue
                return {"stage": "finalize", "due": True, "next_at": now}
            candidates.append(finalize_at)
            if (
                str(session.get("message") or "").startswith("动态重约未完成")
                and self._late_checks_complete(day, session["period"], session)
            ):
                if self._attempt_throttled(session, now):
                    candidates.append(self._throttled_next_at(session))
                    continue
                return {"stage": "reservation_final", "due": True, "next_at": now}
            checkpoints = reservation_checkpoints(
                current_start,
                round_index=int(session.get("round_index") or 0),
                access_before_minutes=self.settings.dynamic_access_check_before_minutes,
                reservation_first_before_minutes=self.settings.dynamic_reservation_first_check_before_minutes,
                reservation_final_before_minutes=self.settings.dynamic_reservation_final_check_before_minutes,
            )
            for stage, checkpoint in checkpoints.items():
                if stage == "access" and not self._gate_enabled():
                    continue
                if self.service.repo.dynamic_check_at(
                    day,
                    session["period"],
                    stage,
                    reservation_start=session.get("current_start"),
                ):
                    continue
                if now >= checkpoint:
                    if self._attempt_throttled(session, now):
                        candidates.append(self._throttled_next_at(session))
                        break
                    return {"stage": stage, "due": True, "next_at": checkpoint}
                candidates.append(checkpoint)
                break
        next_at = min(candidates) if candidates else None
        return {"stage": None, "due": False, "next_at": next_at}

    def _tick_remote_stage(
        self,
        day: str,
        now: datetime,
        remote_stage: str,
        command_results: list[str],
        *,
        records: list[dict] | None,
        access_error: str,
        reservation_records: list[dict] | None,
        reservation_error: str,
        target_period: str | None,
    ) -> dict:
        enabled = [
            name for name, period in self.settings.periods.items()
            if getattr(period, "enabled", True)
        ]
        if not self.settings.dynamic_compensation_enabled or len(enabled) > self.settings.dynamic_max_periods:
            result = {"status": "disabled", "message": "启用时段超过动态补偿上限，全部使用静态预约"}
            if command_results:
                result["commands"] = command_results
            return result
        if target_period is not None:
            enabled = [name for name in enabled if name == target_period]
        results = {"commands": command_results} if command_results else {}
        for period_name in enabled:
            session = self._ensure_session(day, period_name, now)
            if session is None:
                continue
            if session["status"] not in {"monitoring", "entered"}:
                results[period_name] = session["status"]
                continue
            if remote_stage == "access":
                if not self._remote_stage_due(day, period_name, session, remote_stage, now):
                    continue
                if not self._gate_enabled():
                    # No gate source is configured: the access check cannot
                    # observe anything.  Record it as completed so the two
                    # reservation checks still drive the late decision.
                    self.service.repo.mark_dynamic_check(
                        day,
                        period_name,
                        "access",
                        now.isoformat(timespec="seconds"),
                        reservation_start=session.get("current_start"),
                    )
                    self.service.repo.update_dynamic_session(
                        day, period_name,
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message="未配置门禁接口，跳过门禁检查",
                    )
                    results[period_name] = "wait"
                    continue
                if access_error:
                    self._hold_remote_query(day, period_name, now, "门禁查询失败，保留当前预约")
                    results[period_name] = "error_hold"
                    continue
                entry_at = first_entry_time(records or [], day)
                if entry_at is None:
                    self.service.repo.mark_dynamic_check(
                        day,
                        period_name,
                        "access",
                        now.isoformat(timespec="seconds"),
                        reservation_start=session.get("current_start"),
                    )
                    self.service.repo.update_dynamic_session(
                        day, period_name,
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message="已完成门禁检查，等待两次预约状态检查",
                    )
                    results[period_name] = "wait"
                    continue
                anchor = datetime.fromisoformat(session["anchor_start"])
                self.service.repo.mark_dynamic_check(
                    day,
                    period_name,
                    "access",
                    now.isoformat(timespec="seconds"),
                    reservation_start=session.get("current_start"),
                )
                if entry_at < anchor - timedelta(minutes=30):
                    result = self.service.dynamic_reschedule_period(
                        day, period_name, now, f"{period_name}:early:{entry_at.isoformat()}"
                    )
                    if result.success:
                        current = self.service.repo.get_reservation(day, period_name) or {}
                        self.service.repo.update_dynamic_session(
                            day, period_name,
                            status="entered",
                            current_start=self._reservation_datetime(day, current.get("start")),
                            current_end=self._reservation_datetime(day, current.get("end")),
                            round_index=int(session.get("round_index") or 0) + 1,
                            entered_at=entry_at.isoformat(timespec="seconds"),
                            last_action_at=now.isoformat(timespec="seconds"),
                            last_checked_at=now.isoformat(timespec="seconds"),
                            message="检测到提前入馆，已按下一个半小时节点重约",
                        )
                        results[period_name] = "early_reschedule"
                    else:
                        self.service.repo.update_dynamic_session(
                            day, period_name,
                            status="error_hold",
                            message=f"动态重约未完成：{result.message or '提前入馆重约失败'}",
                        )
                        results[period_name] = "error_hold"
                    continue
                self.service.repo.update_dynamic_session(
                    day, period_name,
                    status="entered",
                    entered_at=entry_at.isoformat(timespec="seconds"),
                    last_checked_at=now.isoformat(timespec="seconds"),
                    message="门禁记录已确认入馆，停止迟到补偿",
                )
                results[period_name] = "entered"
                continue
            if remote_stage in {"reservation_first", "reservation_final", "entered"}:
                if remote_stage != "entered" and not self._remote_stage_due(
                    day, period_name, session, remote_stage, now
                ):
                    continue
                if reservation_error or reservation_records is None:
                    self._hold_remote_query(day, period_name, now, "我的预约查询失败，保留当前预约")
                    results[period_name] = "error_hold"
                    continue
                reservation = self.service.repo.get_reservation(day, period_name) or {}
                live_record = find_reservation_record(reservation_records, day, reservation)
                state = reservation_state(live_record) if live_record is not None else None
                retrying_rebook = (
                    reservation.get("status") in {"failed", "uncertain"}
                    and str(session.get("message") or "").startswith("动态重约未完成")
                )
                if retrying_rebook and remote_stage == "reservation_final" and state in {"reserved", "in_use"}:
                    # The earlier rebook submit actually landed on the site.
                    # Adopt it as a new round instead of cancelling the
                    # reservation that was just confirmed.
                    self._reconcile_confirmed_round(day, period_name, now, reservation, state)
                    results[period_name] = "reconciled"
                    continue
                if retrying_rebook and state is None and remote_stage == "reservation_final":
                    # The prior submit never appeared on the site.  Retry the
                    # exact saved interval while it is still in the future;
                    # only fall back to the next half-hour node once it passed.
                    preferred = None
                    if self._interval_still_future(day, reservation, now):
                        preferred = (reservation.get("start"), reservation.get("end"))
                    result = self.service.dynamic_reschedule_period(
                        day, period_name, now,
                        f"{period_name}:late:round{int(session.get('round_index') or 0) + 1}",
                        preferred_interval=preferred,
                    )
                    if result.success:
                        current = self.service.repo.get_reservation(day, period_name) or {}
                        self.service.repo.update_dynamic_session(
                            day,
                            period_name,
                            status="monitoring",
                            current_start=self._reservation_datetime(day, current.get("start")),
                            current_end=self._reservation_datetime(day, current.get("end")),
                            round_index=int(session.get("round_index") or 0) + 1,
                            last_action_at=now.isoformat(timespec="seconds"),
                            last_checked_at=now.isoformat(timespec="seconds"),
                            message="动态重约重试成功，已切换到新的预约轮次",
                        )
                        results[period_name] = "reschedule"
                    else:
                        self.service.repo.update_dynamic_session(
                            day,
                            period_name,
                            status="error_hold",
                            message=f"动态重约未完成：{result.message or '迟到动态重约失败'}",
                        )
                        results[period_name] = "error_hold"
                    continue
                if state is None:
                    self._hold_remote_query(day, period_name, now, "我的预约未匹配当前预约，稍后将重开窗口重查")
                    results[period_name] = "error_hold"
                    continue
                if state == "in_use":
                    self._mark_reservation_stage(day, period_name, remote_stage, now, session)
                    self._sync_remote_display(day, period_name, reservation, live_record)
                    leave_decision, away_begin = self._temporary_leave_decision(
                        day, reservation, live_record, now
                    )
                    if leave_decision is not None and leave_decision.kind == DecisionKind.AWAY_CANCEL:
                        operation_key = f"{period_name}:away:{away_begin.isoformat()}"
                        cancel_result = self.service.dynamic_cancel_period(
                            day, period_name, operation_key, leave_decision.reason
                        )
                        if cancel_result.success:
                            self.service.repo.update_dynamic_session(
                                day, period_name,
                                status="away_cancelled",
                                last_action_at=now.isoformat(timespec="seconds"),
                                last_checked_at=now.isoformat(timespec="seconds"),
                                cancel_count=self.service.repo.dynamic_cancellation_count(day),
                                message=leave_decision.reason,
                            )
                            results[period_name] = "away_cancel"
                        else:
                            self.service.repo.update_dynamic_session(
                                day, period_name,
                                status="error_hold",
                                last_checked_at=now.isoformat(timespec="seconds"),
                                message=cancel_result.message or leave_decision.reason,
                            )
                            results[period_name] = "error_hold"
                        continue
                    self.service.repo.update_dynamic_session(
                        day, period_name,
                        status="entered",
                        entered_at=(session.get("entered_at") or now.isoformat(timespec="seconds")),
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message=(
                            leave_decision.reason
                            if leave_decision is not None
                            else "我的预约状态已确认履约中，停止迟到补偿"
                        ),
                    )
                    results[period_name] = (
                        "error_hold"
                        if leave_decision is not None and leave_decision.kind == DecisionKind.AWAY_HOLD
                        else "entered"
                    )
                    continue
                if state in {"completed", "missed", "cancelled"}:
                    self._mark_reservation_stage(day, period_name, remote_stage, now, session)
                    if reservation.get("status") != state:
                        self.service.repo.save_reservation(
                            day,
                            period_name,
                            state,
                            reservation.get("start", ""),
                            reservation.get("end", ""),
                            reservation.get("room", ""),
                            reservation.get("seat", ""),
                            f"‘我的预约’状态为{state}，已同步到本地",
                        )
                    self.service.repo.update_dynamic_session(
                        day, period_name,
                        status=state,
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message=f"我的预约状态为{state}，停止动态调整",
                    )
                    results[period_name] = state
                    continue
                self._mark_reservation_stage(day, period_name, remote_stage, now, session)
                if remote_stage != "reservation_final" or not self._late_checks_complete(day, period_name):
                    self.service.repo.update_dynamic_session(
                        day, period_name,
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message="已完成预约状态检查，等待下一次检查",
                    )
                    results[period_name] = "wait"
                    continue
                result = self.service.dynamic_reschedule_period(
                    day, period_name, now,
                    f"{period_name}:late:round{int(session.get('round_index') or 0) + 1}",
                )
                if result.success:
                    current = self.service.repo.get_reservation(day, period_name) or {}
                    next_start = self._reservation_datetime(day, current.get("start"))
                    next_end = self._reservation_datetime(day, current.get("end"))
                    self.service.repo.update_dynamic_session(
                        day, period_name,
                        status="monitoring",
                        current_start=next_start,
                        current_end=next_end,
                        round_index=int(session.get("round_index") or 0) + 1,
                        action_index=next_late_action_index(
                            now,
                            datetime.fromisoformat(session["anchor_start"]),
                            int(session["action_index"]),
                            self.settings.dynamic_boundary_lead_minutes,
                            self.settings.dynamic_late_reschedule_minutes,
                            self.settings.dynamic_before_minutes,
                            self.settings.dynamic_after_minutes,
                        ),
                        last_action_at=now.isoformat(timespec="seconds"),
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message="三次检查均未履约，已按下一个半小时节点重约",
                    )
                    results[period_name] = "reschedule"
                else:
                    self.service.repo.update_dynamic_session(
                        day, period_name,
                        status="error_hold",
                        message=f"动态重约未完成：{result.message or '迟到动态重约失败'}",
                    )
                    results[period_name] = "error_hold"
            if remote_stage == "finalize":
                if not self._remote_stage_due(day, period_name, session, remote_stage, now):
                    continue
                if reservation_error or reservation_records is None:
                    self._hold_remote_query(day, period_name, now, "最终核验查询失败，稍后将重开窗口重查")
                    results[period_name] = "error_hold"
                    continue
                reservation = self.service.repo.get_reservation(day, period_name) or {}
                live_record = find_reservation_record(reservation_records, day, reservation)
                state = reservation_state(live_record) if live_record is not None else None
                if state == "in_use":
                    self.service.repo.update_dynamic_session(
                        day,
                        period_name,
                        status="entered",
                        entered_at=(session.get("entered_at") or now.isoformat(timespec="seconds")),
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message="最终核验确认履约中，停止动态调整",
                    )
                    results[period_name] = "entered"
                    continue
                if state in {"completed", "missed", "cancelled"}:
                    if reservation.get("status") != state:
                        self.service.repo.save_reservation(
                            day,
                            period_name,
                            state,
                            reservation.get("start", ""),
                            reservation.get("end", ""),
                            reservation.get("room", ""),
                            reservation.get("seat", ""),
                            f"最终核验确认预约状态为{state}，已同步到本地",
                        )
                    self.service.repo.update_dynamic_session(
                        day,
                        period_name,
                        status=state,
                        last_checked_at=now.isoformat(timespec="seconds"),
                        message=f"最终核验确认预约状态为{state}",
                    )
                    results[period_name] = state
                    continue
                if state != "reserved":
                    self._hold_remote_query(day, period_name, now, "最终核验未确认预约状态，稍后将重开窗口重查")
                    results[period_name] = "error_hold"
                    continue
                result = self.service.dynamic_cancel_period(
                    day,
                    period_name,
                    f"{period_name}:cutoff:round{int(session.get('round_index') or 0)}",
                    "动态窗口结束前未确认履约，已取消当前预约",
                )
                self.service.repo.update_dynamic_session(
                    day,
                    period_name,
                    status="cancelled" if result.success else "error_hold",
                    last_action_at=now.isoformat(timespec="seconds"),
                    last_checked_at=now.isoformat(timespec="seconds"),
                    cancel_count=self.service.repo.dynamic_cancellation_count(day),
                    message=result.message or "最终取消未完成",
                )
                results[period_name] = "cancel_unentered" if result.success else "error_hold"
        return results

    def _mark_reservation_stage(
        self,
        day: str,
        period_name: str,
        stage: str,
        now: datetime,
        session: dict,
    ) -> None:
        if stage in {"reservation_first", "reservation_final"}:
            self.service.repo.mark_dynamic_check(
                day,
                period_name,
                stage,
                now.isoformat(timespec="seconds"),
                reservation_start=session.get("current_start"),
            )

    def _late_checks_complete(self, day: str, period_name: str, session: dict | None = None) -> bool:
        session = session or self.service.repo.get_dynamic_session(day, period_name) or {}
        return all(
            self.service.repo.dynamic_check_at(
                day,
                period_name,
                stage,
                reservation_start=session.get("current_start"),
            )
            for stage in self._required_check_stages()
        )

    def _hold_remote_query(self, day: str, period_name: str, now: datetime, message: str) -> None:
        self.service.repo.update_dynamic_session(
            day, period_name,
            status="error_hold",
            last_checked_at=now.isoformat(timespec="seconds"),
            message=message,
        )

    def _remote_stage_due(
        self,
        day: str,
        period_name: str,
        session: dict,
        stage: str,
        now: datetime,
    ) -> bool:
        if stage == "entered":
            return True
        if stage == "finalize":
            try:
                window_end = datetime.fromisoformat(session["window_end"])
            except (TypeError, ValueError):
                return False
            return finalize_start(window_end) <= now < window_end
        if (
            stage == "reservation_final"
            and str(session.get("message") or "").startswith("动态重约未完成")
            and self._late_checks_complete(day, period_name, session)
        ):
            return True
        if self.service.repo.dynamic_check_at(
            day,
            period_name,
            stage,
            reservation_start=session.get("current_start"),
        ):
            return False
        try:
            current_start = datetime.fromisoformat(
                session.get("current_start") or session["anchor_start"]
            )
            checkpoint = reservation_checkpoints(
                current_start,
                round_index=int(session.get("round_index") or 0),
                access_before_minutes=self.settings.dynamic_access_check_before_minutes,
                reservation_first_before_minutes=self.settings.dynamic_reservation_first_check_before_minutes,
                reservation_final_before_minutes=self.settings.dynamic_reservation_final_check_before_minutes,
            ).get(stage)
            if checkpoint is None:
                return False
        except (TypeError, ValueError):
            return False
        return now >= checkpoint

    def prepare(self, day: str, target_period: str | None = None) -> dict:
        """Create restart-safe sessions without querying the access provider."""
        enabled = [
            name for name, period in self.settings.periods.items()
            if getattr(period, "enabled", True)
        ]
        if not self.settings.dynamic_compensation_enabled or len(enabled) > self.settings.dynamic_max_periods:
            return {"status": "disabled"}
        if target_period is not None:
            enabled = [name for name in enabled if name == target_period]
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
        target_period: str | None = None,
    ) -> bool:
        if self.has_pending_commands(day):
            return True
        entry_at = first_entry_time(records or [], day)
        for session in self.service.repo.dynamic_sessions(day):
            if target_period is not None and session["period"] != target_period:
                continue
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
            reservation = self.service.repo.get_reservation(day, session["period"])
            if reservation_records is not None and reservation and reservation.get("status") in {"failed", "uncertain"}:
                confirmed = find_confirmed_reservation(reservation_records, day, reservation)
                if confirmed is not None and reservation_state(confirmed) in {"reserved", "in_use"}:
                    # tick() will reconcile this result without rebuilding the
                    # browser session or issuing another reservation.
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

    def next_poll_delay(self, day: str, now: datetime, target_period: str | None = None):
        delays = []
        if self.has_pending_commands(day):
            delays.append(timedelta(seconds=self.settings.dynamic_boundary_poll_seconds))
        for session in self.service.repo.dynamic_sessions(day):
            if target_period is not None and session["period"] != target_period:
                continue
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
            logging.getLogger(__name__).info(
                "机器人命令执行%s：%s",
                "成功" if response.get("ok") else "失败",
                compact_message(text, limit=200),
            )
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
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "企业微信命令结果通知失败：%s：%s",
                type(exc).__name__,
                compact_message(exc),
            )

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

    def _reservation_datetime(self, day: str, value: str | None) -> str | None:
        try:
            return datetime.combine(date.fromisoformat(day), parse_hhmm(value)).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            return None

    def _interval_still_future(self, day: str, record: dict | None, now: datetime) -> bool:
        """True when a saved reservation interval has not started yet."""
        if not record:
            return False
        try:
            start = datetime.combine(date.fromisoformat(day), parse_hhmm(record.get("start")))
        except (TypeError, ValueError):
            return False
        return start > now

    def _sync_remote_display(self, day: str, period_name: str, reservation: dict, live_record) -> None:
        """Mirror the site's room/seat display onto the local row when it drifts.

        Matching has already identified the live row as this account's own
        booking at the saved interval, so display-only changes are safe to
        adopt; the interval and status stay untouched.
        """
        if live_record is None or not reservation or reservation.get("status") != "reserved":
            return
        remote_seat = _extract_seat(live_record)
        remote_room = _extract_room(live_record)
        local_seat = _normalize_seat(str(reservation.get("seat") or ""))
        local_room = _normalize_room(str(reservation.get("room") or ""))
        seat_same = (not remote_seat and not local_seat) or _seats_match(remote_seat, local_seat)
        room_same = not remote_room or not local_room or _room_matches(local_room, remote_room)
        if seat_same and room_same:
            return
        self.service.repo.save_reservation(
            day,
            period_name,
            reservation.get("status", "reserved"),
            reservation.get("start", ""),
            reservation.get("end", ""),
            remote_room or reservation.get("room", ""),
            remote_seat or reservation.get("seat", ""),
            "已同步‘我的预约’的位置显示变化",
        )

    def _reconcile_confirmed_round(
        self,
        day: str,
        period_name: str,
        now: datetime,
        reservation: dict,
        state: str,
    ) -> None:
        """Adopt a rebook submit that the site confirmed after uncertainty."""
        self.service.repo.save_reservation(
            day,
            period_name,
            "reserved",
            reservation.get("start", ""),
            reservation.get("end", ""),
            reservation.get("room", ""),
            reservation.get("seat", ""),
            "已从‘我的预约’确认动态重约成功",
        )
        current = self.service.repo.get_reservation(day, period_name) or {}
        changes = {
            "current_start": self._reservation_datetime(day, current.get("start")),
            "current_end": self._reservation_datetime(day, current.get("end")),
            "round_index": int(self.service.repo.get_dynamic_session(day, period_name).get("round_index") or 0) + 1,
            "last_checked_at": now.isoformat(timespec="seconds"),
            "message": "动态重约结果已确认，进入新一轮检查",
        }
        if state == "in_use":
            changes.update(
                status="entered",
                entered_at=now.isoformat(timespec="seconds"),
                message="动态重约结果已确认履约中，停止迟到补偿",
            )
        else:
            changes["status"] = "monitoring"
        self.service.repo.update_dynamic_session(day, period_name, **changes)
        self.service.notify_reconciled_reservation(day, period_name, current)

    def _ensure_session(self, day: str, period_name: str, now: datetime | None = None):
        now = now or datetime.now()
        session = self.service.repo.get_dynamic_session(day, period_name)
        if session is not None:
            if session["status"] in {"monitoring", "entered"}:
                session = self._sync_current_round(day, period_name, session)
                return session
            if self._can_retry_failed_reschedule(day, session, now):
                record_status = (self.service.repo.get_reservation(day, period_name) or {}).get("status")
                message = (
                    "动态重约未完成，仍在窗口内继续重试"
                    if record_status in {"failed", "uncertain"}
                    else "查询或检查中断，仍在窗口内继续重试"
                )
                self.service.repo.update_dynamic_session(
                    day,
                    period_name,
                    status="monitoring",
                    message=message,
                )
                return self.service.repo.get_dynamic_session(day, period_name)
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

    def _sync_current_round(self, day: str, period_name: str, session: dict) -> dict:
        """Recover a round written locally before a process interruption."""
        reservation = self.service.repo.get_reservation(day, period_name)
        if not reservation or reservation.get("status") != "reserved":
            return session
        current_start = self._reservation_datetime(day, reservation.get("start"))
        current_end = self._reservation_datetime(day, reservation.get("end"))
        if current_start is None or current_end is None:
            return session
        if current_start == session.get("current_start") and current_end == session.get("current_end"):
            return session
        try:
            anchor_start = datetime.fromisoformat(session["anchor_start"])
            next_start = datetime.fromisoformat(current_start)
            interval = int(self.settings.dynamic_late_reschedule_minutes)
            delta_minutes = int((next_start - anchor_start).total_seconds() // 60)
            inferred_round = max(1, delta_minutes // interval) if delta_minutes > 0 and interval > 0 else 1
        except (TypeError, ValueError):
            inferred_round = max(1, int(session.get("round_index") or 0))
        self.service.repo.update_dynamic_session(
            day,
            period_name,
            current_start=current_start,
            current_end=current_end,
            round_index=max(int(session.get("round_index") or 0), inferred_round),
            message="已根据本地预约记录恢复当前动态预约轮次",
        )
        return self.service.repo.get_dynamic_session(day, period_name) or session

    def _can_retry_failed_reschedule(self, day: str, session: dict, now: datetime) -> bool:
        """Reopen any transient hold while its relevant safety window is open.

        No message whitelist: a hold must never become a permanent stop while
        the reservation window is still open.  Holds with unusable anchors
        (empty anchor times) stay closed because their retry end cannot be
        parsed.
        """
        if session.get("status") != "error_hold":
            return False
        record = self.service.repo.get_reservation(day, session["period"])
        if not record:
            return False
        try:
            retry_end = session["anchor_end"] if session.get("entered_at") else session["window_end"]
            return now < datetime.fromisoformat(retry_end)
        except (TypeError, ValueError):
            return False

    def _reconcile_action_progress(
        self,
        day: str,
        period_name: str,
        session: dict,
        reservation: dict | None,
        anchor: datetime,
    ) -> None:
        """Recover the next action after a crash between reserve and session save."""
        if not reservation or reservation.get("status") != "reserved":
            return
        try:
            start = parse_hhmm(reservation.get("start"))
        except (TypeError, ValueError):
            return
        anchor_minutes = anchor.hour * 60 + anchor.minute
        start_minutes = start.hour * 60 + start.minute
        interval = int(self.settings.dynamic_late_reschedule_minutes)
        if start_minutes <= anchor_minutes or interval <= 0:
            return
        delta = start_minutes - anchor_minutes
        if delta % interval:
            return
        inferred_index = delta // interval
        current_index = int(session["action_index"])
        if inferred_index <= current_index:
            return
        self.service.repo.update_dynamic_session(
            day,
            period_name,
            action_index=inferred_index,
            message="已根据本地动态预约时间恢复重约进度",
        )

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


def _has_same_interval_live_record(records: list[dict] | None, day: str, expected: dict) -> bool:
    """Return true when the site still shows a live row for this period's time."""
    try:
        expected_start = parse_hhmm(expected.get("start")).strftime("%H:%M")
        expected_end = parse_hhmm(expected.get("end")).strftime("%H:%M")
    except (TypeError, ValueError):
        return False
    expected_day = _normalize_day(day)
    for item in records or []:
        if not isinstance(item, dict) or reservation_state(item) not in {"reserved", "in_use"}:
            continue
        item_day = _normalize_day(str(item.get("date") or item.get("day") or item.get("onDate") or ""))
        item_start = str(item.get("begin") or item.get("start") or item.get("startTime") or item.get("beginTime") or "")
        item_end = str(item.get("end") or item.get("endTime") or item.get("finishTime") or "")
        if item_day != expected_day:
            continue
        try:
            actual_start = parse_hhmm(item_start).strftime("%H:%M")
            actual_end = parse_hhmm(item_end).strftime("%H:%M")
        except (TypeError, ValueError):
            continue
        if actual_start == expected_start and actual_end == expected_end:
            return True
    return False


def _reservation_start_is_after_anchor(reservation: dict | None, anchor: datetime) -> bool:
    """Recognize a locally persisted dynamic rebooking after a restart."""
    if not reservation:
        return False
    try:
        start = parse_hhmm(reservation.get("start"))
    except (TypeError, ValueError):
        return False
    return (start.hour, start.minute) > (anchor.hour, anchor.minute)


def _normalize_day(value: str) -> str:
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(value or ""))
    if not match:
        return ""
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return ""


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
