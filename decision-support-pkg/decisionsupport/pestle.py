"""PESTLE Analysis — Political, Economic, Social, Technological, Legal, Environmental."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import List, Optional

CATEGORIES = ["political", "economic", "social", "technological", "legal", "environmental"]

CATEGORY_INFO = {
    "political": {
        "label": "Political",
        "description": "Government policies, regulations, political stability, trade agreements",
        "prompt": "What political factors could affect this decision or initiative?",
        "color_class": "primary",
        "text_color": "white",
        "icon": "P",
    },
    "economic": {
        "label": "Economic",
        "description": "Economic growth, inflation, interest rates, exchange rates, market conditions",
        "prompt": "What economic factors are relevant?",
        "color_class": "success",
        "text_color": "white",
        "icon": "E",
    },
    "social": {
        "label": "Social",
        "description": "Demographics, culture, lifestyle trends, workforce attitudes",
        "prompt": "What social or cultural factors should be considered?",
        "color_class": "info",
        "text_color": "dark",
        "icon": "S",
    },
    "technological": {
        "label": "Technological",
        "description": "Innovation, automation, R&D activity, technological change",
        "prompt": "What technological factors could have an impact?",
        "color_class": "warning",
        "text_color": "dark",
        "icon": "T",
    },
    "legal": {
        "label": "Legal",
        "description": "Laws, regulations, compliance requirements, intellectual property",
        "prompt": "What legal or regulatory factors apply?",
        "color_class": "danger",
        "text_color": "white",
        "icon": "L",
    },
    "environmental": {
        "label": "Environmental",
        "description": "Climate, sustainability, ecological impact, carbon footprint",
        "prompt": "What environmental or sustainability factors are relevant?",
        "color_class": "secondary",
        "text_color": "white",
        "icon": "E",
    },
}


class PestleDB:
    """SQLite-backed storage for PESTLE sessions."""

    def __init__(self, db_path: str = "pestle.db") -> None:
        self._path = db_path
        self._lock = Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connection()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pestle_sessions (
                    id          TEXT PRIMARY KEY,
                    title       TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status      TEXT NOT NULL DEFAULT 'setup',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pestle_items (
                    id           TEXT PRIMARY KEY,
                    session_id   TEXT NOT NULL REFERENCES pestle_sessions(id),
                    category     TEXT NOT NULL,
                    content      TEXT NOT NULL,
                    submitted_by TEXT NOT NULL DEFAULT 'anonymous',
                    created_at   TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_pestle_items_session ON pestle_items(session_id);
            """)
            conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- Sessions ----------------------------------------------------------

    def create_session(self, title: str, description: str = "") -> str:
        sid = uuid.uuid4().hex[:8]
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO pestle_sessions (id, title, description, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (sid, title, description, "setup", now, now),
                )
        return sid

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM pestle_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, include_archived: bool = False) -> List[dict]:
        with self._lock:
            if include_archived:
                rows = self._connection().execute(
                    "SELECT * FROM pestle_sessions ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = self._connection().execute(
                    "SELECT * FROM pestle_sessions WHERE status != 'archived' ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, session_id: str, status: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE pestle_sessions SET status = ?, updated_at = ? WHERE id = ?",
                    (status, self._now(), session_id),
                )

    def archive(self, session_id: str) -> None:
        self.set_status(session_id, "archived")

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM pestle_items WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM pestle_sessions WHERE id = ?", (session_id,))

    # -- Items -------------------------------------------------------------

    def add_item(self, session_id: str, category: str, content: str, submitted_by: str = "anonymous") -> str:
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category {category!r}")
        iid = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO pestle_items (id, session_id, category, content, submitted_by, created_at) VALUES (?,?,?,?,?,?)",
                    (iid, session_id, category, content, submitted_by, self._now()),
                )
        return iid

    def get_items(self, session_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM pestle_items WHERE session_id = ? ORDER BY category, created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_item(self, item_id: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM pestle_items WHERE id = ?", (item_id,))

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
