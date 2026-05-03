"""
test_delphi.py — Tests for the Delphi method decision support tool.

Covers: DB CRUD, round lifecycle, responses, items/ratings,
consensus statistics, batch import, export, and CLI smoke tests.
"""
from __future__ import annotations

import json

import pytest

from mempalace.delphi import (
    DelphiDB,
    compute_stats,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    return DelphiDB(db_path=str(tmp_path / "test_delphi.db"))


@pytest.fixture
def session_id(db):
    return db.create_session("Test session", "What should we do about X?")


@pytest.fixture
def session_with_round(db, session_id):
    round_id = db.open_round(session_id)
    return session_id, round_id


# ---------------------------------------------------------------------------
# compute_stats
# ---------------------------------------------------------------------------


class TestComputeStats:
    def test_empty_returns_no_consensus(self):
        stats = compute_stats([])
        assert stats["count"] == 0
        assert stats["consensus"] is False
        assert stats["median"] is None

    def test_single_value(self):
        stats = compute_stats([7.0])
        assert stats["count"] == 1
        assert stats["median"] == 7.0
        assert stats["iqr"] == 0.0
        assert stats["consensus"] is True  # IQR=0 ≤ threshold

    def test_consensus_when_iqr_low(self):
        # Tight cluster → IQR ≤ 2 → consensus
        stats = compute_stats([7, 7, 8, 8, 7])
        assert stats["consensus"] is True

    def test_no_consensus_when_iqr_high(self):
        # Wide spread → IQR > 2 → no consensus
        stats = compute_stats([1, 3, 7, 9, 5, 1, 9])
        assert stats["consensus"] is False

    def test_agreement_agree(self):
        stats = compute_stats([8, 7, 8, 9, 8])
        assert stats["agreement"] == "agree"

    def test_agreement_uncertain(self):
        stats = compute_stats([4, 5, 5, 4, 5])
        assert stats["agreement"] == "uncertain"

    def test_agreement_disagree(self):
        stats = compute_stats([1, 2, 2, 1, 2])
        assert stats["agreement"] == "disagree"

    def test_custom_threshold(self):
        values = [5, 6, 7, 6, 5]
        stats_loose = compute_stats(values, threshold=5.0)
        assert stats_loose["consensus"] is True
        # Strict threshold may or may not yield consensus depending on IQR
        compute_stats(values, threshold=0.5)  # should not raise

    def test_statistics_are_correct(self):
        values = [2, 4, 6, 8]
        stats = compute_stats(values)
        assert stats["mean"] == pytest.approx(5.0)
        assert stats["median"] == pytest.approx(5.0)
        assert stats["min"] == 2.0
        assert stats["max"] == 8.0
        assert stats["count"] == 4


# ---------------------------------------------------------------------------
# DelphiDB — sessions
# ---------------------------------------------------------------------------


class TestSessions:
    def test_create_returns_id(self, db):
        sid = db.create_session("My session", "What to do?")
        assert isinstance(sid, str) and len(sid) == 8

    def test_get_session_roundtrip(self, db):
        sid = db.create_session(
            "Title", "Question?", description="Context", consensus_threshold=1.5
        )
        s = db.get_session(sid)
        assert s["title"] == "Title"
        assert s["question"] == "Question?"
        assert s["description"] == "Context"
        assert s["consensus_threshold"] == 1.5
        assert s["status"] == "active"
        assert s["current_round"] == 0

    def test_get_missing_returns_none(self, db):
        assert db.get_session("nope") is None

    def test_list_all(self, db):
        db.create_session("A", "Q?")
        db.create_session("B", "Q?")
        assert len(db.list_sessions()) == 2

    def test_list_by_status(self, db):
        sid = db.create_session("A", "Q?")
        db.set_status(sid, "completed")
        assert len(db.list_sessions(status="active")) == 0
        assert len(db.list_sessions(status="completed")) == 1

    def test_set_status(self, db, session_id):
        db.set_status(session_id, "completed")
        assert db.get_session(session_id)["status"] == "completed"


# ---------------------------------------------------------------------------
# DelphiDB — rounds
# ---------------------------------------------------------------------------


class TestRounds:
    def test_open_round_increments_counter(self, db, session_id):
        db.open_round(session_id)
        assert db.get_session(session_id)["current_round"] == 1

    def test_open_second_round_requires_close(self, db, session_id):
        db.open_round(session_id)
        with pytest.raises(ValueError, match="already open"):
            db.open_round(session_id)

    def test_open_after_close_works(self, db, session_id):
        db.open_round(session_id)
        db.close_round(session_id)
        round_id = db.open_round(session_id)
        assert db.get_session(session_id)["current_round"] == 2
        assert round_id is not None

    def test_close_round_marks_closed(self, db, session_id):
        db.open_round(session_id)
        round_id = db.close_round(session_id, summary="Key insights")
        rnd = db.get_round(round_id)
        assert rnd["status"] == "closed"
        assert rnd["summary"] == "Key insights"
        assert rnd["closed_at"] is not None

    def test_close_no_open_round_raises(self, db, session_id):
        with pytest.raises(ValueError, match="No open round"):
            db.close_round(session_id)

    def test_get_current_round_returns_none_when_closed(self, db, session_id):
        db.open_round(session_id)
        db.close_round(session_id)
        assert db.get_current_round(session_id) is None

    def test_list_rounds(self, db, session_id):
        db.open_round(session_id)
        db.close_round(session_id)
        db.open_round(session_id)
        rounds = db.list_rounds(session_id)
        assert len(rounds) == 2
        assert rounds[0]["round_number"] == 1
        assert rounds[1]["round_number"] == 2

    def test_open_round_with_prompt(self, db, session_id):
        round_id = db.open_round(session_id, prompt="Rate each item 1–9")
        rnd = db.get_round(round_id)
        assert rnd["prompt"] == "Rate each item 1–9"

    def test_open_round_on_missing_session_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            db.open_round("nope")


# ---------------------------------------------------------------------------
# DelphiDB — responses
# ---------------------------------------------------------------------------


class TestResponses:
    def test_add_and_get_response(self, db, session_with_round):
        sid, round_id = session_with_round
        rid = db.add_response(sid, round_id, "My view is X")
        responses = db.get_responses(sid, round_id=round_id)
        assert len(responses) == 1
        assert responses[0]["id"] == rid
        assert responses[0]["response_text"] == "My view is X"
        assert responses[0]["respondent"] == "anonymous"

    def test_response_with_score(self, db, session_with_round):
        sid, round_id = session_with_round
        db.add_response(sid, round_id, "Agree", score=8.0)
        r = db.get_responses(sid)[0]
        assert r["score"] == 8.0

    def test_invalid_score_raises(self, db, session_with_round):
        sid, round_id = session_with_round
        with pytest.raises(ValueError):
            db.add_response(sid, round_id, "text", score=10.0)  # > RATING_MAX

    def test_get_responses_all(self, db, session_id):
        r1 = db.open_round(session_id)
        db.add_response(session_id, r1, "First response")
        db.close_round(session_id)
        r2 = db.open_round(session_id)
        db.add_response(session_id, r2, "Second response")
        all_resp = db.get_responses(session_id)
        assert len(all_resp) == 2

    def test_named_respondent(self, db, session_with_round):
        sid, round_id = session_with_round
        db.add_response(sid, round_id, "My perspective", respondent="Expert A")
        r = db.get_responses(sid)[0]
        assert r["respondent"] == "Expert A"


# ---------------------------------------------------------------------------
# DelphiDB — items and ratings
# ---------------------------------------------------------------------------


class TestItemsAndRatings:
    def test_add_and_get_item(self, db, session_id):
        item_id = db.add_item(session_id, "Adopt microservices architecture")
        items = db.get_items(session_id)
        assert len(items) == 1
        assert items[0]["id"] == item_id
        assert items[0]["text"] == "Adopt microservices architecture"

    def test_add_rating_roundtrip(self, db, session_with_round):
        sid, round_id = session_with_round
        item_id = db.add_item(sid, "Proposition A")
        rating_id = db.add_rating(sid, round_id, item_id, rating=7.0, rationale="Good fit")
        ratings = db.get_ratings(sid)
        assert len(ratings) == 1
        assert ratings[0]["id"] == rating_id
        assert ratings[0]["rating"] == 7.0
        assert ratings[0]["rationale"] == "Good fit"

    def test_rating_out_of_range_raises(self, db, session_with_round):
        sid, round_id = session_with_round
        item_id = db.add_item(sid, "Item")
        with pytest.raises(ValueError):
            db.add_rating(sid, round_id, item_id, rating=10.0)  # > 9
        with pytest.raises(ValueError):
            db.add_rating(sid, round_id, item_id, rating=0.0)  # < 1

    def test_get_ratings_by_item(self, db, session_with_round):
        sid, round_id = session_with_round
        item1 = db.add_item(sid, "Item 1")
        item2 = db.add_item(sid, "Item 2")
        db.add_rating(sid, round_id, item1, rating=7.0)
        db.add_rating(sid, round_id, item2, rating=3.0)
        assert len(db.get_ratings(sid, item_id=item1)) == 1
        assert len(db.get_ratings(sid, item_id=item2)) == 1


# ---------------------------------------------------------------------------
# DelphiDB — round_statistics and consensus
# ---------------------------------------------------------------------------


class TestStatistics:
    def _seed_ratings(self, db, sid, round_id, item_id, values):
        for v in values:
            db.add_rating(sid, round_id, item_id, rating=float(v))

    def test_round_statistics_basic(self, db, session_with_round):
        sid, round_id = session_with_round
        item_id = db.add_item(sid, "Proposition A")
        self._seed_ratings(db, sid, round_id, item_id, [7, 8, 7, 8, 7])
        stats = db.round_statistics(sid, round_id)
        assert len(stats) == 1
        s = stats[0]
        assert s["item_id"] == item_id
        assert s["count"] == 5
        assert s["consensus"] is True

    def test_round_statistics_no_consensus(self, db, session_with_round):
        sid, round_id = session_with_round
        item_id = db.add_item(sid, "Controversial item")
        self._seed_ratings(db, sid, round_id, item_id, [1, 9, 1, 9, 5])
        stats = db.round_statistics(sid, round_id)
        assert stats[0]["consensus"] is False

    def test_round_statistics_empty_round(self, db, session_with_round):
        sid, round_id = session_with_round
        stats = db.round_statistics(sid, round_id)
        assert stats == []

    def test_session_consensus_summary_no_closed_rounds(self, db, session_id):
        summary = db.session_consensus_summary(session_id)
        assert summary["item_stats"] == []
        assert summary["all_consensus"] is False

    def test_session_consensus_summary_all_agree(self, db, session_id):
        round_id = db.open_round(session_id)
        item_id = db.add_item(session_id, "Agreed item")
        for v in [8, 8, 9, 7, 8]:
            db.add_rating(session_id, round_id, item_id, rating=float(v))
        db.close_round(session_id)
        summary = db.session_consensus_summary(session_id)
        assert summary["all_consensus"] is True
        assert summary["item_stats"][0]["consensus"] is True

    def test_session_consensus_summary_uses_last_round(self, db, session_id):
        # Round 1: no consensus
        r1 = db.open_round(session_id)
        item_id = db.add_item(session_id, "Item")
        for v in [1, 9, 1, 9]:
            db.add_rating(session_id, r1, item_id, rating=float(v))
        db.close_round(session_id)
        # Round 2: consensus
        r2 = db.open_round(session_id)
        for v in [8, 8, 7, 8]:
            db.add_rating(session_id, r2, item_id, rating=float(v))
        db.close_round(session_id)
        summary = db.session_consensus_summary(session_id)
        assert summary["as_of_round"] == 2
        assert summary["all_consensus"] is True


# ---------------------------------------------------------------------------
# DelphiDB — batch import
# ---------------------------------------------------------------------------


class TestBatchImport:
    def test_import_text_responses(self, db, session_with_round, tmp_path):
        sid, _ = session_with_round
        form = tmp_path / "r.json"
        form.write_text(
            json.dumps(
                {
                    "respondent": "Expert A",
                    "responses": [
                        {"text": "I think approach A is best because it scales."},
                        {"text": "Approach B has lower upfront cost.", "score": 6},
                    ],
                }
            )
        )
        n_resp, n_rate = db.import_responses_json(sid, str(form))
        assert n_resp == 2
        assert n_rate == 0
        responses = db.get_responses(sid)
        assert len(responses) == 2
        assert responses[0]["respondent"] == "Expert A"

    def test_import_ratings(self, db, session_with_round, tmp_path):
        sid, round_id = session_with_round
        item_id = db.add_item(sid, "Test item")
        form = tmp_path / "r.json"
        form.write_text(
            json.dumps(
                {
                    "ratings": [{"item_id": item_id, "rating": 8, "rationale": "Strong fit"}]
                }
            )
        )
        n_resp, n_rate = db.import_responses_json(sid, str(form))
        assert n_resp == 0
        assert n_rate == 1
        ratings = db.get_ratings(sid)
        assert ratings[0]["rating"] == 8.0

    def test_import_no_open_round_raises(self, db, session_id, tmp_path):
        form = tmp_path / "r.json"
        form.write_text(json.dumps({"responses": [{"text": "Hello"}]}))
        with pytest.raises(ValueError, match="No open round"):
            db.import_responses_json(session_id, str(form))

    def test_import_missing_session_raises(self, db, tmp_path):
        form = tmp_path / "r.json"
        form.write_text(json.dumps({}))
        with pytest.raises(ValueError, match="not found"):
            db.import_responses_json("nope", str(form))

    def test_import_skips_invalid_entries(self, db, session_with_round, tmp_path):
        sid, _ = session_with_round
        form = tmp_path / "r.json"
        form.write_text(
            json.dumps(
                {
                    "responses": [
                        {"text": ""},  # empty — should skip
                        {"text": "Valid response"},
                    ],
                    "ratings": [
                        {"item_id": "", "rating": 7},  # missing item_id — skip
                    ],
                }
            )
        )
        n_resp, n_rate = db.import_responses_json(sid, str(form))
        assert n_resp == 1
        assert n_rate == 0


# ---------------------------------------------------------------------------
# DelphiDB — notes and export
# ---------------------------------------------------------------------------


class TestNotesAndExport:
    def test_add_note(self, db, session_id):
        nid = db.add_note(session_id, "Facilitator observed strong disagreement")
        notes = db.get_notes(session_id)
        assert len(notes) == 1
        assert notes[0]["id"] == nid

    def test_export_includes_all_sections(self, db, session_id):
        round_id = db.open_round(session_id)
        db.add_response(session_id, round_id, "Response text")
        item_id = db.add_item(session_id, "Item A")
        db.add_rating(session_id, round_id, item_id, rating=7.0)
        db.add_note(session_id, "A note")
        db.close_round(session_id)
        data = db.export_session(session_id)
        assert "session" in data
        assert "rounds" in data
        assert "responses" in data
        assert "items" in data
        assert "ratings" in data
        assert "notes" in data
        assert "consensus_summary" in data

    def test_export_missing_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            db.export_session("nope")


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class TestDelphiCLI:
    def _run(self, args, db):
        from io import StringIO
        from unittest.mock import patch

        from mempalace.delphi_cli import _build_parser

        parser = _build_parser()
        parsed = parser.parse_args(args)
        out = StringIO()
        with patch("sys.stdout", out), patch("mempalace.delphi_cli._db", return_value=db):
            if parsed.command == "round":
                from mempalace.delphi_cli import cmd_round

                cmd_round(parsed)
            else:
                dispatch = {
                    "new": "cmd_new",
                    "respond": "cmd_respond",
                    "item": "cmd_item",
                    "rate": "cmd_rate",
                    "summarize": "cmd_summarize",
                    "list": "cmd_list",
                    "show": "cmd_show",
                    "note": "cmd_note",
                    "export": "cmd_export",
                }
                import mempalace.delphi_cli as dcli

                getattr(dcli, dispatch[parsed.command])(parsed)
        return out.getvalue()

    def test_new_creates_session(self, tmp_path):
        db = DelphiDB(db_path=str(tmp_path / "cli.db"))
        out = self._run(["new", "Pricing strategy", "Which pricing model?"], db)
        assert "Session created" in out
        assert len(db.list_sessions()) == 1

    def test_list_empty(self, tmp_path):
        db = DelphiDB(db_path=str(tmp_path / "cli.db"))
        out = self._run(["list"], db)
        assert "No sessions" in out

    def test_round_open_and_close(self, tmp_path):
        db = DelphiDB(db_path=str(tmp_path / "cli.db"))
        sid = db.create_session("T", "Q?")
        out_open = self._run(["round", "open", sid], db)
        assert "Round 1 opened" in out_open
        out_close = self._run(["round", "close", sid], db)
        assert "Round closed" in out_close

    def test_respond_records(self, tmp_path):
        db = DelphiDB(db_path=str(tmp_path / "cli.db"))
        sid = db.create_session("T", "Q?")
        db.open_round(sid)
        out = self._run(["respond", sid, "My detailed expert perspective on the matter"], db)
        assert "Response recorded" in out
        assert len(db.get_responses(sid)) == 1

    def test_item_and_rate(self, tmp_path):
        db = DelphiDB(db_path=str(tmp_path / "cli.db"))
        sid = db.create_session("T", "Q?")
        db.open_round(sid)
        item_out = self._run(["item", sid, "Adopt approach A"], db)
        assert "Item added" in item_out
        item_id = db.get_items(sid)[0]["id"]
        rate_out = self._run(["rate", sid, item_id, "8"], db)
        assert "Rating recorded" in rate_out

    def test_summarize_no_closed_rounds(self, tmp_path):
        db = DelphiDB(db_path=str(tmp_path / "cli.db"))
        sid = db.create_session("T", "Q?")
        out = self._run(["summarize", sid], db)
        assert "No rated items" in out

    def test_export_to_stdout(self, tmp_path):
        db = DelphiDB(db_path=str(tmp_path / "cli.db"))
        sid = db.create_session("T", "Q?")
        from io import StringIO
        from unittest.mock import patch

        from mempalace.delphi_cli import cmd_export, _build_parser

        parser = _build_parser()
        args = parser.parse_args(["export", sid])
        out = StringIO()
        with patch("sys.stdout", out), patch("mempalace.delphi_cli._db", return_value=db):
            cmd_export(args)
        data = json.loads(out.getvalue())
        assert "session" in data
