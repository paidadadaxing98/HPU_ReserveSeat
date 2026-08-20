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


def _to_minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _from_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"
