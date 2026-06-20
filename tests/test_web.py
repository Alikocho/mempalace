"""
test_web.py — Smoke tests for the Flask web interface.

Uses Flask's built-in test client; no real HTTP calls are made.
All DB operations are isolated to tmp_path via environment variable.
"""
from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    # Re-import web so it picks up the patched env var
    import importlib
    import mempalace.web as web_module
    importlib.reload(web_module)

    web_module.app.config["TESTING"] = True
    web_module.app.config["WTF_CSRF_ENABLED"] = False
    return web_module.app


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert json.loads(resp.data)["status"] == "ok"


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    def test_index_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Decision Support" in resp.data

    def test_index_shows_both_frameworks(self, client):
        resp = client.get("/")
        assert b"Cynefin" in resp.data
        assert b"Delphi" in resp.data

    def test_toggle_redirects(self, client):
        resp = client.post("/toggle")
        assert resp.status_code == 302

    def test_set_framework_cynefin(self, client):
        resp = client.post("/framework/cynefin")
        assert resp.status_code == 302

    def test_set_framework_delphi(self, client):
        resp = client.post("/framework/delphi")
        assert resp.status_code == 302

    def test_set_unknown_framework_still_redirects(self, client):
        resp = client.post("/framework/bogus")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Cynefin routes
# ---------------------------------------------------------------------------


class TestCynefinRoutes:
    def test_list_empty(self, client):
        resp = client.get("/cynefin/")
        assert resp.status_code == 200
        assert b"No decisions found" in resp.data

    def test_new_get(self, client):
        resp = client.get("/cynefin/new")
        assert resp.status_code == 200
        assert b"form" in resp.data

    def test_new_post_creates_and_redirects(self, client):
        resp = client.post("/cynefin/new", data={"title": "Test decision", "description": "ctx"})
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/cynefin/" in location

    def test_new_post_missing_title_stays(self, client):
        resp = client.post("/cynefin/new", data={"title": "", "description": "ctx"})
        assert resp.status_code == 200  # re-renders form

    def test_show_after_create(self, client):
        resp = client.post("/cynefin/new", data={"title": "My decision"}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"My decision" in resp.data

    def test_show_missing_redirects(self, client):
        resp = client.get("/cynefin/notexist")
        assert resp.status_code == 302

    def test_assess_saves_and_redirects(self, client):
        # Create a decision first
        resp = client.post("/cynefin/new", data={"title": "D1"})
        did = resp.headers["Location"].rstrip("/").split("/")[-1]

        # Submit all 7 answers
        from mempalace.cynefin import QUESTIONS
        data = {q["id"]: q["answers"][0]["key"] for q in QUESTIONS}
        resp = client.post(f"/cynefin/{did}/assess", data=data)
        assert resp.status_code == 302

    def test_assess_triggers_classification(self, client):
        resp = client.post("/cynefin/new", data={"title": "D2"})
        did = resp.headers["Location"].rstrip("/").split("/")[-1]

        from mempalace.cynefin import QUESTIONS
        data = {q["id"]: q["answers"][0]["key"] for q in QUESTIONS}
        client.post(f"/cynefin/{did}/assess", data=data)

        resp = client.get(f"/cynefin/{did}")
        # Domain should now be set — badge visible
        assert b"domain-" in resp.data

    def test_note_adds_and_redirects(self, client):
        resp = client.post("/cynefin/new", data={"title": "D3"})
        did = resp.headers["Location"].rstrip("/").split("/")[-1]
        resp = client.post(f"/cynefin/{did}/note", data={"content": "Important context"})
        assert resp.status_code == 302

    def test_action_adds_and_redirects(self, client):
        resp = client.post("/cynefin/new", data={"title": "D4"})
        did = resp.headers["Location"].rstrip("/").split("/")[-1]
        resp = client.post(f"/cynefin/{did}/action", data={"action_text": "Do something"})
        assert resp.status_code == 302

    def test_status_change_redirects(self, client):
        resp = client.post("/cynefin/new", data={"title": "D5"})
        did = resp.headers["Location"].rstrip("/").split("/")[-1]
        resp = client.post(f"/cynefin/{did}/status", data={"status": "decided"})
        assert resp.status_code == 302

    def test_list_shows_created_decision(self, client):
        client.post("/cynefin/new", data={"title": "Visible decision"})
        resp = client.get("/cynefin/")
        assert b"Visible decision" in resp.data

    def test_import_get(self, client):
        resp = client.get("/cynefin/import")
        assert resp.status_code == 200

    def test_import_post_valid_json(self, client, tmp_path):
        form_file = tmp_path / "form.json"
        form_file.write_text(json.dumps({"title": "Imported decision"}))
        with open(form_file, "rb") as fh:
            resp = client.post(
                "/cynefin/import",
                data={"form_json": (fh, "form.json")},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Delphi routes
# ---------------------------------------------------------------------------


class TestDelphiRoutes:
    def _create_session(self, client, title="Test session", question="What to do?"):
        resp = client.post("/delphi/new", data={"title": title, "question": question})
        assert resp.status_code == 302
        return resp.headers["Location"].rstrip("/").split("/")[-1]

    def test_list_empty(self, client):
        resp = client.get("/delphi/")
        assert resp.status_code == 200
        assert b"No sessions found" in resp.data

    def test_new_get(self, client):
        resp = client.get("/delphi/new")
        assert resp.status_code == 200
        assert b"question" in resp.data

    def test_new_post_creates_and_redirects(self, client):
        resp = client.post("/delphi/new", data={"title": "T", "question": "Q?"})
        assert resp.status_code == 302

    def test_new_post_missing_question_stays(self, client):
        resp = client.post("/delphi/new", data={"title": "T", "question": ""})
        assert resp.status_code == 200

    def test_show_after_create(self, client):
        sid = self._create_session(client, "My session")
        resp = client.get(f"/delphi/{sid}")
        assert resp.status_code == 200
        assert b"My session" in resp.data

    def test_show_missing_redirects(self, client):
        resp = client.get("/delphi/notexist")
        assert resp.status_code == 302

    def test_round_open_and_close(self, client):
        sid = self._create_session(client)
        resp = client.post(f"/delphi/{sid}/round/open", data={"prompt": "Tell us your view"})
        assert resp.status_code == 302
        resp = client.post(f"/delphi/{sid}/round/close", data={"summary": "Key themes"})
        assert resp.status_code == 302

    def test_round_close_no_open_flashes_error(self, client):
        sid = self._create_session(client)
        resp = client.post(f"/delphi/{sid}/round/close", follow_redirects=True)
        assert b"No open round" in resp.data

    def test_respond_requires_open_round(self, client):
        sid = self._create_session(client)
        resp = client.post(f"/delphi/{sid}/respond",
                           data={"text": "My response"}, follow_redirects=True)
        assert b"No open round" in resp.data

    def test_respond_records(self, client):
        sid = self._create_session(client)
        client.post(f"/delphi/{sid}/round/open", data={})
        resp = client.post(f"/delphi/{sid}/respond",
                           data={"text": "Expert opinion here"}, follow_redirects=True)
        assert b"Expert opinion here" in resp.data

    def test_item_adds(self, client):
        sid = self._create_session(client)
        client.post(f"/delphi/{sid}/round/open", data={})
        resp = client.post(f"/delphi/{sid}/item",
                           data={"text": "Adopt tiered pricing"}, follow_redirects=True)
        assert b"Adopt tiered pricing" in resp.data

    def test_rate_records(self, client):
        sid = self._create_session(client)
        client.post(f"/delphi/{sid}/round/open", data={})
        # Add item first
        client.post(f"/delphi/{sid}/item", data={"text": "Item A"})
        # Get item_id from DB
        import mempalace.web as wm
        db = wm._ddb()
        items = db.get_items(sid)
        assert len(items) == 1
        item_id = items[0]["id"]
        resp = client.post(
            f"/delphi/{sid}/rate",
            data={"respondent": "Expert", f"rating_{item_id}": "8"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_note_adds(self, client):
        sid = self._create_session(client)
        resp = client.post(f"/delphi/{sid}/note",
                           data={"content": "Facilitator note"}, follow_redirects=True)
        assert b"Facilitator note" in resp.data

    def test_list_shows_session(self, client):
        self._create_session(client, "Visible session")
        resp = client.get("/delphi/")
        assert b"Visible session" in resp.data

    def test_import_no_file_flashes_error(self, client):
        sid = self._create_session(client)
        resp = client.post(f"/delphi/{sid}/import",
                           data={}, content_type="multipart/form-data",
                           follow_redirects=True)
        assert b"Please select" in resp.data

    def test_import_json_responses(self, client, tmp_path):
        sid = self._create_session(client)
        client.post(f"/delphi/{sid}/round/open", data={})
        form_file = tmp_path / "responses.json"
        form_file.write_text(json.dumps({
            "respondent": "Alice",
            "responses": [{"text": "My detailed expert view on the topic."}],
        }))
        with open(form_file, "rb") as fh:
            resp = client.post(
                f"/delphi/{sid}/import",
                data={"responses_json": (fh, "responses.json")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        assert b"Imported" in resp.data
