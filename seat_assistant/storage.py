import json
import sqlite3
from statistics import median
from pathlib import Path


class Repository:
    def __init__(self, path: str, account_id: str = "default"):
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.account_id = account_id
        self.db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, kind TEXT, period TEXT, value TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        self.db.execute("CREATE TABLE IF NOT EXISTS reservations (date TEXT, period TEXT, status TEXT, start TEXT, end TEXT, room TEXT, seat TEXT, message TEXT DEFAULT '', PRIMARY KEY(date, period))")
        self.db.execute("CREATE TABLE IF NOT EXISTS defaults (period TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS commands (request_id TEXT PRIMARY KEY, text TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        self.db.execute("CREATE TABLE IF NOT EXISTS scheduler_runs (date TEXT PRIMARY KEY, status TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        self.db.execute("CREATE TABLE IF NOT EXISTS successful_bookings (date TEXT NOT NULL, account_id TEXT NOT NULL, reservation_key TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(date, account_id, reservation_key))")
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
        self.db.commit()

    def _ensure_column(self, table: str, column: str, definition: str):
        columns = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def event(self, kind: str, period: str | None, value: str | None):
        self.db.execute("INSERT INTO events(kind, period, value) VALUES (?, ?, ?)", (kind, period, value))
        self.db.commit()

    def samples(self, period: str) -> list[str]:
        rows = self.db.execute("SELECT value FROM events WHERE kind IN ('arrival', 'delay') AND period=?", (period,)).fetchall()
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
            ") VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)",
            (
                self.account_id,
                status,
                int(bool(login_verified)),
                int(bool(home_verified)),
                int(bool(my_reservations_verified)),
                json.dumps(capabilities or {}, ensure_ascii=False),
                message,
            ),
        )
        self.db.commit()


def _to_minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _from_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"
