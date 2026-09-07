import json
import sqlite3
from statistics import median
from datetime import datetime
from pathlib import Path


class Repository:
    def __init__(self, path: str, account_id: str = "default"):
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout = 5000")
        self.account_id = account_id
        self.db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, kind TEXT, period TEXT, value TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        self.db.execute("CREATE TABLE IF NOT EXISTS reservations (date TEXT, period TEXT, status TEXT, start TEXT, end TEXT, room TEXT, seat TEXT, message TEXT DEFAULT '', PRIMARY KEY(date, period))")
        self.db.execute("CREATE TABLE IF NOT EXISTS defaults (period TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS commands (request_id TEXT PRIMARY KEY, text TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS bot_commands ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL, "
            "day TEXT NOT NULL, sender TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
            "response TEXT NOT NULL DEFAULT '{}', received_at TEXT DEFAULT CURRENT_TIMESTAMP, "
            "claimed_at TEXT, handled_at TEXT"
            ")"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS scheduler_runs (date TEXT PRIMARY KEY, status TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        self.db.execute("CREATE TABLE IF NOT EXISTS successful_bookings (date TEXT NOT NULL, account_id TEXT NOT NULL, reservation_key TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(date, account_id, reservation_key))")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS dynamic_sessions ("
            "date TEXT NOT NULL, period TEXT NOT NULL, anchor_start TEXT NOT NULL, anchor_end TEXT NOT NULL, "
            "current_start TEXT, current_end TEXT, round_index INTEGER NOT NULL DEFAULT 0, "
            "window_start TEXT NOT NULL, window_end TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'monitoring', "
            "action_index INTEGER NOT NULL DEFAULT 0, entered_at TEXT, cancel_count INTEGER NOT NULL DEFAULT 0, "
            "last_action_at TEXT, last_checked_at TEXT, message TEXT NOT NULL DEFAULT '', "
            "PRIMARY KEY(date, period))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS dynamic_remote_checks ("
            "date TEXT NOT NULL, account_id TEXT NOT NULL, period TEXT NOT NULL, reservation_start TEXT NOT NULL, "
            "stage TEXT NOT NULL, checked_at TEXT NOT NULL, "
            "PRIMARY KEY(date, account_id, period, reservation_start, stage))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS dynamic_cancellations ("
            "date TEXT NOT NULL, account_id TEXT NOT NULL, operation_key TEXT NOT NULL, "
            "period TEXT NOT NULL DEFAULT '', start TEXT NOT NULL DEFAULT '', end TEXT NOT NULL DEFAULT '', "
            "room TEXT NOT NULL DEFAULT '', seat TEXT NOT NULL DEFAULT '', "
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(date, account_id, operation_key))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS room_round_robin ("
            "account_id TEXT NOT NULL, library TEXT NOT NULL, floor TEXT NOT NULL, "
            "next_index INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account_id, library, floor)"
            ")"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS account_initialization ("
            "account_id TEXT PRIMARY KEY, status TEXT NOT NULL, login_verified INTEGER NOT NULL DEFAULT 0, "
            "home_verified INTEGER NOT NULL DEFAULT 0, my_reservations_verified INTEGER NOT NULL DEFAULT 0, "
            "capabilities TEXT NOT NULL DEFAULT '{}', last_verified_at TEXT, message TEXT NOT NULL DEFAULT ''"
            ")"
        )
        self._ensure_column("reservations", "message", "TEXT DEFAULT ''")
        self._ensure_column("dynamic_sessions", "current_start", "TEXT")
        self._ensure_column("dynamic_sessions", "current_end", "TEXT")
        self._ensure_column("dynamic_sessions", "round_index", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("dynamic_cancellations", "period", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("dynamic_cancellations", "start", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("dynamic_cancellations", "end", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("dynamic_cancellations", "room", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("dynamic_cancellations", "seat", "TEXT NOT NULL DEFAULT ''")
        self._migrate_dynamic_remote_checks()
        self.db.execute(
            "UPDATE dynamic_sessions SET current_start=anchor_start WHERE current_start IS NULL OR current_start=''"
        )
        self.db.execute(
            "UPDATE dynamic_sessions SET current_end=anchor_end WHERE current_end IS NULL OR current_end=''"
        )
        self.db.commit()

    def _ensure_column(self, table: str, column: str, definition: str):
        columns = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _migrate_dynamic_remote_checks(self):
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(dynamic_remote_checks)")}
        if "reservation_start" in columns:
            return
        self.db.execute("ALTER TABLE dynamic_remote_checks RENAME TO dynamic_remote_checks_legacy")
        self.db.execute(
            "CREATE TABLE dynamic_remote_checks ("
            "date TEXT NOT NULL, account_id TEXT NOT NULL, period TEXT NOT NULL, reservation_start TEXT NOT NULL, "
            "stage TEXT NOT NULL, checked_at TEXT NOT NULL, "
            "PRIMARY KEY(date, account_id, period, reservation_start, stage))"
        )
        self.db.execute(
            "INSERT OR IGNORE INTO dynamic_remote_checks "
            "(date, account_id, period, reservation_start, stage, checked_at) "
            "SELECT old.date, old.account_id, old.period, "
            "COALESCE(NULLIF(session.current_start, ''), session.anchor_start, ''), "
            "old.stage, old.checked_at "
            "FROM dynamic_remote_checks_legacy old "
            "LEFT JOIN dynamic_sessions session ON session.date=old.date AND session.period=old.period"
        )

    def event(self, kind: str, period: str | None, value: str | None):
        self.db.execute("INSERT INTO events(kind, period, value) VALUES (?, ?, ?)", (kind, period, value))
        self.db.commit()

    def samples(self, period: str) -> list[str]:
        rows = self.db.execute("SELECT value FROM events WHERE kind='arrival' AND period=?", (period,)).fetchall()
        return [row[0] for row in rows]

    def events(self, kind: str | None = None, period: str | None = None) -> list[str]:
        query = "SELECT value FROM events"
        clauses = []
        values = []
        if kind is not None:
            clauses.append("kind=?")
            values.append(kind)
        if period is not None:
            clauses.append("period=?")
            values.append(period)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        return [row[0] for row in self.db.execute(query, values).fetchall()]

    def learned_default(self, period: str, fallback: str) -> str:
        values = self.samples(period)
        return _from_minutes(round(median(map(_to_minutes, values)))) if values else fallback

    def save_reservation(self, date, period, status, start, end, room="", seat="", message=""):
        self.db.execute(
            "REPLACE INTO reservations(date, period, status, start, end, room, seat, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (date, period, status, start, end, room, seat, message),
        )
        self.db.commit()

    def get_reservation(self, date, period):
        row = self.db.execute(
            "SELECT date, period, status, start, end, room, seat, message FROM reservations WHERE date=? AND period=?",
            (date, period),
        ).fetchone()
        if row is None:
            return None
        return dict(zip(("date", "period", "status", "start", "end", "room", "seat", "message"), row))

    def reservations(self, date):
        return self.db.execute("SELECT period,status,start,end,room,seat FROM reservations WHERE date=? ORDER BY period", (date,)).fetchall()

    def set_default(self, period: str, value: str):
        self.db.execute("REPLACE INTO defaults(period, value) VALUES (?, ?)", (period, value))
        self.db.commit()

    def default_override(self, period: str):
        row = self.db.execute("SELECT value FROM defaults WHERE period=?", (period,)).fetchone()
        return row[0] if row else None

    def record_command(self, request_id: str, text: str, response: dict) -> bool:
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO commands(request_id, text, response) VALUES (?, ?, ?)",
            (request_id, text, json.dumps(response, ensure_ascii=False)),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def get_command(self, request_id: str):
        row = self.db.execute("SELECT request_id, text, response, created_at FROM commands WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return None
        return {"request_id": row[0], "text": row[1], "response": row[2], "created_at": row[3]}

    def enqueue_bot_command(self, day: str, request_id: str, sender: str, text: str) -> bool:
        request_id = str(request_id or "").strip()
        sender = str(sender or "").strip()
        text = str(text or "").strip()
        day = str(day or "").strip()
        if not request_id or not sender or not text or not day:
            raise ValueError("机器人命令缺少 request_id、sender、text 或 day")
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO bot_commands(request_id, account_id, day, sender, text) VALUES (?, ?, ?, ?, ?)",
            (request_id, self.account_id, day, sender, text),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def pending_bot_commands(self, day: str, limit: int = 20) -> list[dict]:
        if limit <= 0:
            return []
        rows = self.db.execute(
            "SELECT request_id, account_id, day, sender, text, status, response, received_at, claimed_at, handled_at "
            "FROM bot_commands WHERE account_id=? AND day=? AND status='pending' ORDER BY id LIMIT ?",
            (self.account_id, str(day), int(limit)),
        ).fetchall()
        return [_bot_command_from_row(row) for row in rows]

    def claim_bot_command(self, request_id: str) -> bool:
        cursor = self.db.execute(
            "UPDATE bot_commands SET status='processing', claimed_at=? "
            "WHERE request_id=? AND account_id=? AND status='pending'",
            (datetime.now().isoformat(timespec="seconds"), str(request_id), self.account_id),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def complete_bot_command(self, request_id: str, status: str, response: dict) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("机器人命令完成状态必须是 completed 或 failed")
        self.db.execute(
            "UPDATE bot_commands SET status=?, response=?, handled_at=? "
            "WHERE request_id=? AND account_id=?",
            (
                status,
                json.dumps(response if isinstance(response, dict) else {}, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
                str(request_id),
                self.account_id,
            ),
        )
        self.db.commit()

    def get_bot_command(self, request_id: str):
        row = self.db.execute(
            "SELECT request_id, account_id, day, sender, text, status, response, received_at, claimed_at, handled_at "
            "FROM bot_commands WHERE request_id=? AND account_id=?",
            (str(request_id), self.account_id),
        ).fetchone()
        return _bot_command_from_row(row) if row is not None else None

    def scheduler_run(self, date):
        row = self.db.execute("SELECT date, status, summary FROM scheduler_runs WHERE date=?", (date,)).fetchone()
        if row is None:
            return None
        return {"date": row[0], "status": row[1], "summary": json.loads(row[2])}

    def save_scheduler_run(self, date, status: str, summary: dict):
        self.db.execute(
            "REPLACE INTO scheduler_runs(date, status, summary) VALUES (?, ?, ?)",
            (date, status, json.dumps(summary, ensure_ascii=False)),
        )
        self.db.commit()

    def record_successful_booking(self, date: str, reservation_key: str) -> bool:
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO successful_bookings(date, account_id, reservation_key) VALUES (?, ?, ?)",
            (date, self.account_id, reservation_key),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def successful_booking_count(self, date: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) FROM successful_bookings WHERE date=? AND account_id=?",
            (date, self.account_id),
        ).fetchone()
        return int(row[0])

    def save_dynamic_session(
        self,
        date: str,
        period: str,
        anchor_start: str,
        anchor_end: str,
        window_start: str,
        window_end: str,
        status: str = "monitoring",
        action_index: int = 0,
        entered_at: str | None = None,
        cancel_count: int = 0,
        last_action_at: str | None = None,
        last_checked_at: str | None = None,
        message: str = "",
        current_start: str | None = None,
        current_end: str | None = None,
        round_index: int = 0,
    ):
        self.db.execute(
            "REPLACE INTO dynamic_sessions("
            "date, period, anchor_start, anchor_end, current_start, current_end, round_index, window_start, window_end, status, action_index, "
            "entered_at, cancel_count, last_action_at, last_checked_at, message"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ,
            (
                date, period, anchor_start, anchor_end,
                current_start or anchor_start, current_end or anchor_end, int(round_index),
                window_start, window_end, status, int(action_index),
                entered_at, int(cancel_count), last_action_at, last_checked_at, message,
            ),
        )
        self.db.commit()

    def get_dynamic_session(self, date: str, period: str):
        row = self.db.execute(
            "SELECT date, period, anchor_start, anchor_end, current_start, current_end, round_index, window_start, window_end, status, action_index, "
            "entered_at, cancel_count, last_action_at, last_checked_at, message "
            "FROM dynamic_sessions WHERE date=? AND period=?",
            (date, period),
        ).fetchone()
        if row is None:
            return None
        return dict(zip((
            "date", "period", "anchor_start", "anchor_end", "current_start", "current_end", "round_index", "window_start", "window_end", "status",
            "action_index", "entered_at", "cancel_count", "last_action_at", "last_checked_at", "message",
        ), row))

    def dynamic_sessions(self, date: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT date, period, anchor_start, anchor_end, current_start, current_end, round_index, window_start, window_end, status, action_index, "
            "entered_at, cancel_count, last_action_at, last_checked_at, message "
            "FROM dynamic_sessions WHERE date=? ORDER BY period",
            (date,),
        ).fetchall()
        keys = (
            "date", "period", "anchor_start", "anchor_end", "current_start", "current_end", "round_index", "window_start", "window_end", "status",
            "action_index", "entered_at", "cancel_count", "last_action_at", "last_checked_at", "message",
        )
        return [dict(zip(keys, row)) for row in rows]

    def update_dynamic_session(self, date: str, period: str, **fields):
        allowed = {
            "status", "current_start", "current_end", "round_index", "action_index", "entered_at", "cancel_count", "last_action_at", "last_checked_at", "message",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"不支持的动态会话字段：{', '.join(sorted(unknown))}")
        if not fields:
            return
        assignments = ", ".join(f"{key}=?" for key in fields)
        values = [int(value) if key in {"action_index", "cancel_count", "round_index"} else value for key, value in fields.items()]
        values.extend((date, period))
        self.db.execute(
            f"UPDATE dynamic_sessions SET {assignments} WHERE date=? AND period=?",
            values,
        )
        self.db.commit()

    def _dynamic_check_start(self, date: str, period: str, reservation_start: str | None) -> str:
        value = reservation_start
        if value in (None, ""):
            row = self.db.execute(
                "SELECT current_start, anchor_start FROM dynamic_sessions WHERE date=? AND period=?",
                (date, period),
            ).fetchone()
            value = row[0] or row[1] if row else ""
        text = str(value or "").strip()
        if len(text) == 5 and text[2] == ":":
            return f"{date}T{text}:00"
        return text

    def dynamic_check_at(self, date: str, period: str, stage: str, reservation_start: str | None = None) -> str | None:
        reservation_start = self._dynamic_check_start(date, period, reservation_start)
        row = self.db.execute(
            "SELECT checked_at FROM dynamic_remote_checks "
            "WHERE date=? AND account_id=? AND period=? AND reservation_start=? AND stage=?",
            (date, self.account_id, period, reservation_start, stage),
        ).fetchone()
        return row[0] if row else None

    def mark_dynamic_check(
        self,
        date: str,
        period: str,
        stage: str,
        checked_at: str,
        reservation_start: str | None = None,
    ) -> None:
        reservation_start = self._dynamic_check_start(date, period, reservation_start)
        self.db.execute(
            "INSERT OR REPLACE INTO dynamic_remote_checks(date, account_id, period, reservation_start, stage, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (date, self.account_id, period, reservation_start, stage, checked_at),
        )
        self.db.commit()

    def record_dynamic_cancellation(self, date: str, operation_key: str, reservation: dict | None = None) -> bool:
        reservation = reservation or {}
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO dynamic_cancellations(date, account_id, operation_key, period, start, end, room, seat) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                date,
                self.account_id,
                operation_key,
                str(reservation.get("period") or ""),
                str(reservation.get("start") or ""),
                str(reservation.get("end") or ""),
                str(reservation.get("room") or ""),
                str(reservation.get("seat") or ""),
            ),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def has_dynamic_cancellation(self, date: str, operation_key: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM dynamic_cancellations WHERE date=? AND account_id=? AND operation_key=?",
            (date, self.account_id, operation_key),
        ).fetchone()
        return row is not None

    def dynamic_cancellation(self, date: str, operation_key: str):
        row = self.db.execute(
            "SELECT date, period, start, end, room, seat FROM dynamic_cancellations "
            "WHERE date=? AND account_id=? AND operation_key=?",
            (date, self.account_id, operation_key),
        ).fetchone()
        if row is None:
            return None
        return dict(zip(("date", "period", "start", "end", "room", "seat"), row))

    def dynamic_cancellation_count(self, date: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) FROM dynamic_cancellations WHERE date=? AND account_id=?",
            (date, self.account_id),
        ).fetchone()
        return int(row[0])

    def reset_day(self, date: str) -> dict[str, int]:
        """Clear local booking execution state for one account and date."""
        reservations = self.db.execute(
            "DELETE FROM reservations WHERE date=?", (date,)
        ).rowcount
        successful_bookings = self.db.execute(
            "DELETE FROM successful_bookings WHERE date=? AND account_id=?",
            (date, self.account_id),
        ).rowcount
        scheduler_runs = self.db.execute(
            "DELETE FROM scheduler_runs WHERE date=?", (date,)
        ).rowcount
        self.db.execute("DELETE FROM dynamic_sessions WHERE date=?", (date,))
        self.db.execute(
            "DELETE FROM dynamic_remote_checks WHERE date=? AND account_id=?",
            (date, self.account_id),
        )
        self.db.execute("DELETE FROM bot_commands WHERE account_id=? AND day=?", (self.account_id, date))
        self.db.execute(
            "DELETE FROM dynamic_cancellations WHERE date=? AND account_id=?",
            (date, self.account_id),
        )
        self.db.commit()
        return {
            "reservations": reservations,
            "successful_bookings": successful_bookings,
            "scheduler_runs": scheduler_runs,
        }

    def next_room_round_robin(self, library: str, floor: str, rooms: list[str]) -> str:
        candidates = [str(room).strip() for room in rooms if str(room).strip()]
        if not candidates:
            raise ValueError("当前楼层没有可轮询的阅览室")
        library = str(library or "").strip()
        floor = str(floor or "").strip()
        if not library:
            raise ValueError("阅览室轮询必须指定图书馆")
        if not floor:
            raise ValueError("阅览室轮询必须指定楼层")
        row = self.db.execute(
            "SELECT next_index FROM room_round_robin WHERE account_id=? AND library=? AND floor=?",
            (self.account_id, library, floor),
        ).fetchone()
        index = int(row[0]) if row else 0
        selected = candidates[index % len(candidates)]
        next_index = index + 1
        self.db.execute(
            "REPLACE INTO room_round_robin(account_id, library, floor, next_index) VALUES (?, ?, ?, ?)",
            (self.account_id, library, floor, next_index),
        )
        self.db.commit()
        return selected

    def initialization_state(self) -> dict:
        row = self.db.execute(
            "SELECT account_id, status, login_verified, home_verified, my_reservations_verified, "
            "capabilities, last_verified_at, message FROM account_initialization WHERE account_id=?",
            (self.account_id,),
        ).fetchone()
        if row is None:
            return {
                "account_id": self.account_id,
                "status": "pending",
                "login_verified": False,
                "home_verified": False,
                "my_reservations_verified": False,
                "capabilities": {},
                "last_verified_at": None,
                "message": "请先初始化账号后再运行预约",
            }
        try:
            capabilities = json.loads(row[5])
        except (TypeError, json.JSONDecodeError):
            capabilities = {}
        return {
            "account_id": row[0],
            "status": row[1],
            "login_verified": bool(row[2]),
            "home_verified": bool(row[3]),
            "my_reservations_verified": bool(row[4]),
            "capabilities": capabilities if isinstance(capabilities, dict) else {},
            "last_verified_at": row[6],
            "message": row[7],
        }

    def save_initialization_state(
        self,
        status: str,
        login_verified: bool,
        home_verified: bool,
        my_reservations_verified: bool,
        capabilities: dict | None = None,
        message: str = "",
    ):
        self.db.execute(
            "REPLACE INTO account_initialization("
            "account_id, status, login_verified, home_verified, my_reservations_verified, capabilities, last_verified_at, message"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.account_id,
                status,
                int(bool(login_verified)),
                int(bool(home_verified)),
                int(bool(my_reservations_verified)),
                json.dumps(capabilities or {}, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
                message,
            ),
        )
        self.db.commit()


def _to_minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _from_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _bot_command_from_row(row) -> dict:
    if row is None:
        return None
    command = dict(zip(
        ("request_id", "account_id", "day", "sender", "text", "status", "response", "received_at", "claimed_at", "handled_at"),
        row,
    ))
    try:
        command["response"] = json.loads(command["response"]) if command["response"] else {}
    except (TypeError, json.JSONDecodeError):
        command["response"] = {}
    return command
