# Decision Support Tool

A structured, web-accessible decision-support system built on two complementary frameworks — **Cynefin** for classifying decisions by complexity, and **Delphi** for reaching anonymous expert consensus. Both run against a local SQLite database with no cloud dependency.

---

## Contents

- [Frameworks](#frameworks)
  - [Cynefin](#cynefin-framework)
  - [Delphi](#delphi-method)
- [Installation](#installation)
- [Web Interface](#web-interface)
- [CLI Usage](#cli-usage)
  - [Unified `decision` command](#unified-decision-command)
  - [Cynefin CLI](#cynefin-cli)
  - [Delphi CLI](#delphi-cli)
- [Input Formats](#input-formats)
- [Data Storage](#data-storage)
- [Deploying to Railway](#deploying-to-railway)
- [Running Tests](#running-tests)

---

## Frameworks

### Cynefin Framework

Cynefin (Welsh: "habitat") is a sense-making framework developed by Dave Snowden at IBM in 1999. It classifies situations into five domains, each with its own recommended response strategy.

| Domain | Cause & Effect | Strategy |
|---|---|---|
| **Clear** | Obvious to everyone | Sense → Categorize → Respond (apply best practice) |
| **Complicated** | Requires expert analysis | Sense → Analyze → Respond (engage experts) |
| **Complex** | Only visible in retrospect | Probe → Sense → Respond (run safe-to-fail experiments) |
| **Chaotic** | No perceivable pattern | Act → Sense → Respond (stabilize first) |
| **Disorder** | Domain unknown | Gather → Clarify → Classify (get more context) |

The tool classifies a decision by walking through **7 diagnostic questions** covering: precedent, causality, stakeholder agreement, expertise requirements, response reversibility, urgency, and information sufficiency. Each answer contributes weighted scores across domains; the highest scorer determines the classification.

### Delphi Method

The Delphi method is a structured forecasting technique developed at the RAND Corporation in the 1950s. It collects and aggregates anonymous expert opinion through iterative rounds until the group converges.

This tool implements the standard procedure:

1. **Round 1** — Open question; experts submit free-text responses
2. **Round 2+** — Items (distilled from responses) rated on a **1–9 scale** (1 = strongly disagree, 9 = strongly agree)
3. **Convergence** — Consensus is reached when the **IQR ≤ threshold** (default 2.0)

Statistics reported per item: mean, median, Q1, Q3, IQR, % near median, and a yes/no consensus verdict. Items can be rated across multiple rounds; the tool tracks which round each rating came from.

---

## Installation

Requires Python 3.9+.

```bash
pip install -e ".[dev]"
```

This installs four CLI entry points: `cynefin`, `delphi`, `decision`, `decision-web`.

Flask and gunicorn are included as core dependencies. No external API keys are required.

---

## Web Interface

Start the development server:

```bash
decision-web
# or
python -m decisionsupport.web
```

The app binds to `http://localhost:5000` by default. Set `PORT` to change the port.

### Pages

| URL | Description |
|---|---|
| `/` | Dashboard — recent decisions and sessions |
| `/cynefin/` | List all decisions (filterable by status) |
| `/cynefin/new` | Create a new decision |
| `/cynefin/<id>` | Decision detail: assessment, notes, actions, domain guidance |
| `/cynefin/import` | Import a decision from a JSON form file |
| `/delphi/` | List all sessions |
| `/delphi/new` | Create a new session |
| `/delphi/<id>` | Session detail: rounds, responses, items, ratings, consensus |
| `/health` | Health check endpoint (returns `{"status": "ok"}`) |

### Framework toggle

The navbar shows the active framework and a **Toggle** button. Clicking it switches the active framework and persists the choice to `~/.decisionsupport/decision_framework`. The CLI and web interface share the same toggle state.

---

## CLI Usage

### Unified `decision` command

`decision` is a single entry point that proxies all commands to whichever framework is currently active.

```bash
# Check / switch the active framework
decision framework
decision toggle
decision framework cynefin
decision framework delphi

# All other commands are forwarded to the active framework
decision new "Q3 pricing strategy"
decision list
decision show abc12345
```

### Cynefin CLI

```bash
# Create a new decision
cynefin new "Platform architecture choice" -d "Monolith vs microservices for v2"

# Interactive assessment (7 questions, answered in the terminal)
cynefin assess <id>

# Re-run classification on existing responses
cynefin classify <id>

# List decisions
cynefin list
cynefin list --status open
cynefin list --status decided
cynefin list --status closed

# Show full detail for a decision
cynefin show <id>

# Add a note or action
cynefin note <id> "Architecture review scheduled for Thursday"
cynefin action <id> "Spike: benchmark Kafka vs RabbitMQ"

# Mark an action resolved
cynefin resolve <action_id> "Kafka chosen — 3× throughput at p99"

# Change decision status
# (done via web UI or direct DB; status values: open | decided | closed)

# Import from JSON form file
cynefin import form.json

# Ingest a meeting transcript
cynefin ingest <id> transcript.txt
cynefin ingest <id> meeting.vtt --format vtt
cynefin ingest <id> export.json --format json

# Export a decision to JSON
cynefin export <id>
cynefin export <id> --file decision_abc12345.json

# List domain guidance
cynefin domains
```

### Delphi CLI

```bash
# Create a new session
delphi new "AI investment priorities" -q "Which AI initiatives should we fund in FY26?" -t 0.8 2.0

# Open a round for responses
delphi round open <sid>
delphi round open <sid> --prompt "Please assess each initiative on feasibility and impact"

# Close a round with an optional summary
delphi round close <sid>
delphi round close <sid> --summary "Strong convergence on items 1 and 3; item 2 needs more context"

# Submit a text response
delphi respond <sid> "I believe initiative A addresses the core bottleneck..."
delphi respond <sid> "..." --respondent "alice"

# Add an item for rating (distilled from responses)
delphi item <sid> "Fund initiative A: real-time inference pipeline"

# Submit numeric ratings (1–9) for all items
delphi rate <sid> --respondent "alice" 1=8 2=3 3=7

# Show session detail with per-item statistics
delphi show <sid>

# Print round statistics table
delphi summarize <sid>

# List all sessions
delphi list
delphi list --status active
delphi list --status closed

# Add a facilitator note
delphi note <sid> "Expert B flagged a dependency between items 2 and 4"

# Import responses from a JSON file
delphi import <sid> responses.json

# Export session to JSON
delphi export <sid>
delphi export <sid> --file session_abc12345.json
```

---

## Input Formats

### Cynefin — JSON form

Use to submit answers asynchronously (e.g. a stakeholder fills in a form offline):

```json
{
  "title": "Database engine selection",
  "description": "Choosing between Postgres and DynamoDB for the orders service",
  "responses": {
    "q_precedent":   "yes",
    "q_causality":   "direct",
    "q_agreement":   "partial",
    "q_expertise":   "external",
    "q_reversible":  "costly",
    "q_urgency":     "moderate",
    "q_information": "partial"
  }
}
```

Import via CLI: `cynefin import form.json`
Import via web: `/cynefin/import`

### Cynefin — transcripts

The tool accepts three transcript formats for ingesting meeting notes as decision context:

| Format | `--format` flag | Notes |
|---|---|---|
| Plain text | `plain` | One paragraph per note |
| WebVTT subtitles | `vtt` | Strips timestamps and speaker labels |
| JSON array | `json` | `[{"text": "..."}]` or `[{"speaker": "Alice", "text": "..."}]` |

Auto-detection is available with `--format auto` (default).

### Delphi — JSON responses

Use to import async responses from an external form:

```json
{
  "respondent": "alice",
  "responses": [
    {"text": "Initiative A directly addresses our inference bottleneck."},
    {"text": "Initiative B duplicates work already done in Q2."}
  ],
  "ratings": [
    {"item_id": "abc12345", "rating": 8, "rationale": "High feasibility, proven stack"},
    {"item_id": "def67890", "rating": 2, "rationale": "Redundant with Q2 platform work"}
  ]
}
```

Import via CLI: `delphi import <sid> responses.json`
Import via web: session detail page → "Import responses" section

---

## Data Storage

All data is stored in SQLite at `~/.decisionsupport/` by default.

| File | Contents |
|---|---|
| `~/.decisionsupport/cynefin.db` | Decisions, responses, notes, actions |
| `~/.decisionsupport/delphi.db` | Sessions, rounds, responses, items, ratings, notes |
| `~/.decisionsupport/decision_framework` | Active framework (`cynefin` or `delphi`) |

Override the data directory via the `DECISION_SUPPORT_DATA_DIR` environment variable:

```bash
DECISION_SUPPORT_DATA_DIR=/data decision-web
```

Both databases use WAL mode for safe concurrent reads/writes.

### Schema outline

**Cynefin**

```
decisions          id, title, description, domain, status, created_at, updated_at
assessment_responses  id, decision_id, question_id, question_text,
                      answer_key, answer_label, scores_json, created_at
notes              id, decision_id, content, source_type, created_at
actions            id, decision_id, action_text, status, outcome, created_at
```

**Delphi**

```
sessions    id, title, description, question, status, consensus_threshold,
            current_round, created_at
rounds      id, session_id, round_number, status, prompt, summary, opened_at, closed_at
responses   id, session_id, round_id, respondent, text, score, created_at
items       id, session_id, text, source_round, created_at
ratings     id, session_id, round_id, item_id, respondent, rating, rationale, created_at
notes       id, session_id, content, source_type, created_at
```

---

## Deploying to Railway

1. Push the repository to GitHub.
2. Create a new Railway project and connect the GitHub repo.
3. Add a **persistent volume** mounted at `/data`.
4. Set the environment variable `DECISION_SUPPORT_DATA_DIR=/data` in Railway settings.
5. Railway picks up `railway.toml` automatically — no further configuration needed.

```toml
# railway.toml (already included)
[build]
builder = "nixpacks"

[deploy]
startCommand = "gunicorn decisionsupport.web:app --bind 0.0.0.0:$PORT --workers 2 --timeout 30"
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

The `Procfile` provides the same start command for Heroku-compatible platforms.

**Environment variables**

| Variable | Default | Description |
|---|---|---|
| `DECISION_SUPPORT_DATA_DIR` | `~/.decisionsupport` | Path for SQLite databases |
| `SECRET_KEY` | `dev-change-me-in-production` | Flask session secret — **change this** |
| `PORT` | `5000` | Port to bind (set automatically by Railway) |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |

---

## Running Tests

```bash
# All decision-tool tests
python -m pytest tests/test_cynefin.py tests/test_delphi.py tests/test_decision.py tests/test_web.py -v

# Full suite
python -m pytest tests/ --ignore=tests/benchmarks -v

# With coverage
python -m pytest tests/ --ignore=tests/benchmarks --cov=decisionsupport --cov-report=term-missing
```

Test counts: 46 Cynefin · 54 Delphi · 17 Decision · 37 Web = **154 decision-tool tests**.

---

## File Reference

```
decisionsupport/
├── cynefin.py          Core Cynefin module — DB, questions, classification
├── cynefin_cli.py      cynefin CLI entry point
├── delphi.py           Core Delphi module — DB, stats, consensus
├── delphi_cli.py       delphi CLI entry point
├── decision.py         Framework toggle — shared by CLI and web
├── decision_cli.py     decision CLI entry point (proxies to active framework)
├── web.py              Flask application — all HTTP routes
└── templates/
    ├── base.html            Bootstrap 5 base layout, navbar, flash messages
    ├── index.html           Dashboard
    ├── cynefin/
    │   ├── list.html        Decision list with status filter
    │   ├── new.html         Create decision form
    │   ├── show.html        Decision detail, assessment, domain guidance
    │   └── import.html      JSON import form
    └── delphi/
        ├── list.html        Session list
        ├── new.html         Create session form
        └── show.html        Session detail, rounds, ratings, consensus

Procfile                Gunicorn start command (Heroku / Railway)
railway.toml            Railway deployment configuration
tests/
├── test_cynefin.py     46 unit tests
├── test_delphi.py      54 unit tests
├── test_decision.py    17 unit tests
└── test_web.py         37 integration tests (Flask test client)
```
