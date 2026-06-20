"""
test_cynefin.py — Tests for the Cynefin decision support tool.

Covers: DB CRUD, classification logic, JSON form ingestion,
transcript ingestion (plain/VTT/JSON), CLI commands.
"""
from __future__ import annotations

import json

import pytest

from decisionsupport.cynefin import (
    DOMAIN_INFO,
    DOMAINS,
    QUESTIONS,
    CynefinDB,
    classify_from_responses,
    ingest_form_json,
    ingest_transcript,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """In-memory (temp file) CynefinDB isolated per test."""
    return CynefinDB(db_path=str(tmp_path / "test_cynefin.db"))


@pytest.fixture
def decision_id(db):
    return db.create_decision("Test decision", description="Some context")


# ---------------------------------------------------------------------------
# CynefinDB — decisions
# ---------------------------------------------------------------------------


class TestDecisions:
    def test_create_returns_id(self, db):
        did = db.create_decision("Decide on tech stack")
        assert isinstance(did, str) and len(did) == 8

    def test_get_decision_roundtrip(self, db):
        did = db.create_decision("My decision", description="Context here")
        d = db.get_decision(did)
        assert d["title"] == "My decision"
        assert d["description"] == "Context here"
        assert d["status"] == "open"
        assert d["domain"] is None

    def test_get_missing_returns_none(self, db):
        assert db.get_decision("notfound") is None

    def test_list_decisions_all(self, db):
        db.create_decision("A")
        db.create_decision("B")
        results = db.list_decisions()
        assert len(results) == 2

    def test_list_decisions_filter_status(self, db):
        did_open = db.create_decision("Open one")
        did_closed = db.create_decision("Closed one")
        db.set_status(did_closed, "closed")
        open_list = db.list_decisions(status="open")
        closed_list = db.list_decisions(status="closed")
        assert len(open_list) == 1
        assert open_list[0]["id"] == did_open
        assert len(closed_list) == 1

    def test_set_domain(self, db, decision_id):
        db.set_domain(decision_id, "complex")
        d = db.get_decision(decision_id)
        assert d["domain"] == "complex"

    def test_set_status(self, db, decision_id):
        db.set_status(decision_id, "decided")
        d = db.get_decision(decision_id)
        assert d["status"] == "decided"

    def test_create_with_metadata(self, db):
        did = db.create_decision("X", metadata={"team": "eng", "priority": "high"})
        d = db.get_decision(did)
        meta = json.loads(d["metadata"])
        assert meta["team"] == "eng"


# ---------------------------------------------------------------------------
# CynefinDB — assessment responses
# ---------------------------------------------------------------------------


def _make_response(db, decision_id, question_index=0, answer_index=0):
    q = QUESTIONS[question_index]
    a = q["answers"][answer_index]
    return db.save_response(
        decision_id=decision_id,
        question_id=q["id"],
        question_text=q["text"],
        answer_key=a["key"],
        answer_label=a["label"],
        scores=a["scores"],
    )


class TestAssessmentResponses:
    def test_save_response_roundtrip(self, db, decision_id):
        _make_response(db, decision_id)
        responses = db.get_responses(decision_id)
        assert len(responses) == 1
        assert responses[0]["question_id"] == QUESTIONS[0]["id"]

    def test_re_answering_replaces_response(self, db, decision_id):
        _make_response(db, decision_id, question_index=0, answer_index=0)
        _make_response(db, decision_id, question_index=0, answer_index=1)
        responses = db.get_responses(decision_id)
        assert len(responses) == 1  # not doubled
        assert responses[0]["answer_key"] == QUESTIONS[0]["answers"][1]["key"]

    def test_scores_are_stored(self, db, decision_id):
        q = QUESTIONS[0]
        a = q["answers"][0]
        db.save_response(
            decision_id=decision_id,
            question_id=q["id"],
            question_text=q["text"],
            answer_key=a["key"],
            answer_label=a["label"],
            scores=a["scores"],
        )
        r = db.get_responses(decision_id)[0]
        assert r["score_clear"] == a["scores"]["clear"]
        assert r["score_complicated"] == a["scores"]["complicated"]


# ---------------------------------------------------------------------------
# CynefinDB — notes
# ---------------------------------------------------------------------------


class TestNotes:
    def test_add_and_get_note(self, db, decision_id):
        nid = db.add_note(decision_id, "Important context from meeting")
        notes = db.get_notes(decision_id)
        assert len(notes) == 1
        assert notes[0]["id"] == nid
        assert notes[0]["content"] == "Important context from meeting"
        assert notes[0]["source_type"] == "manual"

    def test_source_type_preserved(self, db, decision_id):
        db.add_note(decision_id, "From a transcript", source_type="transcript")
        notes = db.get_notes(decision_id)
        assert notes[0]["source_type"] == "transcript"

    def test_multiple_notes_ordered(self, db, decision_id):
        db.add_note(decision_id, "First")
        db.add_note(decision_id, "Second")
        notes = db.get_notes(decision_id)
        assert notes[0]["content"] == "First"
        assert notes[1]["content"] == "Second"


# ---------------------------------------------------------------------------
# CynefinDB — actions
# ---------------------------------------------------------------------------


class TestActions:
    def test_add_action(self, db, decision_id):
        aid = db.add_action(decision_id, "Try approach A with team")
        actions = db.get_actions(decision_id)
        assert len(actions) == 1
        assert actions[0]["id"] == aid
        assert actions[0]["outcome"] == ""
        assert actions[0]["resolved_at"] is None

    def test_resolve_action(self, db, decision_id):
        aid = db.add_action(decision_id, "Run pilot")
        db.resolve_action(aid, "Pilot succeeded — scaling up")
        actions = db.get_actions(decision_id)
        assert actions[0]["outcome"] == "Pilot succeeded — scaling up"
        assert actions[0]["resolved_at"] is not None


# ---------------------------------------------------------------------------
# CynefinDB — export
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_includes_all_sections(self, db, decision_id):
        _make_response(db, decision_id)
        db.add_note(decision_id, "A note")
        db.add_action(decision_id, "An action")
        db.set_domain(decision_id, "complex")
        data = db.export_decision(decision_id)
        assert "decision" in data
        assert "responses" in data
        assert "notes" in data
        assert "actions" in data
        assert "domain_info" in data
        assert data["domain_info"]["label"] == "Complex"

    def test_export_missing_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            db.export_decision("nonexistent")


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------


def _all_answers(answer_index: int) -> list:
    """Build a synthetic response list selecting answer_index for every question."""
    rows = []
    for q in QUESTIONS:
        a = q["answers"][min(answer_index, len(q["answers"]) - 1)]
        rows.append(
            {
                "score_clear": a["scores"]["clear"],
                "score_complicated": a["scores"]["complicated"],
                "score_complex": a["scores"]["complex"],
                "score_chaotic": a["scores"]["chaotic"],
            }
        )
    return rows


class TestClassification:
    def test_empty_responses_returns_disorder(self):
        domain, scores = classify_from_responses([])
        assert domain == "disorder"

    def test_all_clear_answers(self):
        # Answer index 0 is heavily weighted toward Clear in all questions
        responses = _all_answers(0)
        domain, _ = classify_from_responses(responses)
        assert domain == "clear"

    def test_all_complex_answers(self):
        # Answer "c" (index 2) is complex-heavy across most questions
        responses = _all_answers(2)
        domain, _ = classify_from_responses(responses)
        assert domain == "complex"

    def test_scores_are_non_negative(self):
        responses = _all_answers(1)
        _, scores = classify_from_responses(responses)
        for v in scores.values():
            assert v >= 0

    def test_tie_returns_disorder(self):
        # Manufacture a tie by constructing exactly equal scores for two domains
        equal = [{"score_clear": 5, "score_complicated": 5, "score_complex": 0, "score_chaotic": 0}]
        domain, _ = classify_from_responses(equal)
        assert domain == "disorder"

    def test_single_response_classifies(self):
        r = [{"score_clear": 3, "score_complicated": 0, "score_complex": 0, "score_chaotic": 0}]
        domain, _ = classify_from_responses(r)
        assert domain == "clear"


# ---------------------------------------------------------------------------
# JSON form ingestion
# ---------------------------------------------------------------------------


class TestFormIngestion:
    def _write_form(self, tmp_path, data: dict) -> str:
        p = tmp_path / "form.json"
        p.write_text(json.dumps(data))
        return str(p)

    def test_basic_form_creates_decision(self, db, tmp_path):
        path = self._write_form(tmp_path, {"title": "New platform decision"})
        did = ingest_form_json(db, path)
        d = db.get_decision(did)
        assert d["title"] == "New platform decision"

    def test_form_with_responses(self, db, tmp_path):
        responses = {q["id"]: q["answers"][0]["key"] for q in QUESTIONS}
        path = self._write_form(
            tmp_path,
            {
                "title": "Platform decision",
                "responses": responses,
            },
        )
        did = ingest_form_json(db, path)
        assert len(db.get_responses(did)) == len(QUESTIONS)

    def test_full_responses_triggers_auto_classify(self, db, tmp_path):
        responses = {q["id"]: q["answers"][0]["key"] for q in QUESTIONS}
        path = self._write_form(
            tmp_path,
            {"title": "Auto-classified", "responses": responses},
        )
        did = ingest_form_json(db, path)
        d = db.get_decision(did)
        assert d["domain"] is not None  # auto-classified

    def test_partial_responses_no_auto_classify(self, db, tmp_path):
        path = self._write_form(
            tmp_path,
            {
                "title": "Partial",
                "responses": {QUESTIONS[0]["id"]: QUESTIONS[0]["answers"][0]["key"]},
            },
        )
        did = ingest_form_json(db, path)
        d = db.get_decision(did)
        assert d["domain"] is None  # not enough answers

    def test_form_with_notes(self, db, tmp_path):
        path = self._write_form(
            tmp_path,
            {"title": "With notes", "notes": ["First concern", "Second concern"]},
        )
        did = ingest_form_json(db, path)
        notes = db.get_notes(did)
        assert len(notes) == 2
        assert notes[0]["source_type"] == "form"

    def test_missing_title_raises(self, db, tmp_path):
        path = self._write_form(tmp_path, {"description": "No title"})
        with pytest.raises(ValueError, match="title"):
            ingest_form_json(db, path)

    def test_unknown_question_id_skipped(self, db, tmp_path):
        path = self._write_form(
            tmp_path,
            {"title": "X", "responses": {"q_nonexistent": "a"}},
        )
        did = ingest_form_json(db, path)
        assert db.get_responses(did) == []

    def test_metadata_stored(self, db, tmp_path):
        path = self._write_form(
            tmp_path,
            {"title": "Meta test", "metadata": {"project": "alpha"}},
        )
        did = ingest_form_json(db, path)
        d = db.get_decision(did)
        meta = json.loads(d["metadata"])
        assert meta["project"] == "alpha"


# ---------------------------------------------------------------------------
# Transcript ingestion
# ---------------------------------------------------------------------------


class TestTranscriptIngestion:
    def test_plain_text(self, db, decision_id, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("First paragraph about the situation.\n\nSecond paragraph with more detail.")
        count = ingest_transcript(db, decision_id, str(f))
        assert count == 2
        notes = db.get_notes(decision_id)
        assert len(notes) == 2
        assert notes[0]["source_type"] == "transcript"

    def test_plain_text_skips_short_chunks(self, db, decision_id, tmp_path):
        f = tmp_path / "short.txt"
        f.write_text("ok\n\nThis paragraph is long enough to be a note.")
        count = ingest_transcript(db, decision_id, str(f))
        assert count == 1

    def test_vtt_format(self, db, decision_id, tmp_path):
        vtt = (
            "WEBVTT\n\n"
            "1\n00:00:01.000 --> 00:00:04.000\nAlice: We need to decide quickly.\n\n"
            "2\n00:00:05.000 --> 00:00:09.000\nBob: I think we should gather more data first.\n\n"
        )
        f = tmp_path / "meeting.vtt"
        f.write_text(vtt)
        count = ingest_transcript(db, decision_id, str(f))
        assert count == 2
        notes = db.get_notes(decision_id)
        assert any("gather more data" in n["content"] for n in notes)

    def test_json_transcript(self, db, decision_id, tmp_path):
        data = [
            {"speaker": "Alice", "text": "We need to decide on the architecture approach."},
            {"speaker": "Bob", "text": "I suggest we prototype both options before committing."},
        ]
        f = tmp_path / "convo.json"
        f.write_text(json.dumps(data))
        count = ingest_transcript(db, decision_id, str(f), fmt="json")
        assert count == 2

    def test_json_transcript_with_messages_key(self, db, decision_id, tmp_path):
        data = {
            "messages": [
                {"text": "First message with sufficient length to be stored."},
                {"text": "Second message also long enough."},
            ]
        }
        f = tmp_path / "wrapped.json"
        f.write_text(json.dumps(data))
        count = ingest_transcript(db, decision_id, str(f), fmt="json")
        assert count == 2

    def test_auto_detect_vtt(self, db, decision_id, tmp_path):
        vtt = (
            "WEBVTT\n\n"
            "1\n00:00:01.000 --> 00:00:04.000\nThis is a test utterance long enough.\n\n"
        )
        f = tmp_path / "auto.vtt"
        f.write_text(vtt)
        count = ingest_transcript(db, decision_id, str(f), fmt="auto")
        assert count == 1

    def test_empty_file_returns_zero(self, db, decision_id, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("   ")
        count = ingest_transcript(db, decision_id, str(f))
        assert count == 0


# ---------------------------------------------------------------------------
# Domain metadata completeness
# ---------------------------------------------------------------------------


class TestDomainMetadata:
    def test_all_domains_have_info(self):
        for key in DOMAINS:
            assert key in DOMAIN_INFO
            info = DOMAIN_INFO[key]
            assert "label" in info
            assert "description" in info
            assert "sense_act" in info
            assert "guidance" in info
            assert "warning" in info

    def test_all_questions_have_scores(self):
        for q in QUESTIONS:
            assert "id" in q
            assert "text" in q
            for a in q["answers"]:
                assert "key" in a
                assert "scores" in a
                for domain in ["clear", "complicated", "complex", "chaotic"]:
                    assert domain in a["scores"]

    def test_question_ids_are_unique(self):
        ids = [q["id"] for q in QUESTIONS]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_domains_command(self, tmp_path):
        """domains command prints all five domains."""
        from io import StringIO
        from unittest.mock import patch

        from decisionsupport.cynefin_cli import cmd_domains, _build_parser

        parser = _build_parser()
        args = parser.parse_args(["domains"])
        out = StringIO()
        with patch("sys.stdout", out):
            cmd_domains(args)
        output = out.getvalue()
        for label in ["CLEAR", "COMPLICATED", "COMPLEX", "CHAOTIC", "DISORDER"]:
            assert label in output

    def test_list_empty(self, tmp_path):
        from io import StringIO
        from unittest.mock import patch

        from decisionsupport.cynefin_cli import cmd_list, _build_parser

        db = CynefinDB(db_path=str(tmp_path / "cli_test.db"))
        parser = _build_parser()
        args = parser.parse_args(["list"])
        out = StringIO()
        with patch("sys.stdout", out), patch("decisionsupport.cynefin_cli._db", return_value=db):
            cmd_list(args)
        assert "No decisions" in out.getvalue()

    def test_new_and_show(self, tmp_path):
        from io import StringIO
        from unittest.mock import patch

        from decisionsupport.cynefin_cli import cmd_new, cmd_show, _build_parser

        db = CynefinDB(db_path=str(tmp_path / "cli_test.db"))
        parser = _build_parser()

        # new
        args_new = parser.parse_args(["new", "Pick a database", "-d", "Choosing between Postgres and SQLite"])
        out = StringIO()
        with patch("sys.stdout", out), patch("decisionsupport.cynefin_cli._db", return_value=db):
            cmd_new(args_new)
        new_out = out.getvalue()
        assert "Decision created" in new_out

        # Extract ID from "Decision created: <id>" line
        did = None
        for line in new_out.splitlines():
            if "Decision created:" in line:
                did = line.split(":")[-1].strip()
                break
        assert did is not None, f"Could not find decision ID in: {new_out}"

        # show
        args_show = parser.parse_args(["show", did])
        out2 = StringIO()
        with patch("sys.stdout", out2), patch("decisionsupport.cynefin_cli._db", return_value=db):
            cmd_show(args_show)
        assert "Pick a database" in out2.getvalue()

    def test_import_command(self, tmp_path):
        from io import StringIO
        from unittest.mock import patch

        from decisionsupport.cynefin_cli import cmd_import, _build_parser

        form = tmp_path / "form.json"
        form.write_text(json.dumps({"title": "CLI import test"}))
        db = CynefinDB(db_path=str(tmp_path / "cli_test.db"))
        parser = _build_parser()
        args = parser.parse_args(["import", str(form)])
        out = StringIO()
        with patch("sys.stdout", out), patch("decisionsupport.cynefin_cli._db", return_value=db):
            cmd_import(args)
        assert "imported" in out.getvalue().lower()
