#!/usr/bin/env python3
"""
Decision Support — Web Interface

Environment variables:
    DECISION_SUPPORT_DATA_DIR   Path for SQLite databases (default: ~/.decisionsupport)
    SECRET_KEY                  Flask secret key — change in production
    ADMIN_SECRET                Password to access the admin interface
    PORT                        Port to bind (Railway sets this automatically)
    FLASK_DEBUG                 Set to 'true' to enable debug mode
"""
from __future__ import annotations

import functools
import os
import tempfile
import traceback
from pathlib import Path

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix

from .cynefin import (
    DOMAIN_INFO,
    QUESTIONS,
    CynefinDB,
    classify_from_responses,
    ingest_transcript,
)
from .decision import FRAMEWORKS, get_framework, set_framework, toggle
from .delphi import DEFAULT_CONSENSUS_THRESHOLD, RATING_MAX, RATING_MIN, DelphiDB
from .premortem import LIKELIHOOD_LABELS, SEVERITY_LABELS, PreMortemDB
from .decision_matrix import SCORE_MAX, SCORE_MIN, WEIGHT_MAX, WEIGHT_MIN, DecisionMatrixDB, compute_results
from .sixhats import HAT_INFO, HATS_ORDER, SixHatsDB

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(os.environ.get("DECISION_SUPPORT_DATA_DIR", str(Path.home() / ".decisionsupport")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

_CYNEFIN_DB_PATH = str(DATA_DIR / "cynefin.db")
_DELPHI_DB_PATH = str(DATA_DIR / "delphi.db")
_PREMORTEM_DB_PATH = str(DATA_DIR / "premortem.db")
_MATRIX_DB_PATH = str(DATA_DIR / "decision_matrix.db")
_SIXHATS_DB_PATH = str(DATA_DIR / "sixhats.db")

_FRAMEWORK_CONFIG = DATA_DIR / "decision_framework"

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me-in-production")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


@app.errorhandler(500)
def _internal_error(e):
    return f"<pre>{traceback.format_exc()}</pre>", 500


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------


def _is_admin() -> bool:
    """True when no ADMIN_SECRET is configured (open mode) or user is authenticated."""
    if not ADMIN_SECRET or app.testing:
        return True
    return flask_session.get("is_admin") is True


def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not _is_admin():
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return decorated


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_SECRET:
        return redirect(url_for("index"))
    if _is_admin():
        return redirect(url_for("index"))
    if request.method == "POST":
        if request.form.get("secret", "") == ADMIN_SECRET:
            flask_session["is_admin"] = True
            flask_session.permanent = True
            return redirect(request.form.get("next") or url_for("index"))
        flash("Incorrect password", "danger")
    return render_template("admin/login.html", next=request.args.get("next", ""))


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    flask_session.pop("is_admin", None)
    return redirect(url_for("admin_login"))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _cdb() -> CynefinDB:
    return CynefinDB(db_path=_CYNEFIN_DB_PATH)


def _ddb() -> DelphiDB:
    return DelphiDB(db_path=_DELPHI_DB_PATH)


def _pmdb() -> PreMortemDB:
    return PreMortemDB(db_path=_PREMORTEM_DB_PATH)


def _mxdb() -> DecisionMatrixDB:
    return DecisionMatrixDB(db_path=_MATRIX_DB_PATH)


def _shdb() -> SixHatsDB:
    return SixHatsDB(db_path=_SIXHATS_DB_PATH)



def _fw() -> str:
    return get_framework(config_path=_FRAMEWORK_CONFIG)


@app.context_processor
def _inject_globals():
    return {"active_framework": _fw(), "frameworks": FRAMEWORKS, "is_admin": _is_admin(), "admin_secret_set": bool(ADMIN_SECRET)}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/guide")
def guide():
    return render_template("guide.html")


# ---------------------------------------------------------------------------
# Dashboard and framework toggle
# ---------------------------------------------------------------------------


@app.route("/")
@admin_required
def index():
    decisions = _cdb().list_decisions()[:6]
    sessions = _ddb().list_sessions()[:6]

    pm_raw = _pmdb().list_sessions()[:6]
    pm_sessions = [{"session": s, "scenario_count": len(_pmdb().get_scenarios(s["id"]))} for s in pm_raw]

    mx_raw = _mxdb().list_sessions()[:6]
    mx_sessions = [{"session": s, "id": s["id"], "title": s["title"], "option_count": len(_mxdb().get_options(s["id"]))} for s in mx_raw]

    sh_raw = _shdb().list_sessions()[:6]
    sh_sessions = [{"session": s, "id": s["id"], "title": s["title"], "current_hat": s.get("current_hat")} for s in sh_raw]

    return render_template(
        "index.html",
        decisions=decisions,
        sessions=sessions,
        pm_sessions=pm_sessions,
        mx_sessions=mx_sessions,
        sh_sessions=sh_sessions,
    )


@app.route("/results")
@admin_required
def results():
    import datetime

    cdb = _cdb()
    ddb = _ddb()
    pmdb = _pmdb()
    mxdb = _mxdb()
    shdb = _shdb()

    all_decisions = cdb.list_decisions()
    domain_counts = {"clear": 0, "complicated": 0, "complex": 0, "chaotic": 0, "disorder": 0}
    status_counts = {"open": 0, "decided": 0, "deferred": 0}
    decisions_detail = []
    for d in all_decisions:
        domain = d["domain"] or "disorder"
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        status_counts[d["status"]] = status_counts.get(d["status"], 0) + 1
        actions = cdb.get_actions(d["id"])
        decisions_detail.append({
            "decision": d,
            "actions": actions,
            "pending_actions": sum(1 for a in actions if not a["resolved_at"]),
        })

    all_sessions = ddb.list_sessions()
    sessions_detail = []
    consensus_count = 0
    for s in all_sessions:
        summary = ddb.session_consensus_summary(s["id"])
        rounds = ddb.list_rounds(s["id"])
        if summary["all_consensus"]:
            consensus_count += 1
        sessions_detail.append({
            "session": s,
            "summary": summary,
            "rounds": rounds,
            "closed_rounds": sum(1 for r in rounds if r["status"] == "closed"),
        })

    pm_sessions = pmdb.list_sessions()
    pm_detail = []
    for s in pm_sessions:
        risks = pmdb.get_risks(s["id"])
        scenarios = pmdb.get_scenarios(s["id"])
        pm_detail.append({"session": s, "risks": risks, "scenario_count": len(scenarios)})

    mx_sessions = mxdb.list_sessions()
    mx_detail = []
    for s in mx_sessions:
        options = mxdb.get_options(s["id"])
        criteria = mxdb.get_criteria(s["id"])
        scores = mxdb.get_scores(s["id"])
        results_data = compute_results(options, criteria, scores)
        mx_detail.append({"session": s, "results": results_data, "criteria": criteria})

    sh_sessions = shdb.list_sessions()
    sh_detail = []
    for s in sh_sessions:
        hats = shdb.get_hats(s["id"])
        all_contributions = shdb.get_contributions(s["id"])
        contrib_by_hat = {}
        for c in all_contributions:
            contrib_by_hat.setdefault(c["hat_id"], []).append(c)
        hats_with_contribs = [dict(h, contributions=contrib_by_hat.get(h["id"], [])) for h in hats]
        sh_detail.append({"session": s, "hats": hats_with_contribs, "contribution_count": len(all_contributions)})

    return render_template(
        "results.html",
        decisions=decisions_detail,
        domain_counts=domain_counts,
        status_counts=status_counts,
        sessions=sessions_detail,
        consensus_count=consensus_count,
        total_decisions=len(all_decisions),
        total_sessions=len(all_sessions),
        domain_info=DOMAIN_INFO,
        pm_sessions=pm_detail,
        mx_sessions=mx_detail,
        sh_sessions=sh_detail,
        generated_at=datetime.datetime.now().strftime("%d %b %Y, %H:%M"),
    )


@app.route("/toggle", methods=["POST"])
@admin_required
def toggle_framework():
    new_fw = toggle(config_path=_FRAMEWORK_CONFIG)
    flash(f"Switched to {new_fw.title()}", "info")
    return redirect(request.referrer or url_for("index"))


@app.route("/framework/<name>", methods=["POST"])
@admin_required
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
@admin_required
def cynefin_list():
    status = request.args.get("status")
    archived = request.args.get("archived") == "1"
    decisions = _cdb().list_decisions(status=status or None, include_archived=archived)
    return render_template("cynefin/list.html", decisions=decisions, status_filter=status, show_archived=archived)


@app.route("/cynefin/new", methods=["GET", "POST"])
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
def cynefin_note(decision_id: str):
    content = request.form.get("content", "").strip()
    if content:
        _cdb().add_note(decision_id, content)
        flash("Note added", "success")
    return redirect(url_for("cynefin_show", decision_id=decision_id))


@app.route("/cynefin/<decision_id>/action", methods=["POST"])
@admin_required
def cynefin_action(decision_id: str):
    action_text = request.form.get("action_text", "").strip()
    if action_text:
        _cdb().add_action(decision_id, action_text)
        flash("Action recorded", "success")
    return redirect(url_for("cynefin_show", decision_id=decision_id))


@app.route("/cynefin/<decision_id>/status", methods=["POST"])
@admin_required
def cynefin_status(decision_id: str):
    status = request.form.get("status", "").strip()
    if status in ("open", "decided", "closed"):
        _cdb().set_status(decision_id, status)
        flash(f"Status updated to '{status}'", "success")
    return redirect(url_for("cynefin_show", decision_id=decision_id))


@app.route("/cynefin/<decision_id>/archive", methods=["POST"])
@admin_required
def cynefin_archive(decision_id: str):
    _cdb().set_status(decision_id, "archived")
    flash("Decision archived.", "info")
    return redirect(url_for("cynefin_list"))


@app.route("/cynefin/<decision_id>/delete", methods=["POST"])
@admin_required
def cynefin_delete(decision_id: str):
    _cdb().delete_decision(decision_id)
    flash("Decision permanently deleted.", "warning")
    return redirect(url_for("cynefin_list"))



@app.route("/cynefin/<decision_id>/ingest", methods=["POST"])
@admin_required
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
# Delphi routes — admin
# ---------------------------------------------------------------------------


@app.route("/delphi/")
@admin_required
def delphi_list():
    status = request.args.get("status")
    archived = request.args.get("archived") == "1"
    sessions = _ddb().list_sessions(status=status or None, include_archived=archived)
    return render_template("delphi/list.html", sessions=sessions, status_filter=status, show_archived=archived)


@app.route("/delphi/new", methods=["GET", "POST"])
@admin_required
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
@admin_required
def delphi_show(session_id: str):
    db = _ddb()
    delphi_session = db.get_session(session_id)
    if delphi_session is None:
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
    participate_url = url_for("participate_show", session_id=session_id, _external=True)
    return render_template(
        "delphi/show.html",
        session=delphi_session,
        rounds=rounds,
        current_round=current_round,
        items=items,
        notes=notes,
        responses=responses,
        consensus=consensus,
        stats_by_item=stats_by_item,
        rating_min=RATING_MIN,
        rating_max=RATING_MAX,
        participate_url=participate_url,
    )


@app.route("/delphi/<session_id>/round/open", methods=["POST"])
@admin_required
def delphi_round_open(session_id: str):
    prompt = request.form.get("prompt", "").strip()
    try:
        _ddb().open_round(session_id, prompt=prompt)
        flash("Round opened", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("delphi_show", session_id=session_id))


@app.route("/delphi/<session_id>/round/close", methods=["POST"])
@admin_required
def delphi_round_close(session_id: str):
    summary = request.form.get("summary", "").strip()
    try:
        _ddb().close_round(session_id, summary=summary)
        flash("Round closed", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("delphi_show", session_id=session_id))


@app.route("/delphi/<session_id>/respond", methods=["POST"])
@admin_required
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
@admin_required
def delphi_item(session_id: str):
    db = _ddb()
    delphi_session = db.get_session(session_id)
    text = request.form.get("text", "").strip()
    if not text:
        flash("Item text is required", "danger")
        return redirect(url_for("delphi_show", session_id=session_id))
    db.add_item(session_id, text, source_round=delphi_session["current_round"])
    flash("Item added", "success")
    return redirect(url_for("delphi_show", session_id=session_id))


@app.route("/delphi/<session_id>/rate", methods=["POST"])
@admin_required
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


@app.route("/delphi/<session_id>/archive", methods=["POST"])
@admin_required
def delphi_archive(session_id: str):
    _ddb().set_status(session_id, "archived")
    flash("Session archived.", "info")
    return redirect(url_for("delphi_list"))


@app.route("/delphi/<session_id>/restore", methods=["POST"])
@admin_required
def delphi_restore(session_id: str):
    _ddb().set_status(session_id, "active")
    flash("Session restored.", "success")
    return redirect(url_for("delphi_list"))


@app.route("/delphi/<session_id>/delete", methods=["POST"])
@admin_required
def delphi_delete(session_id: str):
    _ddb().delete_session(session_id)
    flash("Session permanently deleted.", "warning")
    return redirect(url_for("delphi_list"))


@app.route("/delphi/<session_id>/note", methods=["POST"])
@admin_required
def delphi_note(session_id: str):
    content = request.form.get("content", "").strip()
    if content:
        _ddb().add_note(session_id, content)
        flash("Note added", "success")
    return redirect(url_for("delphi_show", session_id=session_id))


@app.route("/delphi/<session_id>/import", methods=["POST"])
@admin_required
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
# Participant routes — no auth, restricted view
# ---------------------------------------------------------------------------


@app.route("/participate/<session_id>")
def participate_show(session_id: str):
    db = _ddb()
    delphi_session = db.get_session(session_id)
    if delphi_session is None:
        return render_template("participate/not_found.html"), 404
    current_round = db.get_current_round(session_id)
    items = db.get_items(session_id) if current_round else []
    closed_rounds = [r for r in db.list_rounds(session_id) if r["status"] == "closed"]
    return render_template(
        "participate/show.html",
        delphi_session=delphi_session,
        current_round=current_round,
        items=items,
        closed_rounds=closed_rounds,
        rating_min=RATING_MIN,
        rating_max=RATING_MAX,
    )


@app.route("/participate/<session_id>/respond", methods=["POST"])
def participate_respond(session_id: str):
    db = _ddb()
    current_round = db.get_current_round(session_id)
    if not current_round:
        flash("This round is not currently open for responses.", "warning")
        return redirect(url_for("participate_show", session_id=session_id))
    text = request.form.get("text", "").strip()
    if not text:
        flash("Please enter a response before submitting.", "warning")
        return redirect(url_for("participate_show", session_id=session_id))
    respondent = request.form.get("respondent", "").strip() or "anonymous"
    try:
        db.add_response(session_id, current_round["id"], text, respondent=respondent)
        flash("Your response has been recorded. Thank you.", "success")
    except ValueError as exc:
        flash(str(exc), "warning")
    return redirect(url_for("participate_show", session_id=session_id))


@app.route("/participate/<session_id>/rate", methods=["POST"])
def participate_rate(session_id: str):
    db = _ddb()
    current_round = db.get_current_round(session_id)
    if not current_round:
        flash("This round is not currently open for ratings.", "warning")
        return redirect(url_for("participate_show", session_id=session_id))
    respondent = request.form.get("respondent", "").strip() or "anonymous"
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
        flash(f"Thank you — {rated} rating(s) submitted.", "success")
    return redirect(url_for("participate_show", session_id=session_id))


# ---------------------------------------------------------------------------
# Pre-Mortem routes — admin
# ---------------------------------------------------------------------------


@app.route("/premortem/")
@admin_required
def premortem_list():
    status = request.args.get("status")
    archived = request.args.get("archived") == "1"
    sessions = _pmdb().list_sessions(status=status or None, include_archived=archived)
    return render_template("premortem/list.html", sessions=sessions, status_filter=status, show_archived=archived)


@app.route("/premortem/new", methods=["GET", "POST"])
@admin_required
def premortem_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        plan_text = request.form.get("plan_text", "").strip()
        if not title:
            flash("Title is required", "danger")
            return render_template("premortem/new.html")
        sid = _pmdb().create_session(title=title, description=description, plan_text=plan_text)
        flash(f"Pre-Mortem session created: {sid}", "success")
        return redirect(url_for("premortem_show", session_id=sid))
    return render_template("premortem/new.html")


@app.route("/premortem/<session_id>")
@admin_required
def premortem_show(session_id: str):
    db = _pmdb()
    session = db.get_session(session_id)
    if session is None:
        flash(f"Session '{session_id}' not found", "danger")
        return redirect(url_for("premortem_list"))
    scenarios = db.get_scenarios(session_id)
    risks = db.get_risks(session_id)
    participate_url = url_for("premortem_participate", session_id=session_id, _external=True)
    return render_template(
        "premortem/show.html",
        session=session,
        scenarios=scenarios,
        risks=risks,
        participate_url=participate_url,
        severity_labels=SEVERITY_LABELS,
        likelihood_labels=LIKELIHOOD_LABELS,
    )


@app.route("/premortem/<session_id>/status", methods=["POST"])
@admin_required
def premortem_status(session_id: str):
    status = request.form.get("status", "").strip()
    if status in ("setup", "brainstorming", "reviewing", "complete"):
        _pmdb().set_status(session_id, status)
        flash(f"Status updated to '{status}'", "success")
    return redirect(url_for("premortem_show", session_id=session_id))


@app.route("/premortem/<session_id>/plan", methods=["POST"])
@admin_required
def premortem_plan(session_id: str):
    plan_text = request.form.get("plan_text", "").strip()
    _pmdb().update_plan(session_id, plan_text)
    flash("Plan updated", "success")
    return redirect(url_for("premortem_show", session_id=session_id))


@app.route("/premortem/<session_id>/scenario/add", methods=["POST"])
@admin_required
def premortem_add_scenario(session_id: str):
    text = request.form.get("scenario_text", "").strip()
    if not text:
        flash("Scenario text is required", "danger")
        return redirect(url_for("premortem_show", session_id=session_id))
    _pmdb().add_scenario(session_id, text)
    flash("Scenario added", "success")
    return redirect(url_for("premortem_show", session_id=session_id))


@app.route("/premortem/<session_id>/scenario/<scenario_id>/delete", methods=["POST"])
@admin_required
def premortem_delete_scenario(session_id: str, scenario_id: str):
    _pmdb().delete_scenario(scenario_id)
    flash("Scenario removed", "info")
    return redirect(url_for("premortem_show", session_id=session_id))


@app.route("/premortem/<session_id>/risk/add", methods=["POST"])
@admin_required
def premortem_add_risk(session_id: str):
    risk_title = request.form.get("risk_title", "").strip()
    if not risk_title:
        flash("Risk title is required", "danger")
        return redirect(url_for("premortem_show", session_id=session_id))
    try:
        severity = int(request.form.get("severity", 3))
        likelihood = int(request.form.get("likelihood", 3))
    except ValueError:
        severity, likelihood = 3, 3
    _pmdb().add_risk(
        session_id=session_id,
        risk_title=risk_title,
        risk_description=request.form.get("risk_description", "").strip(),
        severity=max(1, min(5, severity)),
        likelihood=max(1, min(5, likelihood)),
        mitigation=request.form.get("mitigation", "").strip(),
        scenario_id=request.form.get("scenario_id") or None,
    )
    flash("Risk recorded", "success")
    return redirect(url_for("premortem_show", session_id=session_id))


@app.route("/premortem/<session_id>/risk/<risk_id>/delete", methods=["POST"])
@admin_required
def premortem_delete_risk(session_id: str, risk_id: str):
    _pmdb().delete_risk(risk_id)
    flash("Risk removed", "info")
    return redirect(url_for("premortem_show", session_id=session_id))


@app.route("/premortem/<session_id>/archive", methods=["POST"])
@admin_required
def premortem_archive(session_id: str):
    _pmdb().set_status(session_id, "archived")
    flash("Session archived.", "info")
    return redirect(url_for("premortem_list"))


@app.route("/premortem/<session_id>/delete", methods=["POST"])
@admin_required
def premortem_delete(session_id: str):
    _pmdb().delete_session(session_id)
    flash("Session permanently deleted.", "warning")
    return redirect(url_for("premortem_list"))


@app.route("/premortem/participate/<session_id>")
def premortem_participate(session_id: str):
    db = _pmdb()
    session = db.get_session(session_id)
    if session is None:
        return render_template("participate/not_found.html"), 404
    if session["status"] != "brainstorming":
        return render_template("premortem/participate_closed.html", session=session), 200
    return render_template("premortem/participate.html", session=session)


@app.route("/premortem/participate/<session_id>/submit", methods=["POST"])
def premortem_participate_submit(session_id: str):
    db = _pmdb()
    session = db.get_session(session_id)
    if session is None or session["status"] != "brainstorming":
        flash("This session is not currently accepting scenarios.", "warning")
        return redirect(url_for("premortem_participate", session_id=session_id))
    text = request.form.get("scenario_text", "").strip()
    if not text:
        flash("Please describe a failure scenario.", "warning")
        return redirect(url_for("premortem_participate", session_id=session_id))
    db.add_scenario(session_id, text, submitted_by="anonymous")
    flash("Your scenario has been submitted. Thank you.", "success")
    return redirect(url_for("premortem_participate", session_id=session_id))


# ---------------------------------------------------------------------------
# Decision Matrix routes — admin
# ---------------------------------------------------------------------------


@app.route("/matrix/")
@admin_required
def matrix_list():
    status = request.args.get("status")
    archived = request.args.get("archived") == "1"
    sessions = _mxdb().list_sessions(status=status or None, include_archived=archived)
    return render_template("matrix/list.html", sessions=sessions, status_filter=status, show_archived=archived)


@app.route("/matrix/new", methods=["GET", "POST"])
@admin_required
def matrix_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if not title:
            flash("Title is required", "danger")
            return render_template("matrix/new.html")
        sid = _mxdb().create_session(title=title, description=description)
        flash(f"Decision Matrix created: {sid}", "success")
        return redirect(url_for("matrix_show", session_id=sid))
    return render_template("matrix/new.html")


@app.route("/matrix/<session_id>")
@admin_required
def matrix_show(session_id: str):
    db = _mxdb()
    session = db.get_session(session_id)
    if session is None:
        flash(f"Matrix '{session_id}' not found", "danger")
        return redirect(url_for("matrix_list"))
    options = db.get_options(session_id)
    criteria = db.get_criteria(session_id)
    scores = db.get_scores(session_id)
    results_data = compute_results(options, criteria, scores) if options and criteria else []
    participate_url = url_for("matrix_participate", session_id=session_id, _external=True)
    return render_template(
        "matrix/show.html",
        session=session,
        options=options,
        criteria=criteria,
        scores=scores,
        results=results_data,
        participate_url=participate_url,
        score_min=SCORE_MIN,
        score_max=SCORE_MAX,
        weight_min=WEIGHT_MIN,
        weight_max=WEIGHT_MAX,
    )


@app.route("/matrix/<session_id>/status", methods=["POST"])
@admin_required
def matrix_status(session_id: str):
    status = request.form.get("status", "").strip()
    if status in ("setup", "scoring", "closed"):
        _mxdb().set_status(session_id, status)
        flash(f"Status updated to '{status}'", "success")
    return redirect(url_for("matrix_show", session_id=session_id))


@app.route("/matrix/<session_id>/option/add", methods=["POST"])
@admin_required
def matrix_add_option(session_id: str):
    text = request.form.get("text", "").strip()
    if not text:
        flash("Option text is required", "danger")
        return redirect(url_for("matrix_show", session_id=session_id))
    _mxdb().add_option(session_id, text)
    flash("Option added", "success")
    return redirect(url_for("matrix_show", session_id=session_id))


@app.route("/matrix/<session_id>/option/<option_id>/delete", methods=["POST"])
@admin_required
def matrix_delete_option(session_id: str, option_id: str):
    _mxdb().delete_option(option_id)
    flash("Option removed", "info")
    return redirect(url_for("matrix_show", session_id=session_id))


@app.route("/matrix/<session_id>/criterion/add", methods=["POST"])
@admin_required
def matrix_add_criterion(session_id: str):
    text = request.form.get("text", "").strip()
    if not text:
        flash("Criterion text is required", "danger")
        return redirect(url_for("matrix_show", session_id=session_id))
    try:
        weight = int(request.form.get("weight", 1))
    except ValueError:
        weight = 1
    _mxdb().add_criterion(session_id, text, weight=max(WEIGHT_MIN, min(WEIGHT_MAX, weight)))
    flash("Criterion added", "success")
    return redirect(url_for("matrix_show", session_id=session_id))


@app.route("/matrix/<session_id>/criterion/<criterion_id>/weight", methods=["POST"])
@admin_required
def matrix_update_weight(session_id: str, criterion_id: str):
    try:
        weight = int(request.form.get("weight", 1))
        _mxdb().update_criterion_weight(criterion_id, max(WEIGHT_MIN, min(WEIGHT_MAX, weight)))
        flash("Weight updated", "success")
    except ValueError:
        flash("Invalid weight", "danger")
    return redirect(url_for("matrix_show", session_id=session_id))


@app.route("/matrix/<session_id>/criterion/<criterion_id>/delete", methods=["POST"])
@admin_required
def matrix_delete_criterion(session_id: str, criterion_id: str):
    _mxdb().delete_criterion(criterion_id)
    flash("Criterion removed", "info")
    return redirect(url_for("matrix_show", session_id=session_id))


@app.route("/matrix/<session_id>/archive", methods=["POST"])
@admin_required
def matrix_archive(session_id: str):
    _mxdb().set_status(session_id, "archived")
    flash("Matrix archived.", "info")
    return redirect(url_for("matrix_list"))


@app.route("/matrix/<session_id>/delete", methods=["POST"])
@admin_required
def matrix_delete(session_id: str):
    _mxdb().delete_session(session_id)
    flash("Matrix permanently deleted.", "warning")
    return redirect(url_for("matrix_list"))


@app.route("/matrix/participate/<session_id>")
def matrix_participate(session_id: str):
    db = _mxdb()
    session = db.get_session(session_id)
    if session is None:
        return render_template("participate/not_found.html"), 404
    if session["status"] != "scoring":
        return render_template("matrix/participate_closed.html", session=session), 200
    options = db.get_options(session_id)
    criteria = db.get_criteria(session_id)
    # Hide weights from participants
    criteria_public = [{"id": c["id"], "text": c["text"]} for c in criteria]
    return render_template(
        "matrix/participate.html",
        session=session,
        options=options,
        criteria=criteria_public,
        score_min=SCORE_MIN,
        score_max=SCORE_MAX,
    )


@app.route("/matrix/participate/<session_id>/score", methods=["POST"])
def matrix_participate_score(session_id: str):
    db = _mxdb()
    session = db.get_session(session_id)
    if session is None or session["status"] != "scoring":
        flash("Scoring is not currently open.", "warning")
        return redirect(url_for("matrix_participate", session_id=session_id))
    respondent = request.form.get("respondent", "").strip() or "anonymous"
    options = db.get_options(session_id)
    criteria = db.get_criteria(session_id)
    recorded = 0
    for opt in options:
        for crit in criteria:
            key = f"score_{opt['id']}_{crit['id']}"
            raw = request.form.get(key, "").strip()
            if not raw:
                continue
            try:
                db.add_score(session_id, opt["id"], crit["id"], float(raw), respondent=respondent)
                recorded += 1
            except (ValueError, TypeError):
                pass
    if recorded:
        flash(f"Thank you — {recorded} score(s) submitted.", "success")
    return redirect(url_for("matrix_participate", session_id=session_id))


# ---------------------------------------------------------------------------
# Six Thinking Hats routes — admin
# ---------------------------------------------------------------------------


@app.route("/sixhats/")
@admin_required
def sixhats_list():
    status = request.args.get("status")
    archived = request.args.get("archived") == "1"
    sessions = _shdb().list_sessions(status=status or None, include_archived=archived)
    return render_template("sixhats/list.html", sessions=sessions, status_filter=status, show_archived=archived)


@app.route("/sixhats/new", methods=["GET", "POST"])
@admin_required
def sixhats_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        topic = request.form.get("topic", "").strip()
        if not title:
            flash("Title is required", "danger")
            return render_template("sixhats/new.html", hat_info=HAT_INFO, hats_order=HATS_ORDER)
        sid = _shdb().create_session(title=title, description=description, topic=topic)
        flash(f"Six Hats session created: {sid}", "success")
        return redirect(url_for("sixhats_show", session_id=sid))
    return render_template("sixhats/new.html", hat_info=HAT_INFO, hats_order=HATS_ORDER)


@app.route("/sixhats/<session_id>")
@admin_required
def sixhats_show(session_id: str):
    db = _shdb()
    session = db.get_session(session_id)
    if session is None:
        flash(f"Session '{session_id}' not found", "danger")
        return redirect(url_for("sixhats_list"))
    hats = db.get_hats(session_id)
    current_hat = db.get_current_hat(session_id)
    contributions_by_hat = {}
    for hat in hats:
        contributions_by_hat[hat["id"]] = db.get_contributions(session_id, hat_id=hat["id"])
    participate_url = url_for("sixhats_participate", session_id=session_id, _external=True)
    return render_template(
        "sixhats/show.html",
        session=session,
        hats=hats,
        current_hat=current_hat,
        contributions_by_hat=contributions_by_hat,
        participate_url=participate_url,
        hat_info=HAT_INFO,
    )


@app.route("/sixhats/<session_id>/hat/open", methods=["POST"])
@admin_required
def sixhats_open_hat(session_id: str):
    try:
        hat = _shdb().open_next_hat(session_id)
        if hat:
            info = HAT_INFO.get(hat["hat_color"], {})
            flash(f"{info.get('label', hat['hat_color'])} is now open", "success")
        else:
            flash("All hats have been completed", "info")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("sixhats_show", session_id=session_id))


@app.route("/sixhats/<session_id>/hat/close", methods=["POST"])
@admin_required
def sixhats_close_hat(session_id: str):
    color = _shdb().close_current_hat(session_id)
    if color:
        info = HAT_INFO.get(color, {})
        flash(f"{info.get('label', color)} closed", "success")
    else:
        flash("No hat was open", "warning")
    return redirect(url_for("sixhats_show", session_id=session_id))


@app.route("/sixhats/<session_id>/archive", methods=["POST"])
@admin_required
def sixhats_archive(session_id: str):
    _shdb().set_status(session_id, "archived")
    flash("Session archived.", "info")
    return redirect(url_for("sixhats_list"))


@app.route("/sixhats/<session_id>/delete", methods=["POST"])
@admin_required
def sixhats_delete(session_id: str):
    _shdb().delete_session(session_id)
    flash("Session permanently deleted.", "warning")
    return redirect(url_for("sixhats_list"))


@app.route("/sixhats/participate/<session_id>")
def sixhats_participate(session_id: str):
    db = _shdb()
    session = db.get_session(session_id)
    if session is None:
        return render_template("participate/not_found.html"), 404
    current_hat = db.get_current_hat(session_id)
    hats = db.get_hats(session_id)
    closed_hats = [h for h in hats if h["status"] == "closed"]
    return render_template(
        "sixhats/participate.html",
        session=session,
        current_hat=current_hat,
        closed_hats=closed_hats,
        hat_info=HAT_INFO,
    )


@app.route("/sixhats/participate/<session_id>/contribute", methods=["POST"])
def sixhats_contribute(session_id: str):
    db = _shdb()
    current_hat = db.get_current_hat(session_id)
    if not current_hat:
        flash("No hat is currently open for contributions.", "warning")
        return redirect(url_for("sixhats_participate", session_id=session_id))
    content = request.form.get("content", "").strip()
    if not content:
        flash("Please enter a contribution.", "warning")
        return redirect(url_for("sixhats_participate", session_id=session_id))
    contributor = request.form.get("contributor", "").strip() or "anonymous"
    db.add_contribution(
        session_id=session_id,
        hat_id=current_hat["id"],
        hat_color=current_hat["hat_color"],
        content=content,
        contributor=contributor,
    )
    flash("Your contribution has been recorded. Thank you.", "success")
    return redirect(url_for("sixhats_participate", session_id=session_id))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)


if __name__ == "__main__":
    main()
