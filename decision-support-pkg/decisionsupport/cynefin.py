"""
Cynefin framework decision support tool.

Classifies decisions into Cynefin domains (Clear, Complicated, Complex, Chaotic, Disorder)
through structured diagnostic questions, and surfaces domain-appropriate response strategies.

Storage: SQLite at ~/.decisionsupport/cynefin.db (separate from the memory palace).
Input:   Interactive CLI assessment, async JSON forms, or plain-text / VTT transcripts.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Domain metadata
# ---------------------------------------------------------------------------

DOMAINS: Dict[str, str] = {
    "clear": "Clear",
    "complicated": "Complicated",
    "complex": "Complex",
    "chaotic": "Chaotic",
    "disorder": "Disorder",
}

DOMAIN_INFO: Dict[str, dict] = {
    "clear": {
        "label": "Clear",
        "description": "Cause and effect are obvious to anyone. Best practices apply.",
        "sense_act": "Sense → Categorize → Respond",
        "guidance": [
            "Apply the established best practice or standard operating procedure.",
            "Delegate confidently — the answer is known.",
            "Monitor for exceptions that might shift the domain.",
            "Avoid over-complicating; trust the playbook.",
        ],
        "warning": (
            "Beware complacency ('the clear trap'): repeated success breeds blind spots. "
            "Conditions change and can silently move a situation into Complex."
        ),
    },
    "complicated": {
        "label": "Complicated",
        "description": "Cause and effect require analysis. Multiple right answers exist.",
        "sense_act": "Sense → Analyze → Respond",
        "guidance": [
            "Engage domain experts and structured analysis.",
            "Allow time to assess options before committing.",
            "Apply good practices — multiple valid approaches may exist.",
            "Seek dissenting expert views to avoid groupthink.",
        ],
        "warning": (
            "Expert consensus can become groupthink. Actively invite minority opinions "
            "and challenge assumptions."
        ),
    },
    "complex": {
        "label": "Complex",
        "description": "Cause and effect only apparent in retrospect. Patterns emerge.",
        "sense_act": "Probe → Sense → Respond",
        "guidance": [
            "Run multiple safe-to-fail experiments in parallel.",
            "Amplify what works; dampen what does not.",
            "Create conditions for emergence — don't force a predetermined answer.",
            "Maintain diversity of approaches; avoid premature convergence.",
            "Reserve large irreversible commitments until patterns are clear.",
        ],
        "warning": (
            "Resist imposing order prematurely. Failure of a probe is data, not defeat. "
            "Committing too early collapses the space needed for emergence."
        ),
    },
    "chaotic": {
        "label": "Chaotic",
        "description": "No perceivable cause-and-effect. Immediate action is required.",
        "sense_act": "Act → Sense → Respond",
        "guidance": [
            "Take immediate decisive action to stabilize the situation.",
            "Establish clear command — one decision-maker.",
            "Communicate direction clearly and simply.",
            "Move from Chaotic toward Complex as soon as a foothold is gained.",
            "Learn from actions and adapt quickly.",
        ],
        "warning": (
            "Chaotic demands speed, not perfection. Act to create stability, then reassess. "
            "Prolonged chaos is corrosive — do not linger here."
        ),
    },
    "disorder": {
        "label": "Disorder",
        "description": "The applicable domain is unclear. Gather more context first.",
        "sense_act": "Gather → Clarify → Classify",
        "guidance": [
            "Gather more context about the situation from multiple perspectives.",
            "Break the decision into smaller, more tractable pieces.",
            "Identify which aspects belong to which domains.",
            "Seek diverse viewpoints before classifying.",
        ],
        "warning": (
            "Disorder is the most dangerous state: people default to their own comfort zone "
            "rather than the approach the situation actually demands."
        ),
    },
}

# ---------------------------------------------------------------------------
# Diagnostic questions
# ---------------------------------------------------------------------------

QUESTIONS: List[dict] = [
    {
        "id": "q_precedent",
        "text": "Has your organisation resolved this exact type of situation before with predictable results?",
        "answers": [
            {
                "key": "a",
                "label": "Yes — clear precedent with known outcomes",
                "scores": {"clear": 3, "complicated": 1, "complex": 0, "chaotic": 0},
            },
            {
                "key": "b",
                "label": "Somewhat — similar situations, but with meaningful variation",
                "scores": {"clear": 1, "complicated": 3, "complex": 1, "chaotic": 0},
            },
            {
                "key": "c",
                "label": "No — this is genuinely new territory",
                "scores": {"clear": 0, "complicated": 0, "complex": 2, "chaotic": 1},
            },
        ],
    },
    {
        "id": "q_best_practice",
        "text": "Is there an established process, procedure, or best practice that directly applies?",
        "answers": [
            {
                "key": "a",
                "label": "Yes — a clear playbook or SOP exists and is understood",
                "scores": {"clear": 3, "complicated": 0, "complex": 0, "chaotic": 0},
            },
            {
                "key": "b",
                "label": "Experts know good approaches, but analysis is needed to choose",
                "scores": {"clear": 1, "complicated": 3, "complex": 0, "chaotic": 0},
            },
            {
                "key": "c",
                "label": "No established approach — still emerging or unknown",
                "scores": {"clear": 0, "complicated": 0, "complex": 2, "chaotic": 1},
            },
        ],
    },
    {
        "id": "q_cause_effect",
        "text": "Can you predict the likely outcomes of your options with reasonable confidence?",
        "answers": [
            {
                "key": "a",
                "label": "Yes — cause-and-effect relationships are clear",
                "scores": {"clear": 3, "complicated": 1, "complex": 0, "chaotic": 0},
            },
            {
                "key": "b",
                "label": "Experts could model it, but it requires careful analysis",
                "scores": {"clear": 0, "complicated": 3, "complex": 1, "chaotic": 0},
            },
            {
                "key": "c",
                "label": "Outcomes will only be clear after the fact",
                "scores": {"clear": 0, "complicated": 0, "complex": 3, "chaotic": 1},
            },
            {
                "key": "d",
                "label": "Completely unpredictable — past patterns do not apply",
                "scores": {"clear": 0, "complicated": 0, "complex": 1, "chaotic": 3},
            },
        ],
    },
    {
        "id": "q_time",
        "text": "What is your decision horizon?",
        "answers": [
            {
                "key": "a",
                "label": "Days to weeks — time to consult, analyse, and plan",
                "scores": {"clear": 1, "complicated": 2, "complex": 1, "chaotic": 0},
            },
            {
                "key": "b",
                "label": "Hours — some time to gather key facts",
                "scores": {"clear": 0, "complicated": 2, "complex": 1, "chaotic": 1},
            },
            {
                "key": "c",
                "label": "Minutes or immediate — urgent action required now",
                "scores": {"clear": 0, "complicated": 0, "complex": 1, "chaotic": 3},
            },
        ],
    },
    {
        "id": "q_stakeholders",
        "text": "How aligned are the people involved on both goals and methods?",
        "answers": [
            {
                "key": "a",
                "label": "Aligned on both goals and methods",
                "scores": {"clear": 2, "complicated": 1, "complex": 0, "chaotic": 0},
            },
            {
                "key": "b",
                "label": "Shared goal, but disagreement on methods",
                "scores": {"clear": 0, "complicated": 2, "complex": 1, "chaotic": 0},
            },
            {
                "key": "c",
                "label": "Significant disagreement on both goals and methods",
                "scores": {"clear": 0, "complicated": 0, "complex": 2, "chaotic": 1},
            },
        ],
    },
    {
        "id": "q_experiment",
        "text": "Can you safely run small experiments or pilots to test your approach?",
        "answers": [
            {
                "key": "a",
                "label": "Yes — low-risk, easy to test and reverse",
                "scores": {"clear": 1, "complicated": 1, "complex": 1, "chaotic": 0},
            },
            {
                "key": "b",
                "label": "Yes — safe-to-fail probes are feasible",
                "scores": {"clear": 0, "complicated": 1, "complex": 3, "chaotic": 0},
            },
            {
                "key": "c",
                "label": "No — any action has large or irreversible consequences",
                "scores": {"clear": 0, "complicated": 1, "complex": 0, "chaotic": 2},
            },
        ],
    },
    {
        "id": "q_constraints",
        "text": "Are the requirements and constraints stable, or do they shift as you act?",
        "answers": [
            {
                "key": "a",
                "label": "Stable and well-defined",
                "scores": {"clear": 2, "complicated": 1, "complex": 0, "chaotic": 0},
            },
            {
                "key": "b",
                "label": "Mostly stable, some uncertainty at the edges",
                "scores": {"clear": 0, "complicated": 2, "complex": 1, "chaotic": 0},
            },
            {
                "key": "c",
                "label": "Constraints shift as we learn and act",
                "scores": {"clear": 0, "complicated": 0, "complex": 3, "chaotic": 1},
            },
            {
                "key": "d",
                "label": "Constraints are unclear or changing rapidly",
                "scores": {"clear": 0, "complicated": 0, "complex": 1, "chaotic": 2},
            },
        ],
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = str(Path.home() / ".decisionsupport" / "cynefin.db")


class CynefinDB:
    """SQLite-backed storage for Cynefin decision records."""

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

            CREATE TABLE IF NOT EXISTS decisions (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                domain      TEXT,
                status      TEXT NOT NULL DEFAULT 'open',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                metadata    TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS assessment_responses (
                id               TEXT PRIMARY KEY,
                decision_id      TEXT NOT NULL REFERENCES decisions(id),
                question_id      TEXT NOT NULL,
                question_text    TEXT NOT NULL,
                answer_key       TEXT NOT NULL,
                answer_label     TEXT NOT NULL,
                score_clear      REAL NOT NULL DEFAULT 0,
                score_complicated REAL NOT NULL DEFAULT 0,
                score_complex    REAL NOT NULL DEFAULT 0,
                score_chaotic    REAL NOT NULL DEFAULT 0,
                created_at       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notes (
                id          TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL REFERENCES decisions(id),
                content     TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'manual',
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS actions (
                id          TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL REFERENCES decisions(id),
                action_text TEXT NOT NULL,
                outcome     TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL,
                resolved_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_responses_decision ON assessment_responses(decision_id);
            CREATE INDEX IF NOT EXISTS idx_notes_decision     ON notes(decision_id);
            CREATE INDEX IF NOT EXISTS idx_actions_decision   ON actions(decision_id);
            CREATE INDEX IF NOT EXISTS idx_decisions_status   ON decisions(status);
        """)
        conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- Decisions -----------------------------------------------------------

    def create_decision(
        self,
        title: str,
        description: str = "",
        metadata: Optional[dict] = None,
    ) -> str:
        decision_id = uuid.uuid4().hex[:8]
        now = self._now()
        with self._lock:
            conn = self._connection()
            with conn:
                conn.execute(
                    """INSERT INTO decisions
                       (id, title, description, created_at, updated_at, metadata)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (decision_id, title, description, now, now, json.dumps(metadata or {})),
                )
        return decision_id

    def get_decision(self, decision_id: str) -> Optional[dict]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_decisions(self, status: Optional[str] = None, include_archived: bool = False) -> List[dict]:
        with self._lock:
            conn = self._connection()
            if status:
                rows = conn.execute(
                    "SELECT * FROM decisions WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            elif include_archived:
                rows = conn.execute(
                    "SELECT * FROM decisions ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM decisions WHERE status != 'archived' ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def set_domain(self, decision_id: str, domain: str) -> None:
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE decisions SET domain = ?, updated_at = ? WHERE id = ?",
                    (domain, now, decision_id),
                )

    def set_status(self, decision_id: str, status: str) -> None:
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE decisions SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, decision_id),
                )

    # -- Assessment responses ------------------------------------------------

    def save_response(
        self,
        decision_id: str,
        question_id: str,
        question_text: str,
        answer_key: str,
        answer_label: str,
        scores: dict,
    ) -> str:
        response_id = uuid.uuid4().hex[:8]
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                # Allow re-answering a question — remove the previous answer first
                conn.execute(
                    "DELETE FROM assessment_responses WHERE decision_id = ? AND question_id = ?",
                    (decision_id, question_id),
                )
                conn.execute(
                    """INSERT INTO assessment_responses
                       (id, decision_id, question_id, question_text, answer_key, answer_label,
                        score_clear, score_complicated, score_complex, score_chaotic, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        response_id,
                        decision_id,
                        question_id,
                        question_text,
                        answer_key,
                        answer_label,
                        scores.get("clear", 0),
                        scores.get("complicated", 0),
                        scores.get("complex", 0),
                        scores.get("chaotic", 0),
                        now,
                    ),
                )
        return response_id

    def get_responses(self, decision_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM assessment_responses WHERE decision_id = ? ORDER BY created_at",
                (decision_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- Notes ---------------------------------------------------------------

    def add_note(self, decision_id: str, content: str, source_type: str = "manual") -> str:
        note_id = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO notes (id, decision_id, content, source_type, created_at) VALUES (?, ?, ?, ?, ?)",
                    (note_id, decision_id, content, source_type, self._now()),
                )
        return note_id

    def get_notes(self, decision_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM notes WHERE decision_id = ? ORDER BY created_at",
                (decision_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- Actions -------------------------------------------------------------

    def add_action(self, decision_id: str, action_text: str) -> str:
        action_id = uuid.uuid4().hex[:8]
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO actions (id, decision_id, action_text, created_at) VALUES (?, ?, ?, ?)",
                    (action_id, decision_id, action_text, self._now()),
                )
        return action_id

    def resolve_action(self, action_id: str, outcome: str) -> None:
        now = self._now()
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE actions SET outcome = ?, resolved_at = ? WHERE id = ?",
                    (outcome, now, action_id),
                )

    def get_actions(self, decision_id: str) -> List[dict]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM actions WHERE decision_id = ? ORDER BY created_at",
                (decision_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- Export --------------------------------------------------------------

    def export_decision(self, decision_id: str) -> dict:
        decision = self.get_decision(decision_id)
        if decision is None:
            raise ValueError(f"Decision {decision_id!r} not found")
        domain = decision.get("domain") or ""
        return {
            "decision": decision,
            "responses": self.get_responses(decision_id),
            "notes": self.get_notes(decision_id),
            "actions": self.get_actions(decision_id),
            "domain_info": DOMAIN_INFO.get(domain, {}),
        }

    def delete_decision(self, decision_id: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM actions WHERE decision_id = ?", (decision_id,))
                conn.execute("DELETE FROM notes WHERE decision_id = ?", (decision_id,))
                conn.execute("DELETE FROM assessment_responses WHERE decision_id = ?", (decision_id,))
                conn.execute("DELETE FROM decisions WHERE id = ?", (decision_id,))

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------


def classify_from_responses(responses: List[dict]) -> Tuple[str, Dict[str, float]]:
    """
    Aggregate domain scores from assessment responses.

    Returns (suggested_domain, score_breakdown).
    Suggests 'disorder' when no responses exist or scores are tied.
    """
    totals: Dict[str, float] = {"clear": 0.0, "complicated": 0.0, "complex": 0.0, "chaotic": 0.0}
    for r in responses:
        totals["clear"] += r["score_clear"]
        totals["complicated"] += r["score_complicated"]
        totals["complex"] += r["score_complex"]
        totals["chaotic"] += r["score_chaotic"]

    if not any(totals.values()):
        return "disorder", totals

    max_score = max(totals.values())
    top = [d for d, s in totals.items() if s == max_score]
    if len(top) > 1:
        return "disorder", totals

    return top[0], totals


# ---------------------------------------------------------------------------
# Async form ingestion (JSON)
# ---------------------------------------------------------------------------

# Form JSON schema:
# {
#   "title": str,           required
#   "description": str,     optional
#   "responses": {          optional – map question_id → answer_key
#     "q_precedent": "a",
#     ...
#   },
#   "notes": [str, ...],    optional
#   "metadata": {}          optional
# }


def ingest_form_json(db: CynefinDB, path: str) -> str:
    """
    Create a decision from a JSON form file.

    Returns the new decision_id.  Auto-classifies when all questions are answered.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    if not isinstance(data.get("title"), str) or not data["title"].strip():
        raise ValueError("Form JSON must include a non-empty 'title' field")

    decision_id = db.create_decision(
        title=data["title"].strip(),
        description=data.get("description", ""),
        metadata=data.get("metadata", {}),
    )

    q_map = {q["id"]: q for q in QUESTIONS}
    for question_id, answer_key in (data.get("responses") or {}).items():
        if question_id not in q_map:
            continue
        q = q_map[question_id]
        answer = next((a for a in q["answers"] if a["key"] == answer_key), None)
        if answer is None:
            continue
        db.save_response(
            decision_id=decision_id,
            question_id=question_id,
            question_text=q["text"],
            answer_key=answer_key,
            answer_label=answer["label"],
            scores=answer["scores"],
        )

    for note in data.get("notes") or []:
        if isinstance(note, str) and note.strip():
            db.add_note(decision_id, note.strip(), source_type="form")

    # Auto-classify when the form provides answers for every question
    responses = db.get_responses(decision_id)
    if len(responses) == len(QUESTIONS):
        domain, _ = classify_from_responses(responses)
        db.set_domain(decision_id, domain)

    return decision_id


# ---------------------------------------------------------------------------
# Transcript ingestion
# ---------------------------------------------------------------------------

# Supported formats:
#   plain   — raw paragraphs separated by blank lines
#   vtt     — WebVTT captions (Zoom, Teams, Google Meet exports)
#   json    — list of {"speaker": ..., "text": ...} objects


def _parse_vtt(text: str) -> List[str]:
    """Extract utterance text from a WebVTT file, stripping cue headers and timestamps."""
    lines = text.splitlines()
    utterances: List[str] = []
    buf: List[str] = []
    # VTT timestamp line pattern: 00:00:00.000 --> 00:00:00.000
    _ts = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[.,]\d{3}")
    for line in lines:
        line = line.strip()
        if _ts.match(line) or line.upper() == "WEBVTT" or line.isdigit():
            if buf:
                utterances.append(" ".join(buf))
                buf = []
        elif line:
            buf.append(line)
    if buf:
        utterances.append(" ".join(buf))
    return [u for u in utterances if len(u) > 20]


def _parse_json_transcript(text: str) -> List[str]:
    """
    Parse a JSON transcript — either a list of message objects or a dict with a
    'messages' / 'transcript' key.  Each entry must have a 'text' or 'content' field.
    """
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("messages") or data.get("transcript") or []
    if not isinstance(data, list):
        return []
    chunks: List[str] = []
    for entry in data:
        if isinstance(entry, dict):
            body = entry.get("text") or entry.get("content") or ""
        elif isinstance(entry, str):
            body = entry
        else:
            body = ""
        body = body.strip()
        if len(body) > 20:
            chunks.append(body)
    return chunks


def ingest_transcript(
    db: CynefinDB,
    decision_id: str,
    path: str,
    fmt: str = "auto",
) -> int:
    """
    Ingest a transcript file as notes attached to a decision.

    fmt: 'auto' (detect from extension), 'plain', 'vtt', 'json'
    Returns the number of notes added.
    """
    content = Path(path).read_text(encoding="utf-8").strip()
    if not content:
        return 0

    if fmt == "auto":
        ext = Path(path).suffix.lower()
        if ext == ".vtt":
            fmt = "vtt"
        elif ext in (".json", ".jsonl"):
            fmt = "json"
        else:
            fmt = "plain"

    if fmt == "vtt":
        chunks = _parse_vtt(content)
    elif fmt == "json":
        try:
            chunks = _parse_json_transcript(content)
        except (json.JSONDecodeError, ValueError):
            # Fall back to plain text on parse failure
            chunks = [c.strip() for c in content.split("\n\n") if len(c.strip()) > 20]
    else:
        chunks = [c.strip() for c in content.split("\n\n") if len(c.strip()) > 20]

    count = 0
    for chunk in chunks:
        db.add_note(decision_id, chunk, source_type="transcript")
        count += 1
    return count
