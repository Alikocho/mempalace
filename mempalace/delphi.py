"""
Delphi method decision support.

Structured anonymous expert consensus through iterative questioning rounds.
Participants submit text responses in early rounds and numeric ratings in later
rounds; the tool tracks convergence via IQR until consensus is reached.

Storage: SQLite at ~/.mempalace/delphi.db
Rating scale: 1–9 (standard Delphi; 1 = strongly disagree, 9 = strongly agree)
Consensus definition: IQR ≤ consensus_threshold (default 2.0)
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RATING_MIN = 1
RATING_MAX = 9
DEFAULT_CONSENSUS_THRESHOLD = 2.0  # IQR ≤ this value → consensus

AGREEMENT_LABELS: Dict[str, str] = {
    "agree": "Agree (7–9)",
    "uncertain": "Uncertain (4–6)",
    "disagree": "Disagree (1–3)",
}

DEFAULT_DB_PATH = str(Path.home() / ".mempalace" / "delphi.db")

# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------


def _quartile(sorted_vals: List[float], q: float) -> float:
    """Linear interpolation quartile — works with small samples."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = lo + 1
    if hi >= n:
        return float(sorted_vals[-1])
    frac = pos - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def compute_stats(values: List[float], threshold: float = DEFAULT_CONSENSUS_THRESHOLD) -> dict:
    """
    Return descriptive statistics and a consensus verdict for a list of ratings.

    Consensus requires: IQR ≤ threshold.
    Agreement label is determined by the median value on a 1–9 scale.
    """
    if not values:
        return {
            "count": 0,
            "consensus": False,
            "agreement": None,
            "mean": None,
            "median": None,
            "q1": None,
            "q3": None,
            "iqr": None,
            "min": None,
            "max": None,
            "pct_near_median": None,
        }

    sv = sorted(values)
    n = len(sv)
    mean = statistics.mean(sv)
    median = statistics.median(sv)
    q1 = _quartile(sv, 0.25)
    q3 = _quartile(sv, 0.75)
    iqr = q3 - q1
    pct_near = sum(1 for v in sv if abs(v - median) <= 1) / n * 100
    consensus = iqr <= threshold

    if median >= 7:
        agreement = "agree"
    elif median >= 4:
        agreement = "uncertain"
    else:
        agreement = "disagree"

    return {
        "count": n,
        "consensus": consensus,
        "agreement": agreement,
        "mean": round(mean, 2),
        "median": float(median),
        "q1": round(q1, 2),
        "q3": round(q3, 2),
        "iqr": round(iqr, 2),
        "min": float(sv[0]),
        "max": float(sv[-1]),
        "pct_near_median": round(pct_near, 1),
    }


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class DelphiDB:
    """SQLite-backed storage for Delphi method sessions."""

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

            CREATE TABLE IF NOT EXISTS sessions (
                id                    TEXT PRIMARY KEY,
                title                 TEXT NOT NULL,
                description           TEXT NOT NULL DEFAULT '',
                question              TEXT NOT NULL,
                status                TEXT NOT NULL DEFAULT 'active',
                current_round         INTEGER NOT NULL DEFAULT 0,
                consensus_threshold   REAL NOT NULL DEFAULT 2.0,
                created_at            TEXT NOT NULL,
                updated_at            TEXT NOT NULL,
                metadata              TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS rounds (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL REFERENCES sessions(id),
                round_number INTEGER NOT NULL,
                status       TEXT NOT NULL DEFAULT 'open',
                prompt       TEXT NOT NULL DEFAULT '',
                summary      TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL,
                closed_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS responses (
                id            TEXT PRIMARY KEY,
                session_id    TEXT NOT NULL REFERENCES sessions(id),
                round_id      TEXT NOT NULL REFERENCES rounds(id),
                round_number  INTEGER NOT NULL,
                respondent    TEXT NOT NULL DEFAULT 'anonymous',
                response_text TEXT NOT NULL,
                score         REAL,
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS items (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL REFERENCES sessions(id),
                text         TEXT NOT NULL,
                source_round INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ratings (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES sessions(id),
                round_id    TEXT NOT NULL REFERENCES rounds(id),
                item_id     TEXT NOT NULL REFERENCES items(id),
                respondent  TEXT NOT NULL DEFAULT 'anonymous',
                rating      REAL NOT NULL,
                rationale   TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notes (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES sessions(id),
                content     TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'manual',
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_rounds_session   ON rounds(session_id);
            CREATE INDEX IF NOT EXISTS idx_responses_round  ON responses(round_id);
            CREATE INDEX IF NOT EXISTS idx_items_session    ON items(session_id);
            CREATE INDEX IF NOT EXISTS idx_ratings_round    ON ratings(round_id);
            CREATE INDEX IF NOT EXISTS idx_ratings_item     ON ratings(item_id);
            CREATE INDEX IF NOT EXISTS idx_notes_session    ON notes(session_id);
        """)
        conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- Sessions ------------------------------------------------------------

    def create_session(
        self,
        title: str,
        question: str,
        description: str = "",
        consensus_threshold: float = DEFAULT_CONSENSUS_THRESHOLD,
        metadata: Optional[dict] = None,
    ) -> str:
        session_id = uuid.uuid4().hex[:8]
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """INSERT INTO sessions
                       (id, title, description, question, consensus_threshold,
                        created_at, updated_at, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        title,
                        description,
                        question,
                        consensus_threshold,
                        now,
                        now,
                        json.dumps(metadata or {}),
                    ),
                )
        return session_id

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, status: Optional[str] = None) -> List[dict]:
        with self._lock:
            conn = self._connection()
            if status:
                rows = conn.execute(
                    "SELECT * FROM sessions WHERE status = ? ORDER BY created_at DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sessions ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, session_id: str, status: str) -> None:
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, session_id),
                )

    def _bump_updated(self, session_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (self._now(), session_id),
            )

    # -- Rounds --------------------------------------------------------------

    def open_round(self, session_id: str, prompt: str = "") -> str:
        """Create and open the next round for a session. Fails if a round is already open."""
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id!r} not found")

        with self._lock:
            conn = self._connection()
            open_round = conn.execute(
                "SELECT id FROM rounds WHERE session_id = ? AND status = 'open'",
                (session_id,),
            ).fetchone()
            if open_round:
                raise ValueError(
                    f"Round is already open ({open_round['id']}). Close it before opening a new one."
                )

            next_num = session["current_round"] + 1
            round_id = uuid.uuid4().hex[:8]
            now = self._now()
            with conn:
                conn.execute(
                    "INSERT INTO rounds (id, session_id, round_number, prompt, created_at) VALUES (?, ?, ?, ?, ?)",
                    (round_id, session_id, next_num, prompt, now),
                )
                conn.execute(
                    "UPDATE sessions SET current_round = ?, updated_at = ? WHERE id = ?",
                    (next_num, now, session_id),
                )
        return round_id

    def close_round(self, session_id: str, summary: str = "") -> str:
        """Close the open round for a session. Returns the closed round_id."""
        with self._lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT id FROM rounds WHERE session_id = ? AND status = 'open'",
                (session_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"No open round for session {session_id!r}")
            round_id = row["id"]
            now = self._now()
            with conn:
                conn.execute(
                    "UPDATE rounds SET status = 'closed', summary = ?, closed_at = ? WHERE id = ?",
                    (summary, now, round_id),
                )
                conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
        return round_id

    def get_current_round(self, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM rounds WHERE session_id = ? AND status = 'open'",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_round(self, round_id: str) -> Optional[dict]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM rounds WHERE id = ?", (round_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_rounds(self, session_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM rounds WHERE session_id = ? ORDER BY round_number",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- Responses (text) ----------------------------------------------------

    def add_response(
        self,
        session_id: str,
        round_id: str,
        response_text: str,
        respondent: str = "anonymous",
        score: Optional[float] = None,
    ) -> str:
        if score is not None and not (RATING_MIN <= score <= RATING_MAX):
            raise ValueError(f"Score must be between {RATING_MIN} and {RATING_MAX}")
        response_id = uuid.uuid4().hex[:8]
        rnd = self.get_round(round_id)
        if rnd is None:
            raise ValueError(f"Round {round_id!r} not found")
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """INSERT INTO responses
                       (id, session_id, round_id, round_number, respondent, response_text, score, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        response_id,
                        session_id,
                        round_id,
                        rnd["round_number"],
                        respondent,
                        response_text,
                        score,
                        self._now(),
                    ),
                )
        return response_id

    def get_responses(self, session_id: str, round_id: Optional[str] = None) -> List[dict]:
        with self._lock:
            conn = self._connection()
            if round_id:
                rows = conn.execute(
                    "SELECT * FROM responses WHERE session_id = ? AND round_id = ? ORDER BY created_at",
                    (session_id, round_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM responses WHERE session_id = ? ORDER BY round_number, created_at",
                    (session_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    # -- Items (propositions to be rated) ------------------------------------

    def add_item(self, session_id: str, text: str, source_round: int = 1) -> str:
        item_id = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO items (id, session_id, text, source_round, created_at) VALUES (?, ?, ?, ?, ?)",
                    (item_id, session_id, text, source_round, self._now()),
                )
        return item_id

    def get_items(self, session_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM items WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- Ratings -------------------------------------------------------------

    def add_rating(
        self,
        session_id: str,
        round_id: str,
        item_id: str,
        rating: float,
        respondent: str = "anonymous",
        rationale: str = "",
    ) -> str:
        if not (RATING_MIN <= rating <= RATING_MAX):
            raise ValueError(f"Rating must be between {RATING_MIN} and {RATING_MAX}")
        rating_id = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """INSERT INTO ratings
                       (id, session_id, round_id, item_id, respondent, rating, rationale, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rating_id, session_id, round_id, item_id, respondent, rating, rationale, self._now()),
                )
        return rating_id

    def get_ratings(
        self,
        session_id: str,
        round_id: Optional[str] = None,
        item_id: Optional[str] = None,
    ) -> List[dict]:
        with self._lock:
            conn = self._connection()
            query = "SELECT * FROM ratings WHERE session_id = ?"
            params: list = [session_id]
            if round_id:
                query += " AND round_id = ?"
                params.append(round_id)
            if item_id:
                query += " AND item_id = ?"
                params.append(item_id)
            query += " ORDER BY item_id, created_at"
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # -- Statistics ----------------------------------------------------------

    def round_statistics(self, session_id: str, round_id: str) -> List[dict]:
        """
        Return per-item consensus statistics for all rated items in a round.
        Items with no ratings in this round are omitted.
        """
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id!r} not found")
        threshold = session["consensus_threshold"]

        items = {item["id"]: item for item in self.get_items(session_id)}
        ratings = self.get_ratings(session_id, round_id=round_id)

        # Group ratings by item
        by_item: Dict[str, List[float]] = {}
        for r in ratings:
            by_item.setdefault(r["item_id"], []).append(r["rating"])

        results = []
        for item_id, values in by_item.items():
            stats = compute_stats(values, threshold=threshold)
            stats["item_id"] = item_id
            stats["item_text"] = items.get(item_id, {}).get("text", "")
            results.append(stats)

        return sorted(results, key=lambda s: s.get("item_text", ""))

    def session_consensus_summary(self, session_id: str) -> dict:
        """
        Aggregate consensus state across all rounds for each item.
        Returns the most recent round's statistics per item.
        """
        rounds = self.list_rounds(session_id)
        closed_rounds = [r for r in rounds if r["status"] == "closed"]
        if not closed_rounds:
            return {"items": [], "all_consensus": False}

        last_round = closed_rounds[-1]
        stats = self.round_statistics(session_id, last_round["id"])
        all_consensus = bool(stats) and all(s["consensus"] for s in stats)
        return {
            "as_of_round": last_round["round_number"],
            "items": stats,
            "all_consensus": all_consensus,
        }

    # -- Notes ---------------------------------------------------------------

    def add_note(self, session_id: str, content: str, source_type: str = "manual") -> str:
        note_id = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO notes (id, session_id, content, source_type, created_at) VALUES (?, ?, ?, ?, ?)",
                    (note_id, session_id, content, source_type, self._now()),
                )
        return note_id

    def get_notes(self, session_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM notes WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- Batch import --------------------------------------------------------

    def import_responses_json(self, session_id: str, path: str) -> Tuple[int, int]:
        """
        Import responses and/or ratings from a JSON file.

        File format:
        {
            "respondent": "Alice",          // optional, default "anonymous"
            "responses": [                  // text responses
                {"text": "...", "score": 7}
            ],
            "ratings": [                    // numeric ratings
                {"item_id": "abc123", "rating": 8, "rationale": "..."}
            ]
        }

        Returns (n_responses, n_ratings) added.
        """
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id!r} not found")

        current_round = self.get_current_round(session_id)
        if current_round is None:
            raise ValueError("No open round. Run 'delphi round open <id>' first.")

        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        respondent = data.get("respondent", "anonymous")
        n_responses = 0
        n_ratings = 0

        for entry in data.get("responses") or []:
            text = entry.get("text", "").strip()
            if not text:
                continue
            score = entry.get("score")
            self.add_response(
                session_id=session_id,
                round_id=current_round["id"],
                response_text=text,
                respondent=respondent,
                score=float(score) if score is not None else None,
            )
            n_responses += 1

        for entry in data.get("ratings") or []:
            item_id = entry.get("item_id", "").strip()
            rating = entry.get("rating")
            if not item_id or rating is None:
                continue
            self.add_rating(
                session_id=session_id,
                round_id=current_round["id"],
                item_id=item_id,
                rating=float(rating),
                respondent=respondent,
                rationale=entry.get("rationale", ""),
            )
            n_ratings += 1

        return n_responses, n_ratings

    # -- Export --------------------------------------------------------------

    def export_session(self, session_id: str) -> dict:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id!r} not found")
        return {
            "session": session,
            "rounds": self.list_rounds(session_id),
            "responses": self.get_responses(session_id),
            "items": self.get_items(session_id),
            "ratings": self.get_ratings(session_id),
            "notes": self.get_notes(session_id),
            "consensus_summary": self.session_consensus_summary(session_id),
        }

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
