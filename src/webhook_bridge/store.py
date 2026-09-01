from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Delivery:
    id: int
    event_id: int
    source: str
    external_id: str
    payload: dict[str, Any]
    attempts: int


class EventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    received_at REAL NOT NULL,
                    UNIQUE(source, external_id)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES events(id),
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL,
                    last_error TEXT,
                    response_code INTEGER,
                    delivered_at REAL,
                    UNIQUE(event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_deliveries_due
                    ON deliveries(status, next_attempt_at);
                """
            )

    def register_event(
        self, source: str, external_id: str, payload: dict[str, Any], *, now: float | None = None
    ) -> tuple[int, bool]:
        created_at = time.time() if now is None else now
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with closing(self._connect()) as db, db:
            try:
                cursor = db.execute(
                    "INSERT INTO events(source, external_id, payload, received_at) VALUES(?,?,?,?)",
                    (source, external_id, encoded, created_at),
                )
                event_id = int(cursor.lastrowid)
                db.execute(
                    "INSERT INTO deliveries(event_id, next_attempt_at) VALUES(?,?)",
                    (event_id, created_at),
                )
                return event_id, True
            except sqlite3.IntegrityError:
                row = db.execute(
                    "SELECT id FROM events WHERE source=? AND external_id=?",
                    (source, external_id),
                ).fetchone()
                if row is None:
                    raise
                return int(row["id"]), False

    def due_deliveries(self, *, now: float | None = None, limit: int = 50) -> list[Delivery]:
        due_at = time.time() if now is None else now
        with closing(self._connect()) as db, db:
            rows = db.execute(
                """
                SELECT d.id, d.event_id, d.attempts, e.source, e.external_id, e.payload
                FROM deliveries d JOIN events e ON e.id=d.event_id
                WHERE d.status='pending' AND d.next_attempt_at<=?
                ORDER BY d.next_attempt_at, d.id LIMIT ?
                """,
                (due_at, limit),
            ).fetchall()
        return [
            Delivery(
                id=int(row["id"]),
                event_id=int(row["event_id"]),
                source=str(row["source"]),
                external_id=str(row["external_id"]),
                payload=json.loads(row["payload"]),
                attempts=int(row["attempts"]),
            )
            for row in rows
        ]

    def mark_delivered(self, delivery_id: int, response_code: int, *, now: float | None = None) -> None:
        delivered_at = time.time() if now is None else now
        with closing(self._connect()) as db, db:
            db.execute(
                """UPDATE deliveries SET status='delivered', attempts=attempts+1,
                response_code=?, delivered_at=?, last_error=NULL WHERE id=?""",
                (response_code, delivered_at, delivery_id),
            )

    def mark_failed(
        self,
        delivery_id: int,
        error: str,
        *,
        next_attempt_at: float,
        terminal: bool = False,
        response_code: int | None = None,
    ) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                """UPDATE deliveries SET status=?, attempts=attempts+1,
                next_attempt_at=?, last_error=?, response_code=? WHERE id=?""",
                ("dead" if terminal else "pending", next_attempt_at, error[:1000], response_code, delivery_id),
            )

    def stats(self) -> dict[str, int]:
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS count FROM deliveries GROUP BY status"
            ).fetchall()
            total = db.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
        result = {"events": int(total), "pending": 0, "delivered": 0, "dead": 0}
        result.update({str(row["status"]): int(row["count"]) for row in rows})
        return result
