"""Persistência SQLite: chamadas, turnos da transcrição e recados."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id          TEXT PRIMARY KEY,
    direction   TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    from_number TEXT NOT NULL DEFAULT '',
    to_number   TEXT NOT NULL DEFAULT '',
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    status      TEXT NOT NULL CHECK (status IN ('ringing', 'active', 'completed', 'failed')),
    summary     TEXT,
    purpose     TEXT,
    channel     TEXT,
    next_action TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started_at DESC);

CREATE TABLE IF NOT EXISTS turns (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    role    TEXT NOT NULL CHECK (role IN ('caller', 'agent')),
    text    TEXT NOT NULL,
    ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_call ON turns(call_id, id);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    call_id    TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    name       TEXT,
    phone      TEXT,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC);
"""

CALL_FIELDS = ("id", "direction", "from_number", "to_number", "started_at", "ended_at", "status", "summary")


def now_iso() -> str:
    """Timestamp ISO 8601 em UTC, ex.: 2026-10-14T15:04:05Z."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Database:
    """Wrapper fino e thread-safe (a API roda em threads diferentes nos testes)."""

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # --- calls -------------------------------------------------------------------
    def create_call(
        self,
        direction: str,
        from_number: str = "",
        to_number: str = "",
        status: str = "ringing",
        purpose: str | None = None,
        channel: str | None = None,
        call_id: str | None = None,
    ) -> str:
        call_id = call_id or str(uuid.uuid4())
        self._exec(
            "INSERT INTO calls (id, direction, from_number, to_number, started_at, status, purpose, channel)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (call_id, direction, from_number or "", to_number or "", now_iso(), status, purpose, channel),
        )
        return call_id

    def get_call_row(self, call_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM calls WHERE id = ?", (call_id,))
        return dict(rows[0]) if rows else None

    def update_call(self, call_id: str, **fields: Any) -> None:
        allowed = {"status", "ended_at", "summary", "channel", "next_action", "from_number", "to_number"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"campos inválidos: {bad}")
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE calls SET {cols} WHERE id = ?", (*fields.values(), call_id))

    def finish_call(self, call_id: str, status: str = "completed") -> None:
        """Encerra a chamada se ainda não estiver encerrada."""
        self._exec(
            "UPDATE calls SET status = ?, ended_at = COALESCE(ended_at, ?) WHERE id = ? AND status IN ('ringing', 'active')",
            (status, now_iso(), call_id),
        )

    def list_calls(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._query(
            f"SELECT {', '.join(CALL_FIELDS)} FROM calls ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def get_call(self, call_id: str) -> dict[str, Any] | None:
        rows = self._query(f"SELECT {', '.join(CALL_FIELDS)} FROM calls WHERE id = ?", (call_id,))
        if not rows:
            return None
        call = dict(rows[0])
        call["transcript"] = self.get_transcript(call_id)
        return call

    def count_active(self) -> int:
        return self._query("SELECT COUNT(*) AS n FROM calls WHERE status = 'active'")[0]["n"]

    # --- transcript --------------------------------------------------------------
    def add_turn(self, call_id: str, role: str, text: str) -> None:
        text = text.strip()
        if text:
            self._exec(
                "INSERT INTO turns (call_id, role, text, ts) VALUES (?, ?, ?, ?)",
                (call_id, role, text, now_iso()),
            )

    def get_transcript(self, call_id: str) -> list[dict[str, Any]]:
        rows = self._query("SELECT role, text, ts FROM turns WHERE call_id = ? ORDER BY id", (call_id,))
        return [dict(r) for r in rows]

    # --- recados -----------------------------------------------------------------
    def add_message(self, call_id: str, message: str, name: str | None = None, phone: str | None = None) -> str:
        msg_id = str(uuid.uuid4())
        self._exec(
            "INSERT INTO messages (id, call_id, name, phone, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, call_id, name or None, phone or None, message, now_iso()),
        )
        return msg_id

    def list_messages(self, limit: int = 200, call_id: str | None = None) -> list[dict[str, Any]]:
        if call_id:
            rows = self._query(
                "SELECT id, call_id, name, phone, message, created_at FROM messages WHERE call_id = ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (call_id, limit),
            )
        else:
            rows = self._query(
                "SELECT id, call_id, name, phone, message, created_at FROM messages"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
