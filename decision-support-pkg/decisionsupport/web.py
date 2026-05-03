#!/usr/bin/env python3
"""
Decision Support — Web Interface

Flask application wrapping Cynefin and Delphi decision support tools.

Environment variables:
    DECISION_SUPPORT_DATA_DIR   Path for SQLite databases (default: ~/.decisionsupport)
    SECRET_KEY                  Flask secret key — change in production
    PORT                        Port to bind (Railway sets this automatically)
    FLASK_DEBUG                 Set to 'true' to enable debug mode
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import traceback

from flask import Flask, flash, redirect, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from .cynefin import (
    DOMAIN_INFO,
    QUESTIONS,
    CynefinDB,
    classify_from_responses,
    ingest_form_json,
    ingest_transcript,
)
from .decision import FRAMEWORKS, get_framework, set_framework, toggle
from .delphi import DEFAULT_CONSENSUS_THRESHOLD, RATING_MAX, RATING_MIN, DelphiDB

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(os.environ.get("DECISION_SUPPORT_DATA_DIR", str(Path.home() / ".decisionsupport")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

_CYNEFIN_DB_PATH = str(DATA_DIR / "cynefin.db")
_DELPHI_DB_PATH = str(DATA_DIR / "delphi.db")
_FRAMEWORK_CONFIG = DATA_DIR / "decision_framework"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me-in-production")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


@app.errorhandler(500)
def _internal_error(e):
    return f"<pre>{traceback.format_exc()}</pre>", 500


def _cdb() -> CynefinDB:
    return CynefinDB(db_path=_CYNEFIN_DB_PATH)


def _ddb() -> DelphiDB:
    return DelphiDB(db_path=_DELPHI_DB_PATH)


def _fw() -> str:
    return get_framework(config_path=_FRAMEWORK_CONFIG)


@app.context_processor
def _inject_globals():
    return {"active_framework": _fw(), "frameworks": FRAMEWORKS}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.route("/health")
def health():
    return {"status": "ok"}, 200


# ---------------------------------------------------------------------------
# Dashboard and framework toggle
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    decisions = _cdb().list_decisions()[:6]
    sessions = _ddb().list_sessions()[:6]
    return render_template("index.html", decisions=decisions, sessions=sessions)


@app.route("/toggle", methods=["POST"])
def toggle_framework():
    new_fw = toggle(config_path=_FRAMEWORK_CONFIG)
    flash(f"Switched to {new_fw.title()}", "info")
    return redirect(request.referrer or url_for("index"))


@app.route("/framework/<name>", methods=["POST"])
def set_framework_route(name: str):
    try:
        set_framework(name, config_path=_FRAMEWORK_CONFIG)
        flash(f"Framework set to {name.title()}", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Cynefin routes
# ---------------------------------------------------------------------------


@app.route("/cynefin/")
def cynefin_list():
    status = request.args.get("status")
    decisions = _cdb().list_decisions(status=status or None)
    return render_template("cynefin/list.html", decisions=decisions, status_filter=status)


@app.route("/cynefin/new", methods=["GET", "POST"])
def cynefin_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if not title:
            flash("Title is required", "danger")
            return render_template("cynefin/new.html")
        did = _cdb().create_decision(title=title, description=description)
        flash(f"Decision created: {did}", "success")
        return redirect(url_for("cynefin_show", decision_id=did))
    return render_template("cynefin/new.html")


@app.route("/cynefin/<decision_id>")
def cynefin_show(decision_id: str):
    db = _cdb()
    decision = db.get_decision(decision_id)
    if decision is None:
        flash(f"Decision '{decision_id}' not found", "danger")
        return redirect(url_for("cynefin_list"))
    responses = db.get_responses(decision_id)
    notes = db.get_notes(decision_id)
    actions = db.get_actions(decision_id)
    answered_ids = {r["question_id"] for r in responses}
    domain_info = DOMAIN_INFO.get(decision.get("domain") or "", {})
    scores = None
    if responses:
        _, scores = classify_from_responses(responses)
    return render_template(
        "cynefin/show.html",
        decision=decision,
        responses=responses,
        notes=notes,
        actions=actions,
        answered_ids=answered_ids,
        questions=QUESTIONS,
        domain_info=domain_info,
        scores=scores,
        domain_info_all=DOMAIN_INFO,
    )


@app.route("/cynefin/<decision_id>/assess", methods=["POST"])
def cynefin_assess(decision_id: str):
    db = _cdb()
    if db.get_decision(decision_id) is None:
        flash("Decision not found", "danger")
        return redirect(url_for("cynefin_list"))
    q_map = {q["id"]: q for q in QUESTIONS}
    answered = 0
    for question_id, q in q_map.items():
        answer_key = request.form.get(question_id, "").strip()
        if not answer_key:
            continue
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
        answered += 1
    if answered:
        responses = db.get_responses(decision_id)
        domain, _ = classify_from_responses(responses)
        db.set_domain(decision_id, domain)
        label = DOMAIN_INFO.get(domain, {}).get("label", domain.title())
        flash(f"Assessment saved — domain: {label}", "success")
    return redirect(url_for("cynefin_show", decision_id=decision_id))


@app.route("/cynefin/<decision_id>/note", methods=["POST"])
def cynefin_note(decision_id: str):
    content = request.form.get("content", "").strip()
    if content:
        _cdb().add_note(decision_id, content)
        flash("Note added", "success")
    return redirect(url_for("cynefin_show", decision_id=decision_id))


@app.route("/cynefin/<decision_id>/action", methods=["POST"])
def cynefin_action(decision_id: str):
    action_text = request.form.get("action_text", "").strip()
    if action_text:
        _cdb().add_action(decision_id, action_text)
        flash("Action recorded", "success")
    return redirect(url_for("cynefin_show", decision_id=decision_id))


@app.route("/cynefin/<decision_id>/status", methods=["POST"])
def cynefin_status(decision_id: str):
    status = request.form.get("status", "").strip()
    if status in ("open", "decided", "closed"):
        _cdb().set_status(decision_id, status)
        flash(f"Status updated to '{status}'", "success")
    return redirect(url_for("cynefin_show", decision_id=decision_id))


@app.route("/cynefin/import", methods=["GET", "POST"])
def cynefin_import():
    if request.method == "POST":
        f = request.files.get("form_json")
        if not f or not f.filename:
            flash("Please select a JSON file", "danger")
            return render_template("cynefin/import.html")
        suffix = Path(f.filename).suffix or ".json"
        tmp_path = tempfile.mktemp(suffix=suffix)
        try:
            f.save(tmp_path)
            did = ingest_form_json(_cdb(), tmp_path)
            flash(f"Decision imported: {did}", "success")
            return redirect(url_for("cynefin_show", decision_id=did))
        except (ValueError, OSError) as exc:
            flash(str(exc), "danger")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    return render_template("cynefin/import.html")


@app.route("/cynefin/<decision_id>/ingest", methods=["POST"])
def cynefin_ingest(decision_id: str):
    f = request.files.get("transcript")
    if not f or not f.filename:
        flash("Please select a transcript file", "danger")
        return redirect(url_for("cynefin_show", decision_id=decision_id))
    fmt = request.form.get("format", "auto")
    suffix = Path(f.filename).suffix or ".txt"
    tmp_path = tempfile.mktemp(suffix=suffix)
    try:
        f.save(tmp_path)
        count = ingest_transcript(_cdb(), decision_id, tmp_path, fmt=fmt)
        flash(f"{count} note(s) ingested from transcript", "success")
    except (OSError, ValueError) as exc:
        flash(str(exc), "danger")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return redirect(url_for("cynefin_show", decision_id=decision_id))


# ---------------------------------------------------------------------------
# Delphi routes
# ---------------------------------------------------------------------------


@app.route("/delphi/")
def delphi_list():
    status = request.args.get("status")
    sessions = _ddb().list_sessions(status=status or None)
    return render_template("delphi/list.html", sessions=sessions, status_filter=status)


@app.route("/delphi/new", methods=["GET", "POST"])
def delphi_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        question = request.form.get("question", "").strip()
        description = request.form.get("description", "").strip()
        try:
            threshold = float(request.form.get("threshold") or DEFAULT_CONSENSUS_THRESHOLD)
        except ValueError:
            threshold = DEFAULT_CONSENSUS_THRESHOLD
        if not title or not question:
            flash("Title and question are required", "danger")
            return render_template("delphi/new.html", default_threshold=DEFAULT_CONSENSUS_THRESHOLD)
        sid = _ddb().create_session(
            title=title,
            question=question,
            description=description,
            consensus_threshold=threshold,
        )
        flash(f"Session created: {sid}", "success")
        return redirect(url_for("delphi_show", session_id=sid))
    return render_template("delphi/new.html", default_threshold=DEFAULT_CONSENSUS_THRESHOLD)


@app.route("/delphi/<session_id>")
def delphi_show(session_id: str):
    db = _ddb()
    session = db.get_session(session_id)
    if session is None:
        flash(f"Session '{session_id}' not found", "danger")
        return redirect(url_for("delphi_list"))
    rounds = db.list_rounds(session_id)
    current_round = db.get_current_round(session_id)
    items = db.get_items(session_id)
    notes = db.get_notes(session_id)
    responses = db.get_responses(session_id, round_id=current_round["id"]) if current_round else []
    consensus = db.session_consensus_summary(session_id)
    current_stats = db.round_statistics(session_id, current_round["id"]) if current_round else []
    stats_by_item = {s["item_id"]: s for s in current_stats}
    return render_template(
        "delphi/show.html",
        session=session,
        rounds=rounds,
        current_round=current_round,
        items=items,
        notes=notes,
        responses=responses,
        consensus=consensus,
        stats_by_item=stats_by_item,
        rating_min=RATING_MIN,
        rating_max=RATING_MAX,
    )


@app.route("/delphi/<session_id>/round/open", methods=["POST"])
def delphi_round_open(session_id: str):
    prompt = request.form.get("prompt", "").strip()
    try:
        _ddb().open_round(session_id, prompt=prompt)
        flash("Round opened", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("delphi_show", session_id=session_id))


@app.route("/delphi/<session_id>/round/close", methods=["POST"])
def delphi_round_close(session_id: str):
    summary = request.form.get("summary", "").strip()
    try:
        _ddb().close_round(session_id, summary=summary)
        flash("Round closed", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("delphi_show", session_id=session_id))


@app.route("/delphi/<session_id>/respond", methods=["POST"])
def delphi_respond(session_id: str):
    db = _ddb()
    current_round = db.get_current_round(session_id)
    if not current_round:
        flash("No open round — open one first", "danger")
        return redirect(url_for("delphi_show", session_id=session_id))
    text = request.form.get("text", "").strip()
    if not text:
        flash("Response text is required", "danger")
        return redirect(url_for("delphi_show", session_id=session_id))
    score_raw = request.form.get("score", "").strip()
    score = float(score_raw) if score_raw else None
    respondent = request.form.get("respondent", "anonymous").strip() or "anonymous"
    try:
        db.add_response(session_id, current_round["id"], text, respondent=respondent, score=score)
        flash("Response recorded", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("delphi_show", session_id=session_id))


@app.route("/delphi/<session_id>/item", methods=["POST"])
def delphi_item(session_id: str):
    db = _ddb()
    session = db.get_session(session_id)
    text = request.form.get("text", "").strip()
    if not text:
        flash("Item text is required", "danger")
        return redirect(url_for("delphi_show", session_id=session_id))
    db.add_item(session_id, text, source_round=session["current_round"])
    flash("Item added", "success")
    return redirect(url_for("delphi_show", session_id=session_id))


@app.route("/delphi/<session_id>/rate", methods=["POST"])
def delphi_rate(session_id: str):
    db = _ddb()
    current_round = db.get_current_round(session_id)
    if not current_round:
        flash("No open round", "danger")
        return redirect(url_for("delphi_show", session_id=session_id))
    respondent = request.form.get("respondent", "anonymous").strip() or "anonymous"
    items = db.get_items(session_id)
    rated = 0
    for item in items:
        rating_raw = request.form.get(f"rating_{item['id']}", "").strip()
        if not rating_raw:
            continue
        try:
            db.add_rating(
                session_id,
                current_round["id"],
                item["id"],
                rating=float(rating_raw),
                respondent=respondent,
                rationale=request.form.get(f"rationale_{item['id']}", "").strip(),
            )
            rated += 1
        except (ValueError, TypeError):
            pass
    if rated:
        flash(f"{rated} rating(s) recorded", "success")
    return redirect(url_for("delphi_show", session_id=session_id))


@app.route("/delphi/<session_id>/note", methods=["POST"])
def delphi_note(session_id: str):
    content = request.form.get("content", "").strip()
    if content:
        _ddb().add_note(session_id, content)
        flash("Note added", "success")
    return redirect(url_for("delphi_show", session_id=session_id))


@app.route("/delphi/<session_id>/import", methods=["POST"])
def delphi_import(session_id: str):
    f = request.files.get("responses_json")
    if not f or not f.filename:
        flash("Please select a JSON file", "danger")
        return redirect(url_for("delphi_show", session_id=session_id))
    tmp_path = tempfile.mktemp(suffix=".json")
    try:
        f.save(tmp_path)
        n_resp, n_rate = _ddb().import_responses_json(session_id, tmp_path)
        flash(f"Imported {n_resp} response(s) and {n_rate} rating(s)", "success")
    except (ValueError, OSError) as exc:
        flash(str(exc), "danger")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return redirect(url_for("delphi_show", session_id=session_id))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)


if __name__ == "__main__":
    main()
