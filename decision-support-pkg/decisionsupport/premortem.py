"""
Pre-Mortem decision support.

Imagines a future failure to surface hidden risks before committing to a plan.
Participants anonymously describe *how* the project failed; the facilitator
consolidates these into assessed risks with severity, likelihood, and mitigation.

Workflow:
  setup → brainstorming → reviewing → complete
  (any state can be archived or deleted)

Storage: SQLite at <data_dir>/premortem.db
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

DEFAULT_DB_PATH = str(Path.home() / ".decisionsupport" / "premortem.db")

STATUSES = ("setup", "brainstorming", "reviewing", "complete", "archived")

# 1 = very low, 5 = very high
SEVERITY_LABELS = {1: "Very Low", 2: "Low", 3: "Medium", 4: "High", 5: "Very High"}
LIKELIHOOD_LABELS = {1: "Unlikely", 2: "Possible", 3: "Likely", 4: "Very Likely", 5: "Almost Certain"}


class PreMortemDB:
    """SQLite-backed storage for Pre-Mortem sessions."""

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

            CREATE TABLE IF NOT EXISTS pm_sessions (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                plan_text   TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'setup',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pm_scenarios (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL REFERENCES pm_sessions(id),
                scenario_text TEXT NOT NULL,
                submitted_by TEXT NOT NULL DEFAULT 'anonymous',
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pm_risks (
                id               TEXT PRIMARY KEY,
                session_id       TEXT NOT NULL REFERENCES pm_sessions(id),
                scenario_id      TEXT REFERENCES pm_scenarios(id),
                risk_title       TEXT NOT NULL,
                risk_description TEXT NOT NULL DEFAULT '',
                severity         INTEGER NOT NULL DEFAULT 3,
                likelihood       INTEGER NOT NULL DEFAULT 3,
                mitigation       TEXT NOT NULL DEFAULT '',
                created_at       TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_pm_scenarios_session ON pm_scenarios(session_id);
            CREATE INDEX IF NOT EXISTS idx_pm_risks_session     ON pm_risks(session_id);
        """)
        conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- Sessions -------------------------------------------------------

    def create_session(self, title: str, description: str = "", plan_text: str = "") -> str:
        sid = uuid.uuid4().hex[:8]
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO pm_sessions (id, title, description, plan_text, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (sid, title, description, plan_text, now, now),
                )
        return sid

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM pm_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, status: Optional[str] = None, include_archived: bool = False) -> List[dict]:
        with self._lock:
            conn = self._connection()
            if status:
                rows = conn.execute(
                    "SELECT * FROM pm_sessions WHERE status = ? ORDER BY created_at DESC", (status,)
                ).fetchall()
            elif include_archived:
                rows = conn.execute("SELECT * FROM pm_sessions ORDER BY created_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pm_sessions WHERE status != 'archived' ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, session_id: str, status: str) -> None:
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE pm_sessions SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, session_id),
                )

    def update_plan(self, session_id: str, plan_text: str) -> None:
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE pm_sessions SET plan_text = ?, updated_at = ? WHERE id = ?",
                    (plan_text, now, session_id),
                )

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM pm_risks WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM pm_scenarios WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM pm_sessions WHERE id = ?", (session_id,))

    # -- Scenarios (anonymous participant submissions) ------------------

    def add_scenario(self, session_id: str, scenario_text: str, submitted_by: str = "anonymous") -> str:
        sid = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO pm_scenarios (id, session_id, scenario_text, submitted_by, created_at) VALUES (?,?,?,?,?)",
                    (sid, session_id, scenario_text, submitted_by, self._now()),
                )
        return sid

    def get_scenarios(self, session_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM pm_scenarios WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_scenario(self, scenario_id: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM pm_scenarios WHERE id = ?", (scenario_id,))

    # -- Risks (admin-consolidated) ------------------------------------

    def add_risk(
        self,
        session_id: str,
        risk_title: str,
        risk_description: str = "",
        severity: int = 3,
        likelihood: int = 3,
        mitigation: str = "",
        scenario_id: Optional[str] = None,
    ) -> str:
        rid = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """INSERT INTO pm_risks
                       (id, session_id, scenario_id, risk_title, risk_description,
                        severity, likelihood, mitigation, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (rid, session_id, scenario_id, risk_title, risk_description,
                     severity, likelihood, mitigation, self._now()),
                )
        return rid

    def update_risk(
        self,
        risk_id: str,
        risk_title: str,
        risk_description: str,
        severity: int,
        likelihood: int,
        mitigation: str,
    ) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """UPDATE pm_risks SET risk_title=?, risk_description=?,
                       severity=?, likelihood=?, mitigation=? WHERE id=?""",
                    (risk_title, risk_description, severity, likelihood, mitigation, risk_id),
                )

    def get_risks(self, session_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM pm_risks WHERE session_id = ? ORDER BY severity DESC, likelihood DESC",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_risk(self, risk_id: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM pm_risks WHERE id = ?", (risk_id,))

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
