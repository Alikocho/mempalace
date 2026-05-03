#!/usr/bin/env python3
"""
cynefin — Cynefin framework decision support tool.

Classifies decisions into Clear, Complicated, Complex, Chaotic, or Disorder domains
and surfaces domain-appropriate response strategies.

Usage:
    cynefin new "Decision title" [-d "context"]
    cynefin assess <id>
    cynefin classify <id>
    cynefin list [--status open|decided|closed]
    cynefin show <id>
    cynefin note <id> <text>
    cynefin action <id> <text>
    cynefin resolve <action_id> <outcome>
    cynefin import <form.json>
    cynefin ingest <id> <transcript_file> [--format plain|vtt|json]
    cynefin export <id> [--file out.json]
    cynefin domains
"""

import argparse
import json
import sys
import textwrap
from typing import Optional

from .cynefin import (
    DOMAIN_INFO,
    QUESTIONS,
    CynefinDB,
    classify_from_responses,
    ingest_form_json,
    ingest_transcript,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_STATUSES = {"open", "decided", "closed"}


def _db() -> CynefinDB:
    return CynefinDB()


def _require_decision(db: CynefinDB, decision_id: str) -> dict:
    d = db.get_decision(decision_id)
    if d is None:
        print(f"Error: decision '{decision_id}' not found.", file=sys.stderr)
        sys.exit(1)
    return d


def _fmt_domain(domain: Optional[str]) -> str:
    if not domain:
        return "(unclassified)"
    info = DOMAIN_INFO.get(domain, {})
    return info.get("label", domain.title())


def _print_decision(d: dict, *, verbose: bool = False) -> None:
    domain_label = _fmt_domain(d.get("domain"))
    print(f"\n  ID:          {d['id']}")
    print(f"  Title:       {d['title']}")
    if d.get("description"):
        print(f"  Context:     {d['description']}")
    print(f"  Domain:      {domain_label}")
    print(f"  Status:      {d['status']}")
    print(f"  Created:     {d['created_at'][:10]}")
    if verbose and d.get("domain") and d["domain"] in DOMAIN_INFO:
        info = DOMAIN_INFO[d["domain"]]
        print(f"\n  {info['sense_act']}")
        print("\n  Guidance:")
        for g in info["guidance"]:
            print(f"    • {g}")
        print(f"\n  Warning: {info['warning']}")


def _score_bar(score: float, max_score: float, width: int = 20) -> str:
    if max_score == 0:
        filled = 0
    else:
        filled = round((score / max_score) * width)
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace) -> None:
    db = _db()
    decision_id = db.create_decision(
        title=args.title,
        description=args.description or "",
    )
    print(f"\n  Decision created: {decision_id}")
    print(f"  Title: {args.title}")
    print(f"\n  Run: cynefin assess {decision_id}   to classify this decision")


def cmd_assess(args: argparse.Namespace) -> None:
    db = _db()
    decision = _require_decision(db, args.id)

    print(f"\n  Assessing: {decision['title']}")
    print(f"  ID: {args.id}\n")
    print("  Answer each question to classify this decision into a Cynefin domain.")
    print("  (You can re-run 'assess' at any time to update your answers.)\n")

    answered = 0
    for q in QUESTIONS:
        print(f"  Q: {q['text']}")
        for a in q["answers"]:
            print(f"     [{a['key']}] {a['label']}")
        while True:
            valid_keys = [a["key"] for a in q["answers"]]
            choice = input(f"  Your answer ({'/'.join(valid_keys)}), or Enter to skip: ").strip().lower()
            if choice == "":
                break
            answer = next((a for a in q["answers"] if a["key"] == choice), None)
            if answer:
                db.save_response(
                    decision_id=args.id,
                    question_id=q["id"],
                    question_text=q["text"],
                    answer_key=choice,
                    answer_label=answer["label"],
                    scores=answer["scores"],
                )
                answered += 1
                break
            print(f"  Invalid choice. Enter one of: {', '.join(valid_keys)}")
        print()

    if answered == 0:
        print("  No answers recorded.")
        return

    # Auto-classify
    responses = db.get_responses(args.id)
    domain, scores = classify_from_responses(responses)
    db.set_domain(args.id, domain)
    _print_classification(domain, scores, len(responses))


def cmd_classify(args: argparse.Namespace) -> None:
    db = _db()
    _require_decision(db, args.id)
    responses = db.get_responses(args.id)
    if not responses:
        print(f"\n  No assessment responses found for '{args.id}'.")
        print(f"  Run: cynefin assess {args.id}")
        return
    domain, scores = classify_from_responses(responses)
    db.set_domain(args.id, domain)
    _print_classification(domain, scores, len(responses))


def _print_classification(domain: str, scores: dict, n_answered: int) -> None:
    max_score = max(scores.values()) if scores.values() else 1
    print(f"\n  Classification ({n_answered}/{len(QUESTIONS)} questions answered):\n")
    for d in ["clear", "complicated", "complex", "chaotic"]:
        bar = _score_bar(scores[d], max_score)
        marker = "  ◄" if d == domain else ""
        label = DOMAIN_INFO[d]["label"].ljust(12)
        print(f"    {label} {bar}  {scores[d]:.0f}{marker}")

    if domain == "disorder":
        print("\n  Result: Disorder — more information needed to classify.")
    else:
        info = DOMAIN_INFO[domain]
        print(f"\n  Result: {info['label']}")
        print(f"  {info['description']}")
        print(f"  Approach: {info['sense_act']}")
        print("\n  Guidance:")
        for g in info["guidance"]:
            print(f"    • {g}")
        print(f"\n  Warning: {info['warning']}")
    print()


def cmd_list(args: argparse.Namespace) -> None:
    db = _db()
    status = getattr(args, "status", None)
    if status and status not in _VALID_STATUSES:
        print(f"Error: status must be one of {sorted(_VALID_STATUSES)}", file=sys.stderr)
        sys.exit(1)
    decisions = db.list_decisions(status=status)
    if not decisions:
        msg = f"No decisions with status '{status}'." if status else "No decisions found."
        print(f"\n  {msg}")
        return
    print(f"\n  {'ID':<10} {'Domain':<14} {'Status':<10} {'Title'}")
    print(f"  {'-'*8}  {'-'*12}  {'-'*8}  {'-'*40}")
    for d in decisions:
        domain = _fmt_domain(d.get("domain")).ljust(14)
        status_col = d["status"].ljust(10)
        title = d["title"][:55] + ("…" if len(d["title"]) > 55 else "")
        print(f"  {d['id']:<10} {domain} {status_col} {title}")
    print()


def cmd_show(args: argparse.Namespace) -> None:
    db = _db()
    decision = _require_decision(db, args.id)
    _print_decision(decision, verbose=True)

    responses = db.get_responses(args.id)
    if responses:
        print(f"\n  Assessment ({len(responses)}/{len(QUESTIONS)} questions answered):")
        for r in responses:
            print(f"    {r['question_id']}: {r['answer_label']}")

    notes = db.get_notes(args.id)
    if notes:
        print(f"\n  Notes ({len(notes)}):")
        for n in notes:
            snippet = n["content"][:120].replace("\n", " ")
            print(f"    [{n['source_type']}] {snippet}")

    actions = db.get_actions(args.id)
    if actions:
        print(f"\n  Actions ({len(actions)}):")
        for a in actions:
            resolved = f" → {a['outcome']}" if a.get("outcome") else " (pending)"
            print(f"    {a['id']}: {a['action_text']}{resolved}")
    print()


def cmd_note(args: argparse.Namespace) -> None:
    db = _db()
    _require_decision(db, args.id)
    note_id = db.add_note(args.id, args.text)
    print(f"\n  Note added: {note_id}")


def cmd_action(args: argparse.Namespace) -> None:
    db = _db()
    _require_decision(db, args.id)
    action_id = db.add_action(args.id, args.text)
    print(f"\n  Action recorded: {action_id}")
    print(f"  When done, run: cynefin resolve {action_id} \"<outcome>\"")


def cmd_resolve(args: argparse.Namespace) -> None:
    db = _db()
    db.resolve_action(args.action_id, args.outcome)
    print(f"\n  Action {args.action_id} resolved.")


def cmd_import(args: argparse.Namespace) -> None:
    db = _db()
    try:
        decision_id = ingest_form_json(db, args.file)
    except (ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    decision = db.get_decision(decision_id)
    print(f"\n  Decision imported: {decision_id}")
    print(f"  Title: {decision['title']}")
    if decision.get("domain"):
        print(f"  Domain: {_fmt_domain(decision['domain'])}")
    else:
        print("  Domain: unclassified (not all questions answered)")
        print(f"  Run: cynefin assess {decision_id}  to complete the assessment")


def cmd_ingest(args: argparse.Namespace) -> None:
    db = _db()
    _require_decision(db, args.id)
    try:
        count = ingest_transcript(db, args.id, args.file, fmt=args.format)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"\n  Ingested {count} note(s) from '{args.file}' into decision '{args.id}'.")


def cmd_export(args: argparse.Namespace) -> None:
    db = _db()
    try:
        data = db.export_decision(args.id)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    output = json.dumps(data, indent=2, default=str)
    if args.file:
        with open(args.file, "w", encoding="utf-8") as fh:
            fh.write(output)
        print(f"\n  Exported to: {args.file}")
    else:
        print(output)


def cmd_domains(_args: argparse.Namespace) -> None:
    print("\n  Cynefin Framework — Decision Domains\n")
    for key in ["clear", "complicated", "complex", "chaotic", "disorder"]:
        info = DOMAIN_INFO[key]
        print(f"  {info['label'].upper()}")
        print(f"    {info['description']}")
        print(f"    Approach: {info['sense_act']}")
        print("    Guidance:")
        for g in info["guidance"]:
            wrapped = textwrap.fill(g, width=72, initial_indent="      • ", subsequent_indent="        ")
            print(wrapped)
        print(f"    ⚠  {info['warning']}")
        print()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cynefin",
        description="Cynefin framework decision support — classify decisions and surface strategies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    # new
    p_new = sub.add_parser("new", help="Create a new decision")
    p_new.add_argument("title", help="Short title for the decision")
    p_new.add_argument("-d", "--description", default="", help="Optional context or background")

    # assess
    p_assess = sub.add_parser(
        "assess", help="Answer diagnostic questions to classify a decision"
    )
    p_assess.add_argument("id", help="Decision ID")

    # classify
    p_classify = sub.add_parser(
        "classify", help="Re-compute and display the domain classification"
    )
    p_classify.add_argument("id", help="Decision ID")

    # list
    p_list = sub.add_parser("list", help="List decisions")
    p_list.add_argument(
        "--status",
        choices=sorted(_VALID_STATUSES),
        default=None,
        help="Filter by status",
    )

    # show
    p_show = sub.add_parser("show", help="Show full detail for a decision")
    p_show.add_argument("id", help="Decision ID")

    # note
    p_note = sub.add_parser("note", help="Add a manual note to a decision")
    p_note.add_argument("id", help="Decision ID")
    p_note.add_argument("text", help="Note text")

    # action
    p_action = sub.add_parser("action", help="Record an action taken for a decision")
    p_action.add_argument("id", help="Decision ID")
    p_action.add_argument("text", help="Action description")

    # resolve
    p_resolve = sub.add_parser("resolve", help="Record the outcome of an action")
    p_resolve.add_argument("action_id", help="Action ID")
    p_resolve.add_argument("outcome", help="What happened / what you learned")

    # import
    p_import = sub.add_parser("import", help="Create a decision from an async JSON form file")
    p_import.add_argument("file", help="Path to form JSON file")

    # ingest
    p_ingest = sub.add_parser(
        "ingest", help="Attach a transcript (plain text, VTT, or JSON) to a decision"
    )
    p_ingest.add_argument("id", help="Decision ID")
    p_ingest.add_argument("file", help="Path to transcript file")
    p_ingest.add_argument(
        "--format",
        choices=["auto", "plain", "vtt", "json"],
        default="auto",
        help="Transcript format (default: auto-detect from file extension)",
    )

    # export
    p_export = sub.add_parser("export", help="Export a decision to JSON")
    p_export.add_argument("id", help="Decision ID")
    p_export.add_argument("--file", default=None, help="Output file path (default: stdout)")

    # domains
    sub.add_parser("domains", help="Print descriptions of all Cynefin domains")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    dispatch = {
        "new": cmd_new,
        "assess": cmd_assess,
        "classify": cmd_classify,
        "list": cmd_list,
        "show": cmd_show,
        "note": cmd_note,
        "action": cmd_action,
        "resolve": cmd_resolve,
        "import": cmd_import,
        "ingest": cmd_ingest,
        "export": cmd_export,
        "domains": cmd_domains,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
