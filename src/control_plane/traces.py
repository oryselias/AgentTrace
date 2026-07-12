"""Trace persistence — metadata only, never unredacted prompts.

ponytail: Memory + SQLite for local/CI; swap to Postgres when Compose lands (same TraceStore Protocol).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol, runtime_checkable

from control_plane.schemas import TraceRecord


@runtime_checkable
class TraceStore(Protocol):
    def write(self, record: TraceRecord) -> None: ...

    def list_recent(self, *, limit: int = 50) -> list[TraceRecord]: ...


class MemoryTraceStore:
    def __init__(self) -> None:
        self._rows: list[TraceRecord] = []

    def write(self, record: TraceRecord) -> None:
        self._rows.append(record)

    def list_recent(self, *, limit: int = 50) -> list[TraceRecord]:
        if limit < 1:
            return []
        return list(reversed(self._rows[-limit:]))


class SqliteTraceStore:
    """Durable local store. Schema is TraceRecord JSON — portable to Postgres later."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS traces (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)"
        )
        self._conn.commit()

    def write(self, record: TraceRecord) -> None:
        self._conn.execute(
            "INSERT INTO traces (payload) VALUES (?)",
            (record.model_dump_json(),),
        )
        self._conn.commit()

    def list_recent(self, *, limit: int = 50) -> list[TraceRecord]:
        if limit < 1:
            return []
        rows = self._conn.execute(
            "SELECT payload FROM traces ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [TraceRecord.model_validate(json.loads(r[0])) for r in rows]

    def close(self) -> None:
        self._conn.close()
