#!/usr/bin/env python3
"""
delphi — Delphi method decision support.

Structured anonymous expert consensus through iterative rounds.

Workflow:
  1. Create a session:        delphi new "Title" "Central question"
  2. Open Round 1:            delphi round open <id>
  3. Collect text responses:  delphi respond <id> "My perspective is..."
     (or async)               delphi import <id> responses.json
  4. Close Round 1:           delphi round close <id> --summary "Key themes: ..."
  5. Add items to rate:       delphi item <id> "Proposition A"
  6. Open Round 2:            delphi round open <id> --prompt "Rate each item 1–9"
  7. Rate items:              delphi rate <id> <item_id> 7 --rationale "Because..."
  8. Close Round 2:           delphi round close <id>
  9. Check consensus:         delphi summarize <id>
  10. Repeat rounds until consensus.

Usage:
    delphi new <title> <question> [-d description] [--threshold 2.0]
    delphi round open <id> [--prompt "..."]
    delphi round close <id> [--summary "..."]
    delphi respond <id> <text> [--score N] [--name respondent]
    delphi item <id> <text>
    delphi rate <id> <item_id> <rating> [--rationale "..."] [--name respondent]
    delphi summarize <id>
    delphi list [--status active|completed|closed]
    delphi show <id>
    delphi note <id> <text>
    delphi import <id> <file.json>
    delphi export <id> [--file out.json]
"""

import argparse
import json
import sys
from typing import Optional

from .delphi import (
    AGREEMENT_LABELS,
    DEFAULT_CONSENSUS_THRESHOLD,
    RATING_MAX,
    RATING_MIN,
    DelphiDB,
)

_VALID_STATUSES = {"active", "completed", "closed"}


def _db() -> DelphiDB:
    return DelphiDB()


def _require_session(db: DelphiDB, session_id: str) -> dict:
    s = db.get_session(session_id)
    if s is None:
        print(f"Error: session '{session_id}' not found.", file=sys.stderr)
        sys.exit(1)
    return s


def _require_round(db: DelphiDB, session_id: str) -> dict:
    rnd = db.get_current_round(session_id)
    if rnd is None:
        print(
            f"Error: no open round for session '{session_id}'.\n"
            f"  Run: delphi round open {session_id}",
            file=sys.stderr,
        )
        sys.exit(1)
    return rnd


def _consensus_bar(iqr: Optional[float], threshold: float, width: int = 12) -> str:
    """Visual indicator of how close the IQR is to the consensus threshold."""
    if iqr is None:
        return "?" * width
    ratio = max(0.0, 1.0 - (iqr / max(threshold * 2, 0.1)))
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace) -> None:
    db = _db()
    threshold = getattr(args, "threshold", DEFAULT_CONSENSUS_THRESHOLD)
    session_id = db.create_session(
        title=args.title,
        question=args.question,
        description=args.description or "",
        consensus_threshold=threshold,
    )
    print(f"\n  Session created: {session_id}")
    print(f"  Title:    {args.title}")
    print(f"  Question: {args.question}")
    print(f"  Threshold: IQR ≤ {threshold} (1–9 scale)")
    print(f"\n  Next: delphi round open {session_id}")


def cmd_round(args: argparse.Namespace) -> None:
    action = getattr(args, "round_action", None)
    if not action:
        print("Usage: delphi round open|close <id>", file=sys.stderr)
        sys.exit(1)

    db = _db()
    session_id = args.id

    if action == "open":
        _require_session(db, session_id)
        prompt = getattr(args, "prompt", "") or ""
        try:
            round_id = db.open_round(session_id, prompt=prompt)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        session = db.get_session(session_id)
        print(f"\n  Round {session['current_round']} opened: {round_id}")
        if prompt:
            print(f"  Prompt: {prompt}")
        print(f"\n  Collect responses: delphi respond {session_id} \"<text>\"")
        print(f"  Or import async:   delphi import {session_id} responses.json")

    elif action == "close":
        _require_session(db, session_id)
        summary = getattr(args, "summary", "") or ""
        try:
            round_id = db.close_round(session_id, summary=summary)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"\n  Round closed: {round_id}")
        if summary:
            print(f"  Summary: {summary}")
        # Show response count
        responses = db.get_responses(session_id, round_id=round_id)
        ratings = db.get_ratings(session_id, round_id=round_id)
        print(f"\n  {len(responses)} text response(s), {len(ratings)} rating(s) recorded.")
        if ratings:
            stats = db.round_statistics(session_id, round_id)
            _print_statistics(stats)
        print("\n  Next:")
        if not db.get_items(session_id):
            print(f"    Add propositions to rate: delphi item {session_id} \"<proposition>\"")
        print(f"    Open next round:          delphi round open {session_id}")
        print(f"    Check consensus:          delphi summarize {session_id}")


def cmd_respond(args: argparse.Namespace) -> None:
    db = _db()
    _require_session(db, args.id)
    rnd = _require_round(db, args.id)

    score = getattr(args, "score", None)
    if score is not None:
        if not (RATING_MIN <= score <= RATING_MAX):
            print(
                f"Error: score must be between {RATING_MIN} and {RATING_MAX}",
                file=sys.stderr,
            )
            sys.exit(1)

    respondent = getattr(args, "name", "anonymous") or "anonymous"
    response_id = db.add_response(
        session_id=args.id,
        round_id=rnd["id"],
        response_text=args.text,
        respondent=respondent,
        score=score,
    )
    print(f"\n  Response recorded: {response_id}")
    if score is not None:
        print(f"  Score: {score}/9")


def cmd_item(args: argparse.Namespace) -> None:
    db = _db()
    session = _require_session(db, args.id)
    item_id = db.add_item(args.id, args.text, source_round=session["current_round"])
    print(f"\n  Item added: {item_id}")
    print(f"  Text: {args.text}")
    print(f"\n  Rate it: delphi rate {args.id} {item_id} <1-9>")


def cmd_rate(args: argparse.Namespace) -> None:
    db = _db()
    _require_session(db, args.id)
    rnd = _require_round(db, args.id)

    try:
        rating = float(args.rating)
    except (TypeError, ValueError):
        print(f"Error: rating must be a number between {RATING_MIN} and {RATING_MAX}", file=sys.stderr)
        sys.exit(1)

    respondent = getattr(args, "name", "anonymous") or "anonymous"
    rationale = getattr(args, "rationale", "") or ""

    try:
        rating_id = db.add_rating(
            session_id=args.id,
            round_id=rnd["id"],
            item_id=args.item_id,
            rating=rating,
            respondent=respondent,
            rationale=rationale,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Rating recorded: {rating_id}  ({rating}/9)")
    if rationale:
        print(f"  Rationale: {rationale}")


def cmd_summarize(args: argparse.Namespace) -> None:
    db = _db()
    session = _require_session(db, args.id)
    summary = db.session_consensus_summary(args.id)

    print(f"\n  Delphi Summary — {session['title']}")
    print(f"  Question: {session['question']}")
    print(f"  Round: {session['current_round']}  |  Status: {session['status']}")
    print(f"  Consensus threshold: IQR ≤ {session['consensus_threshold']}")

    items = summary.get("items", [])
    if not items:
        print("\n  No rated items yet.")
        print(f"  Add items: delphi item {args.id} \"<proposition>\"")
        return

    print(f"\n  Results (as of Round {summary.get('as_of_round', '?')}):\n")
    _print_statistics(items)

    if summary["all_consensus"]:
        print("  ✓ Consensus reached on all items.\n")
    else:
        n_consensus = sum(1 for s in items if s["consensus"])
        print(f"  {n_consensus}/{len(items)} items have consensus.")
        print(f"  Continue: delphi round open {args.id}\n")


def _print_statistics(stats: list) -> None:
    threshold_note = "(IQR ≤ threshold)"
    print(f"  {'Item':<36} {'Med':>4} {'IQR':>5} {'N':>3}  {'Consensus':>10}  Agreement")
    print(f"  {'-'*36} {'-'*4} {'-'*5} {'-'*3}  {'-'*10}  {'-'*12}")
    for s in stats:
        text = (s.get("item_text") or "")[:35]
        if len(s.get("item_text") or "") > 35:
            text = text[:-1] + "…"
        if s["count"] == 0:
            print(f"  {text:<36}  {'—':>4}  {'—':>5}  {'0':>3}  {'—':>10}")
            continue
        med = f"{s['median']:.1f}"
        iqr = f"{s['iqr']:.1f}"
        cnt = str(s["count"])
        consensus_str = "Yes ✓" if s["consensus"] else "No"
        agreement = AGREEMENT_LABELS.get(s.get("agreement", ""), "")
        print(f"  {text:<36} {med:>4} {iqr:>5} {cnt:>3}  {consensus_str:>10}  {agreement}")
    print()
    _ = threshold_note  # suppress unused warning


def cmd_list(args: argparse.Namespace) -> None:
    db = _db()
    status = getattr(args, "status", None)
    sessions = db.list_sessions(status=status)
    if not sessions:
        msg = f"No sessions with status '{status}'." if status else "No sessions found."
        print(f"\n  {msg}")
        return
    print(f"\n  {'ID':<10} {'Round':>5} {'Status':<10} {'Title'}")
    print(f"  {'-'*8}  {'-----'}  {'-'*8}  {'-'*40}")
    for s in sessions:
        title = s["title"][:55] + ("…" if len(s["title"]) > 55 else "")
        print(f"  {s['id']:<10} {s['current_round']:>5}  {s['status']:<10} {title}")
    print()


def cmd_show(args: argparse.Namespace) -> None:
    db = _db()
    session = _require_session(db, args.id)

    print(f"\n  Session: {session['id']}")
    print(f"  Title:    {session['title']}")
    print(f"  Question: {session['question']}")
    if session.get("description"):
        print(f"  Context:  {session['description']}")
    print(f"  Status:   {session['status']}  |  Round: {session['current_round']}")
    print(f"  Threshold: IQR ≤ {session['consensus_threshold']}")

    rounds = db.list_rounds(args.id)
    if rounds:
        print(f"\n  Rounds ({len(rounds)}):")
        for r in rounds:
            closed = f" → closed {r['closed_at'][:10]}" if r.get("closed_at") else " (open)"
            print(f"    Round {r['round_number']} [{r['id']}]{closed}")
            if r.get("prompt"):
                print(f"      Prompt: {r['prompt']}")
            if r.get("summary"):
                print(f"      Summary: {r['summary']}")

    items = db.get_items(args.id)
    if items:
        print(f"\n  Items ({len(items)}):")
        for item in items:
            print(f"    {item['id']}: {item['text']}")

    current = db.get_current_round(args.id)
    if current:
        responses = db.get_responses(args.id, round_id=current["id"])
        ratings = db.get_ratings(args.id, round_id=current["id"])
        print(f"\n  Current round ({current['id']}):")
        print(f"    {len(responses)} response(s), {len(ratings)} rating(s)")

    notes = db.get_notes(args.id)
    if notes:
        print(f"\n  Notes ({len(notes)}):")
        for n in notes:
            print(f"    [{n['source_type']}] {n['content'][:100]}")
    print()


def cmd_note(args: argparse.Namespace) -> None:
    db = _db()
    _require_session(db, args.id)
    note_id = db.add_note(args.id, args.text)
    print(f"\n  Note added: {note_id}")


def cmd_import(args: argparse.Namespace) -> None:
    db = _db()
    _require_session(db, args.id)
    try:
        n_resp, n_rate = db.import_responses_json(args.id, args.file)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"\n  Imported from '{args.file}':")
    print(f"    {n_resp} text response(s)")
    print(f"    {n_rate} rating(s)")


def cmd_export(args: argparse.Namespace) -> None:
    db = _db()
    try:
        data = db.export_session(args.id)
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


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delphi",
        description="Delphi method — structured anonymous expert consensus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    # new
    p_new = sub.add_parser("new", help="Create a new Delphi session")
    p_new.add_argument("title", help="Short title for the session")
    p_new.add_argument("question", help="The central question for expert consensus")
    p_new.add_argument("-d", "--description", default="", help="Optional context")
    p_new.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_CONSENSUS_THRESHOLD,
        help=f"IQR ≤ this = consensus (default: {DEFAULT_CONSENSUS_THRESHOLD})",
    )

    # round
    p_round = sub.add_parser("round", help="Open or close a round")
    round_sub = p_round.add_subparsers(dest="round_action")

    p_round_open = round_sub.add_parser("open", help="Open the next round")
    p_round_open.add_argument("id", help="Session ID")
    p_round_open.add_argument("--prompt", default="", help="Optional prompt for this round")

    p_round_close = round_sub.add_parser("close", help="Close the current round")
    p_round_close.add_argument("id", help="Session ID")
    p_round_close.add_argument("--summary", default="", help="Facilitator's summary of this round")

    # respond
    p_respond = sub.add_parser("respond", help="Submit a text response for the current round")
    p_respond.add_argument("id", help="Session ID")
    p_respond.add_argument("text", help="Response text")
    p_respond.add_argument("--score", type=float, default=None, help=f"Optional numeric score ({RATING_MIN}–{RATING_MAX})")
    p_respond.add_argument("--name", default="anonymous", help="Respondent name (kept anonymous in summaries)")

    # item
    p_item = sub.add_parser("item", help="Add a proposition item for rating")
    p_item.add_argument("id", help="Session ID")
    p_item.add_argument("text", help="Proposition text")

    # rate
    p_rate = sub.add_parser("rate", help=f"Rate an item ({RATING_MIN}–{RATING_MAX})")
    p_rate.add_argument("id", help="Session ID")
    p_rate.add_argument("item_id", help="Item ID")
    p_rate.add_argument("rating", type=float, help=f"Rating ({RATING_MIN}–{RATING_MAX})")
    p_rate.add_argument("--rationale", default="", help="Optional rationale")
    p_rate.add_argument("--name", default="anonymous", help="Respondent name")

    # summarize
    p_summarize = sub.add_parser("summarize", help="Show consensus statistics")
    p_summarize.add_argument("id", help="Session ID")

    # list
    p_list = sub.add_parser("list", help="List sessions")
    p_list.add_argument(
        "--status",
        choices=sorted(_VALID_STATUSES),
        default=None,
        help="Filter by status",
    )

    # show
    p_show = sub.add_parser("show", help="Show full session detail")
    p_show.add_argument("id", help="Session ID")

    # note
    p_note = sub.add_parser("note", help="Add a manual note to a session")
    p_note.add_argument("id", help="Session ID")
    p_note.add_argument("text", help="Note text")

    # import
    p_import = sub.add_parser("import", help="Import responses/ratings from a JSON file")
    p_import.add_argument("id", help="Session ID")
    p_import.add_argument("file", help="Path to JSON file")

    # export
    p_export = sub.add_parser("export", help="Export session data to JSON")
    p_export.add_argument("id", help="Session ID")
    p_export.add_argument("--file", default=None, help="Output file (default: stdout)")

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

    if args.command == "round":
        cmd_round(args)
        return

    dispatch = {
        "new": cmd_new,
        "respond": cmd_respond,
        "item": cmd_item,
        "rate": cmd_rate,
        "summarize": cmd_summarize,
        "list": cmd_list,
        "show": cmd_show,
        "note": cmd_note,
        "import": cmd_import,
        "export": cmd_export,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
