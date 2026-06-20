"""
Six Thinking Hats framework.

Edward de Bono's parallel thinking method. The facilitator opens one hat at a time;
all participants contribute from that hat's perspective before moving on.

Hat colours and perspectives:
  white  — facts and information available
  red    — emotions, feelings, intuitions
  black  — caution, critical judgment, risks
  yellow — optimism, benefits, best case
  green  — creativity, alternatives, new ideas
  blue   — process control, summary, next steps

Workflow:
  setup → active (hats opened/closed one at a time) → complete
  (any state can be archived or deleted)

Storage: SQLite at <data_dir>/sixhats.db
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

DEFAULT_DB_PATH = str(Path.home() / ".decisionsupport" / "sixhats.db")

HATS_ORDER = ["white", "red", "black", "yellow", "green", "blue"]

HAT_INFO = {
    "white": {
        "label": "White Hat",
        "icon": "🤍",
        "perspective": "Facts & Information",
        "prompt": "What facts and data do we have? What information is missing?",
        "color_class": "secondary",
    },
    "red": {
        "label": "Red Hat",
        "icon": "❤️",
        "perspective": "Emotions & Intuition",
        "prompt": "What are your gut feelings and emotions about this? No justification needed.",
        "color_class": "danger",
    },
    "black": {
        "label": "Black Hat",
        "icon": "🖤",
        "perspective": "Caution & Critical Judgment",
        "prompt": "What could go wrong? What are the risks and weaknesses?",
        "color_class": "dark",
    },
    "yellow": {
        "label": "Yellow Hat",
        "icon": "💛",
        "perspective": "Optimism & Benefits",
        "prompt": "What are the benefits and value? What's the best case?",
        "color_class": "warning",
    },
    "green": {
        "label": "Green Hat",
        "icon": "💚",
        "perspective": "Creativity & New Ideas",
        "prompt": "What are new ideas, alternatives, or creative solutions?",
        "color_class": "success",
    },
    "blue": {
        "label": "Blue Hat",
        "icon": "💙",
        "perspective": "Process & Summary",
        "prompt": "What is our thinking process? What conclusions can we draw?",
        "color_class": "primary",
    },
}


class SixHatsDB:
    """SQLite-backed storage for Six Thinking Hats sessions."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        conn = self._connection()
        conn.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS sh_sessions (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                topic       TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'setup',
                current_hat TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sh_hats (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES sh_sessions(id),
                hat_color   TEXT NOT NULL,
                order_num   INTEGER NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                opened_at   TEXT,
                closed_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS sh_contributions (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES sh_sessions(id),
                hat_id      TEXT NOT NULL REFERENCES sh_hats(id),
                hat_color   TEXT NOT NULL,
                contributor TEXT NOT NULL DEFAULT 'anonymous',
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sh_hats_session         ON sh_hats(session_id);
            CREATE INDEX IF NOT EXISTS idx_sh_contributions_session ON sh_contributions(session_id);
            CREATE INDEX IF NOT EXISTS idx_sh_contributions_hat    ON sh_contributions(hat_id);
        """)
        conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- Sessions -------------------------------------------------------

    def create_session(self, title: str, description: str = "", topic: str = "") -> str:
        sid = uuid.uuid4().hex[:8]
        now = self._now()
        with self._lock:
            conn = self._connection()
            with conn:
                conn.execute(
                    "INSERT INTO sh_sessions (id, title, description, topic, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (sid, title, description, topic, now, now),
                )
                # Pre-create the 6 hat rows in order
                for i, color in enumerate(HATS_ORDER):
                    conn.execute(
                        "INSERT INTO sh_hats (id, session_id, hat_color, order_num) VALUES (?,?,?,?)",
                        (uuid.uuid4().hex[:8], sid, color, i),
                    )
        return sid

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM sh_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, status: Optional[str] = None, include_archived: bool = False) -> List[dict]:
        with self._lock:
            conn = self._connection()
            if status:
                rows = conn.execute(
                    "SELECT * FROM sh_sessions WHERE status = ? ORDER BY created_at DESC", (status,)
                ).fetchall()
            elif include_archived:
                rows = conn.execute("SELECT * FROM sh_sessions ORDER BY created_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sh_sessions WHERE status != 'archived' ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, session_id: str, status: str) -> None:
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE sh_sessions SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, session_id),
                )

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM sh_contributions WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM sh_hats WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM sh_sessions WHERE id = ?", (session_id,))

    # -- Hat management ------------------------------------------------

    def get_hats(self, session_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM sh_hats WHERE session_id = ? ORDER BY order_num",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_current_hat(self, session_id: str) -> Optional[dict]:
        """Return the currently open hat, if any."""
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM sh_hats WHERE session_id = ? AND status = 'open'",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def open_next_hat(self, session_id: str) -> Optional[dict]:
        """Open the next pending hat. Returns the opened hat dict, or None if all done."""
        now = self._now()
        with self._lock:
            conn = self._connection()
            # Ensure no hat is currently open
            open_hat = conn.execute(
                "SELECT id FROM sh_hats WHERE session_id = ? AND status = 'open'",
                (session_id,),
            ).fetchone()
            if open_hat:
                raise ValueError("A hat is already open. Close it before opening the next.")

            next_hat = conn.execute(
                "SELECT * FROM sh_hats WHERE session_id = ? AND status = 'pending' ORDER BY order_num LIMIT 1",
                (session_id,),
            ).fetchone()
            if next_hat is None:
                return None

            with conn:
                conn.execute(
                    "UPDATE sh_hats SET status = 'open', opened_at = ? WHERE id = ?",
                    (now, next_hat["id"]),
                )
                conn.execute(
                    "UPDATE sh_sessions SET current_hat = ?, status = 'active', updated_at = ? WHERE id = ?",
                    (next_hat["hat_color"], now, session_id),
                )
            return dict(next_hat)

    def close_current_hat(self, session_id: str) -> Optional[str]:
        """Close the open hat. Returns the closed hat color, or None if none was open."""
        now = self._now()
        with self._lock:
            conn = self._connection()
            open_hat = conn.execute(
                "SELECT * FROM sh_hats WHERE session_id = ? AND status = 'open'",
                (session_id,),
            ).fetchone()
            if open_hat is None:
                return None

            with conn:
                conn.execute(
                    "UPDATE sh_hats SET status = 'closed', closed_at = ? WHERE id = ?",
                    (now, open_hat["id"]),
                )
                # Check if all hats are now closed → auto-complete
                pending = conn.execute(
                    "SELECT COUNT(*) FROM sh_hats WHERE session_id = ? AND status != 'closed'",
                    (session_id,),
                ).fetchone()[0]
                new_status = "complete" if pending == 0 else "active"
                conn.execute(
                    "UPDATE sh_sessions SET current_hat = NULL, status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now, session_id),
                )
        return open_hat["hat_color"]

    # -- Contributions -------------------------------------------------

    def add_contribution(
        self,
        session_id: str,
        hat_id: str,
        hat_color: str,
        content: str,
        contributor: str = "anonymous",
    ) -> str:
        cid = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """INSERT INTO sh_contributions
                       (id, session_id, hat_id, hat_color, contributor, content, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (cid, session_id, hat_id, hat_color, contributor, content, self._now()),
                )
        return cid

    def get_contributions(self, session_id: str, hat_id: Optional[str] = None) -> List[dict]:
        with self._lock:
            conn = self._connection()
            if hat_id:
                rows = conn.execute(
                    "SELECT * FROM sh_contributions WHERE session_id = ? AND hat_id = ? ORDER BY created_at",
                    (session_id, hat_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sh_contributions WHERE session_id = ? ORDER BY created_at",
                    (session_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
