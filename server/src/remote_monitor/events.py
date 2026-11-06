"""Persistent event log (SQLite) - motion alarms, faces, sound alarms, recordings, viewers."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,
    kind    TEXT    NOT NULL,
    level   TEXT    NOT NULL,
    message TEXT    NOT NULL,
    snapshot TEXT,
    data    TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""


class EventLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def add(
        self,
        kind: str,
        message: str,
        level: str = "info",
        snapshot: str | None = None,
        data: dict | None = None,
        ts: float | None = None,
    ) -> dict:
        ts = time.time() if ts is None else ts
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events(ts, kind, level, message, snapshot, data) VALUES (?,?,?,?,?,?)",
                (ts, kind, level, message, snapshot, json.dumps(data) if data else None),
            )
            self._db.commit()
            eid = cur.lastrowid
        return {
            "id": eid,
            "ts": ts,
            "kind": kind,
            "level": level,
            "message": message,
            "snapshot": snapshot,
            "data": data,
        }

    def list(self, limit: int = 100, before: int | None = None, kind: str | None = None) -> list[dict]:
        q = "SELECT id, ts, kind, level, message, snapshot, data FROM events"
        cond, args = [], []
        if before:
            cond.append("id < ?")
            args.append(before)
        if kind:
            cond.append("kind = ?")
            args.append(kind)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(min(limit, 500))
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [
            {
                "id": r[0],
                "ts": r[1],
                "kind": r[2],
                "level": r[3],
                "message": r[4],
                "snapshot": r[5],
                "data": json.loads(r[6]) if r[6] else None,
            }
            for r in rows
        ]

    def clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM events")
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
