# Decision Support Tool

A structured, web-accessible decision-support system built on five complementary frameworks — **Cynefin** for classifying decisions by complexity, **Delphi** for reaching anonymous expert consensus, **Pre-Mortem** for surfacing hidden risks, **Decision Matrix** for scoring options against weighted criteria, and **Six Thinking Hats** for structured parallel thinking. All frameworks run against a local SQLite database with no cloud dependency.

---

## Contents

- [Frameworks](#frameworks)
  - [Cynefin](#cynefin-framework)
  - [Delphi](#delphi-method)
  - [Pre-Mortem](#pre-mortem)
  - [Decision Matrix](#decision-matrix)
  - [Six Thinking Hats](#six-thinking-hats)
- [Admin & Participant Interfaces](#admin--participant-interfaces)
- [Installation](#installation)
- [Web Interface](#web-interface)
- [CLI Usage](#cli-usage)
- [Input Formats](#input-formats)
- [Data Storage](#data-storage)
- [Results Dashboard](#results-dashboard)
- [Deploying to Railway](#deploying-to-railway)
- [Running Tests](#running-tests)
- [File Reference](#file-reference)

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

**Workflow:** create → assess (answer questions) → domain assigned → record notes and actions → mark decided/closed

---

### Delphi Method

The Delphi method is a structured forecasting technique developed at the RAND Corporation in the 1950s. It collects and aggregates anonymous expert opinion through iterative rounds until the group converges.

This tool implements the standard procedure:

1. **Round 1** — Open question; experts submit free-text responses
2. **Round 2+** — Items (distilled from responses) rated on a **1–9 scale** (1 = strongly disagree, 9 = strongly agree)
3. **Convergence** — Consensus is reached when the **IQR ≤ threshold** (default 2.0)

Statistics reported per item: mean, median, Q1, Q3, IQR, % near median, and a yes/no consensus verdict. Items can be rated across multiple rounds; the tool tracks which round each rating came from.

**Workflow:** create session → open round → share participant link → collect responses/ratings → close round → repeat until consensus

---

### Pre-Mortem

A Pre-Mortem (Gary Klein, 1989) imagines a project has already failed and asks participants to explain why. This surfaces hidden risks before commitment, overcoming optimism bias and groupthink.

**How it works:**

1. **Setup** — Describe the plan or initiative being evaluated
2. **Brainstorming** — Share the anonymous participant link; participants describe specific failure scenarios ("We failed because…")
3. **Reviewing** — The facilitator reads all scenarios and consolidates them into assessed risks with severity (1–5), likelihood (1–5), and mitigation notes
4. **Complete** — Present the risk register to the team and update the plan

Participant submissions are anonymous. The admin sees all scenarios; participants only see the submission form. A risk heat map groups risks by severity for quick triage.

**Workflow:** create → setup → brainstorming (share link) → reviewing (consolidate risks) → complete

---

### Decision Matrix

A weighted Decision Matrix (also called Pugh Matrix or criteria-based scoring) scores a set of options against defined criteria. Weights can be assigned to criteria by importance; participant scores are aggregated into a weighted average per option, ranking the options objectively.

**How it works:**

1. **Setup** — Add options (the alternatives being considered) and criteria (what matters); set a weight (1–5) per criterion. Weights are hidden from participants to prevent anchoring.
2. **Scoring** — Share the participant link; participants score each option 1–10 against each criterion (10 = best)
3. **Closed** — Review the ranked results; the tool computes weighted averages and displays a ranked bar chart

**Workflow:** create → add options and criteria → set weights → open scoring (share link) → collect scores → close → review results

---

### Six Thinking Hats

Six Thinking Hats (Edward de Bono, 1985) is a parallel thinking method where all participants think from the same perspective at the same time, eliminating debate and surfacing richer collective thinking.

| Hat | Perspective | Focus |
|---|---|---|
| 🤍 **White** | Facts & Information | What data do we have? What's missing? |
| ❤️ **Red** | Emotions & Intuition | Gut feelings — no justification needed |
| 🖤 **Black** | Caution & Risks | What could go wrong? Weaknesses? |
| 💛 **Yellow** | Optimism & Benefits | Best case, value, opportunities |
| 💚 **Green** | Creativity & Ideas | Alternatives, new approaches |
| 💙 **Blue** | Process & Summary | What conclusions can we draw? |

The facilitator opens one hat at a time. Participants submit contributions for the active hat via a shared link. When the facilitator closes a hat, contributions are locked and the next hat can be opened. After all six hats are closed, the session auto-completes.

**Workflow:** create → open White hat → collect contributions → close → open Red hat → … → all 6 closed → complete

---

## Admin & Participant Interfaces

Every framework with a participant-facing component has two separate interfaces:

**Admin interface** — requires login if `ADMIN_SECRET` is set. Gives the facilitator full control: create, configure, open/close rounds or hats, view all responses, consolidate results, archive, delete.

**Participant interface** — a public, anonymous URL that can be shared with participants. Shows only what participants need to see:
- Delphi: current round prompt, response and rating forms, closed round summaries only (no names, no statistics)
- Pre-Mortem: the plan being evaluated, a single scenario submission form (open only during brainstorming)
- Decision Matrix: the scoring grid (criteria shown without weights)
- Six Thinking Hats: the current open hat's prompt and a contribution form; completed hats listed below

Results and intermediate statistics are never shown to participants, preventing anchoring and conformity bias.

### Authentication

Set the `ADMIN_SECRET` environment variable to enable the login page at `/admin/login`. Without it, the app runs in open mode (no auth required) — suitable for local use.

```bash
ADMIN_SECRET=your-secret-password decision-web
```

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
| `/` | Dashboard — recent items across all frameworks |
| `/results` | Results dashboard — exportable PDF summary of all sessions |
| **Cynefin** | |
| `/cynefin/` | List decisions (filter by status, show archived) |
| `/cynefin/new` | Create a decision |
| `/cynefin/<id>` | Decision detail: assessment, domain guidance, notes, actions |
| **Delphi** | |
| `/delphi/` | List sessions |
| `/delphi/new` | Create a session |
| `/delphi/<id>` | Session detail: rounds, responses, items, ratings, consensus stats |
| `/participate/<id>` | Participant view — submit responses and ratings |
| **Pre-Mortem** | |
| `/premortem/` | List sessions |
| `/premortem/new` | Create a session |
| `/premortem/<id>` | Session detail: plan, scenarios, risk register |
| `/premortem/participate/<id>` | Participant view — submit failure scenarios |
| **Decision Matrix** | |
| `/matrix/` | List matrices |
| `/matrix/new` | Create a matrix |
| `/matrix/<id>` | Matrix detail: options, criteria, weights, ranked results |
| `/matrix/participate/<id>` | Participant view — score options against criteria |
| **Six Thinking Hats** | |
| `/sixhats/` | List sessions |
| `/sixhats/new` | Create a session |
| `/sixhats/<id>` | Session detail: hat management, contributions per hat |
| `/sixhats/participate/<id>` | Participant view — contribute to the current open hat |
| **Utility** | |
| `/health` | Health check endpoint (returns `{"status": "ok"}`) |
| `/admin/login` | Login (only shown when `ADMIN_SECRET` is set) |

### Archive and delete

Every record (decision, session, matrix) can be **archived** (hidden from the main list, data preserved) or **permanently deleted** (cascading hard delete). Both actions require an "Are you sure?" confirmation dialog. Archived items can be restored or viewed via the "Show archived" toggle on each list page.

---

## CLI Usage

The CLI covers Cynefin and Delphi only. Pre-Mortem, Decision Matrix, and Six Thinking Hats are web-only.

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

# Show full detail for a decision
cynefin show <id>

# Add a note or action
cynefin note <id> "Architecture review scheduled for Thursday"
cynefin action <id> "Spike: benchmark Kafka vs RabbitMQ"

# Mark an action resolved
cynefin resolve <action_id> "Kafka chosen — 3× throughput at p99"

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
delphi new "AI investment priorities" -q "Which AI initiatives should we fund in FY26?" -t 2.0

# Open a round for responses
delphi round open <sid>
delphi round open <sid> --prompt "Rate each initiative on feasibility and impact"

# Close a round with an optional summary
delphi round close <sid>
delphi round close <sid> --summary "Strong convergence on items 1 and 3"

# Submit a text response
delphi respond <sid> "Initiative A addresses the core bottleneck..."
delphi respond <sid> "..." --respondent "alice"

# Add an item for rating
delphi item <sid> "Fund initiative A: real-time inference pipeline"

# Submit numeric ratings (1–9) for all items
delphi rate <sid> --respondent "alice" 1=8 2=3 3=7

# Show session detail with per-item statistics
delphi show <sid>

# Print round statistics table
delphi summarize <sid>

# List sessions
delphi list
delphi list --status active

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

### Cynefin — transcripts

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
    {"text": "Initiative A directly addresses our inference bottleneck."}
  ],
  "ratings": [
    {"item_id": "abc12345", "rating": 8, "rationale": "High feasibility, proven stack"}
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
| `cynefin.db` | Decisions, assessment responses, notes, actions |
| `delphi.db` | Sessions, rounds, text responses, items, ratings, notes |
| `premortem.db` | Sessions, anonymous scenarios, assessed risks |
| `decision_matrix.db` | Sessions, options, criteria (with weights), participant scores |
| `sixhats.db` | Sessions, hat rows (6 per session), participant contributions |
| `decision_framework` | Active framework name (plain text) |

Override the data directory:

```bash
DECISION_SUPPORT_DATA_DIR=/data decision-web
```

All databases use WAL mode for safe concurrent reads and writes.

### Schema outline

**Cynefin**
```
decisions              id, title, description, domain, status, created_at, updated_at
assessment_responses   id, decision_id, question_id, answer_key, answer_label, scores, created_at
notes                  id, decision_id, content, source_type, created_at
actions                id, decision_id, action_text, outcome, created_at, resolved_at
```

**Delphi**
```
sessions    id, title, description, question, status, consensus_threshold, current_round, created_at
rounds      id, session_id, round_number, status, prompt, summary, opened_at, closed_at
responses   id, session_id, round_id, respondent, response_text, score, created_at
items       id, session_id, text, source_round, created_at
ratings     id, session_id, round_id, item_id, respondent, rating, rationale, created_at
notes       id, session_id, content, source_type, created_at
```

**Pre-Mortem**
```
pm_sessions    id, title, description, plan_text, status, created_at, updated_at
pm_scenarios   id, session_id, scenario_text, submitted_by, created_at
pm_risks       id, session_id, scenario_id, risk_title, risk_description,
               severity (1–5), likelihood (1–5), mitigation, created_at
```

**Decision Matrix**
```
dm_sessions   id, title, description, status, created_at, updated_at
dm_options    id, session_id, text, created_at
dm_criteria   id, session_id, text, weight (1–5), created_at
dm_scores     id, session_id, option_id, criterion_id, respondent, score (1–10), created_at
```

**Six Thinking Hats**
```
sh_sessions       id, title, description, topic, status, current_hat, created_at, updated_at
sh_hats           id, session_id, hat_color, order_num, status, opened_at, closed_at
sh_contributions  id, session_id, hat_id, hat_color, contributor, content, created_at
```

---

## Results Dashboard

The `/results` page is a unified admin view covering all five frameworks:

- **Cynefin** — domain distribution bar chart, status breakdown, decision list with pending action counts
- **Delphi** — sessions with consensus status, round counts, per-item IQR and median
- **Pre-Mortem** — sessions with risk counts grouped by severity
- **Decision Matrix** — sessions with ranked option results
- **Six Thinking Hats** — sessions with hat completion status and contribution counts

The page has a **Save as PDF** button (uses the browser's print dialog with print-optimised CSS).

---

## Deploying to Railway

1. Push the repository to GitHub.
2. Create a new Railway project and connect the GitHub repo.
3. Add a **persistent volume** mounted at `/data`.
4. Set environment variables in Railway settings (see table below).
5. Railway picks up `railway.toml` automatically.

```toml
# railway.toml (already included)
[deploy]
startCommand = "/bin/sh -c 'gunicorn decisionsupport.web:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120'"
healthcheckPath = "/health"
healthcheckTimeout = 60
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

The `Procfile` provides the same start command for Heroku-compatible platforms.

**Environment variables**

| Variable | Default | Description |
|---|---|---|
| `DECISION_SUPPORT_DATA_DIR` | `~/.decisionsupport` | Path for SQLite databases — set to `/data` on Railway |
| `SECRET_KEY` | `dev-change-me-in-production` | Flask session secret — **always change in production** |
| `ADMIN_SECRET` | _(unset)_ | Password for the admin interface — set to enable auth |
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

---

## File Reference

```
decisionsupport/
├── cynefin.py           Cynefin module — DB, questions, classification, transcript ingestion
├── cynefin_cli.py       cynefin CLI entry point
├── delphi.py            Delphi module — DB, statistics, consensus tracking
├── delphi_cli.py        delphi CLI entry point
├── premortem.py         Pre-Mortem module — DB for sessions, scenarios, risks
├── decision_matrix.py   Decision Matrix module — DB for options, criteria, scores; compute_results()
├── sixhats.py           Six Thinking Hats module — DB, hat lifecycle, contributions
├── decision.py          Framework toggle — shared state for CLI and web
├── decision_cli.py      decision CLI entry point (proxies to active framework)
├── web.py               Flask application — all routes for all five frameworks
└── templates/
    ├── base.html              Bootstrap 5 base layout, navbar (all 5 frameworks), confirm modal
    ├── index.html             Dashboard
    ├── results.html           Results dashboard (all frameworks, PDF-printable)
    ├── admin/
    │   └── login.html         Admin login page
    ├── participate/
    │   ├── show.html          Delphi participant view
    │   └── not_found.html     404 for invalid participant links
    ├── cynefin/
    │   ├── list.html          Decision list with status filter and archive toggle
    │   ├── new.html           Create decision form
    │   └── show.html          Decision detail, assessment, domain guidance, notes, actions
    ├── delphi/
    │   ├── list.html          Session list
    │   ├── new.html           Create session form
    │   └── show.html          Session detail, rounds, ratings, consensus stats, participant link
    ├── premortem/
    │   ├── list.html          Session list
    │   ├── new.html           Create session form
    │   ├── show.html          Session detail, scenarios, risk register, heat map
    │   ├── participate.html   Participant scenario submission form
    │   └── participate_closed.html  Shown when brainstorming is not open
    ├── matrix/
    │   ├── list.html          Matrix list
    │   ├── new.html           Create matrix form
    │   ├── show.html          Matrix detail, options, criteria, weights, ranked results
    │   ├── participate.html   Participant scoring grid
    │   └── participate_closed.html  Shown when scoring is not open
    └── sixhats/
        ├── list.html          Session list
        ├── new.html           Create session form
        ├── show.html          Session detail, hat controls, contributions by hat
        └── participate.html   Participant contribution form (current hat only)

tests/
├── test_cynefin.py      Cynefin unit tests
├── test_delphi.py       Delphi unit tests
├── test_decision.py     Framework toggle tests
└── test_web.py          Web integration tests (Flask test client)

Dockerfile              Container image definition
Procfile                Gunicorn start command (Heroku / Railway)
railway.toml            Railway deployment configuration
pyproject.toml          Package metadata and dependencies
```


---

## License

MIT License &copy; 2025 [Alastair Kocho-Williams](https://github.com/alikocho)

See [LICENSE](LICENSE) for the full text.
