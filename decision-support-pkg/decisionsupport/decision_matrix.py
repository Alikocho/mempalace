"""
Decision Matrix framework.

Evaluates a set of options against weighted criteria. Participants score each
option (1–10) against each criterion; the tool computes weighted averages and
ranks options. Criteria weights are set by the admin and hidden from participants
to avoid anchoring bias.

Workflow:
  setup → scoring → closed
  (any state can be archived or deleted)

Storage: SQLite at <data_dir>/decision_matrix.db
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_DB_PATH = str(Path.home() / ".decisionsupport" / "decision_matrix.db")

SCORE_MIN = 1
SCORE_MAX = 10
WEIGHT_MIN = 1
WEIGHT_MAX = 5


def compute_results(options: List[dict], criteria: List[dict], scores: List[dict]) -> List[dict]:
    """
    Compute weighted average score for each option.

    Returns list of dicts sorted by weighted_avg descending:
      { option_id, option_text, weighted_avg, raw_scores: {criterion_id: [scores]} }
    """
    # Index scores: option_id → criterion_id → [values]
    by_opt_crit: Dict[str, Dict[str, List[float]]] = {}
    for s in scores:
        by_opt_crit.setdefault(s["option_id"], {}).setdefault(s["criterion_id"], []).append(s["score"])

    total_weight = sum(c["weight"] for c in criteria) or 1

    results = []
    for opt in options:
        oid = opt["id"]
        weighted_sum = 0.0
        raw: Dict[str, List[float]] = {}
        for crit in criteria:
            cid = crit["id"]
            vals = by_opt_crit.get(oid, {}).get(cid, [])
            raw[cid] = vals
            if vals:
                avg = sum(vals) / len(vals)
                weighted_sum += avg * crit["weight"]
        weighted_avg = round(weighted_sum / total_weight, 2) if total_weight else 0.0
        results.append({
            "option_id": oid,
            "option_text": opt["text"],
            "weighted_avg": weighted_avg,
            "raw_scores": raw,
        })

    results.sort(key=lambda x: x["weighted_avg"], reverse=True)
    return results


class DecisionMatrixDB:
    """SQLite-backed storage for Decision Matrix sessions."""

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

            CREATE TABLE IF NOT EXISTS dm_sessions (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'setup',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dm_options (
                id         TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES dm_sessions(id),
                text       TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dm_criteria (
                id         TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES dm_sessions(id),
                text       TEXT NOT NULL,
                weight     INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dm_scores (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL REFERENCES dm_sessions(id),
                option_id    TEXT NOT NULL REFERENCES dm_options(id),
                criterion_id TEXT NOT NULL REFERENCES dm_criteria(id),
                respondent   TEXT NOT NULL DEFAULT 'anonymous',
                score        REAL NOT NULL,
                created_at   TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dm_options_session  ON dm_options(session_id);
            CREATE INDEX IF NOT EXISTS idx_dm_criteria_session ON dm_criteria(session_id);
            CREATE INDEX IF NOT EXISTS idx_dm_scores_session   ON dm_scores(session_id);
            CREATE INDEX IF NOT EXISTS idx_dm_scores_option    ON dm_scores(option_id);
        """)
        conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- Sessions -------------------------------------------------------

    def create_session(self, title: str, description: str = "") -> str:
        sid = uuid.uuid4().hex[:8]
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO dm_sessions (id, title, description, created_at, updated_at) VALUES (?,?,?,?,?)",
                    (sid, title, description, now, now),
                )
        return sid

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM dm_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, status: Optional[str] = None, include_archived: bool = False) -> List[dict]:
        with self._lock:
            conn = self._connection()
            if status:
                rows = conn.execute(
                    "SELECT * FROM dm_sessions WHERE status = ? ORDER BY created_at DESC", (status,)
                ).fetchall()
            elif include_archived:
                rows = conn.execute("SELECT * FROM dm_sessions ORDER BY created_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM dm_sessions WHERE status != 'archived' ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, session_id: str, status: str) -> None:
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE dm_sessions SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, session_id),
                )

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM dm_scores WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM dm_criteria WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM dm_options WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM dm_sessions WHERE id = ?", (session_id,))

    # -- Options --------------------------------------------------------

    def add_option(self, session_id: str, text: str) -> str:
        oid = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO dm_options (id, session_id, text, created_at) VALUES (?,?,?,?)",
                    (oid, session_id, text, self._now()),
                )
        return oid

    def get_options(self, session_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM dm_options WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_option(self, option_id: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM dm_scores WHERE option_id = ?", (option_id,))
                conn.execute("DELETE FROM dm_options WHERE id = ?", (option_id,))

    # -- Criteria -------------------------------------------------------

    def add_criterion(self, session_id: str, text: str, weight: int = 1) -> str:
        cid = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO dm_criteria (id, session_id, text, weight, created_at) VALUES (?,?,?,?,?)",
                    (cid, session_id, text, weight, self._now()),
                )
        return cid

    def update_criterion_weight(self, criterion_id: str, weight: int) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE dm_criteria SET weight = ? WHERE id = ?",
                    (weight, criterion_id),
                )

    def get_criteria(self, session_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM dm_criteria WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_criterion(self, criterion_id: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM dm_scores WHERE criterion_id = ?", (criterion_id,))
                conn.execute("DELETE FROM dm_criteria WHERE id = ?", (criterion_id,))

    # -- Scores ---------------------------------------------------------

    def add_score(
        self,
        session_id: str,
        option_id: str,
        criterion_id: str,
        score: float,
        respondent: str = "anonymous",
    ) -> str:
        if not (SCORE_MIN <= score <= SCORE_MAX):
            raise ValueError(f"Score must be between {SCORE_MIN} and {SCORE_MAX}")
        sid = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """INSERT INTO dm_scores
                       (id, session_id, option_id, criterion_id, respondent, score, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (sid, session_id, option_id, criterion_id, respondent, score, self._now()),
                )
        return sid

    def get_scores(self, session_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM dm_scores WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
