# Repository architecture

```text
support-triage-copilot/
├── README.md                     # task, flow, quick start, API, states, limitations
├── compose.yaml                  # app + PostgreSQL; app bound to 127.0.0.1:8091; no DB host port
├── Dockerfile                    # python:3.13.18-slim, non-root user, healthcheck
├── requirements.txt              # direct pins
├── requirements-dev.txt          # test pins
├── requirements.lock             # full resolved set installed in the image
├── .env.example                  # safe demo defaults, no secrets
├── pytest.ini
├── app/
│   ├── main.py                   # FastAPI: REST API, UI routes, startup (schema, seeds, expiry)
│   ├── config.py                 # settings, categories, category→queue map, requestable details
│   ├── db.py                     # pool (autocommit; explicit transactions), schema, KB/demo seeding
│   ├── tickets.py                # states, attempts, late results, expiry, operator decisions
│   ├── knowledge.py              # FTS query building, overlap rule, section lookup
│   ├── triage.py                 # one analysis run: retrieve → model (no tx) → validate → record
│   ├── llm/
│   │   ├── base.py               # adapter interface, error types
│   │   ├── contract.py           # JSON schema + app-side validator
│   │   ├── prompt.py             # system prompt, data-tagged user message
│   │   ├── demo.py               # replay of recorded synthetic responses
│   │   └── provider.py           # Anthropic Messages API adapter (live)
│   ├── templates/                # base, queue, ticket, kb (Jinja2, autoescape)
│   └── static/                   # app.css, app.js (textContent only), favicon.svg
├── db/init/001_schema.sql        # tables, constraints, FTS index (applied idempotently by the app)
├── fixtures/
│   ├── knowledge/*.md            # 12 synthetic KB sections with stable IDs
│   ├── tickets/demo-tickets.json # 8 demo tickets, one scenario each
│   └── model-demo/recordings.json# recorded synthetic model responses for the demo tickets
├── evaluation/
│   ├── run.py                    # CLI: per-case results + report
│   ├── baseline.py               # keyword routing baseline
│   ├── demo_mechanics.py         # label-echo stand-in for demo-mode scoring checks
│   ├── config-freeze.json        # config hash recorded before the holdout run
│   ├── cases/dev.jsonl, holdout.jsonl
│   └── results/                  # demo-dev/, demo-holdout/ (report.md, summary.json, cases.jsonl)
├── scripts/demo.ps1              # Windows PowerShell 5.1 demo
├── tests/{unit,integration,e2e}/
└── docs/
    ├── architecture.md
    ├── demo-script.md
    ├── evaluation.md
    └── screenshots/ticket-review.png
```

## Responsibilities

- `tickets.py` owns ticket versions. Every state change locks the ticket row, checks the caller's version, and writes the change and its history event in one transaction. The lock order is always ticket → attempt.
- `triage.py` runs the model call with no transaction open and no row locked (tested). Results go to the specific attempt; `finish_attempt` applies them only while that attempt is current and running.
- `llm/` hides provider details. Demo mode is labelled in the UI, on every attempt and in reports.
- `knowledge.py` returns sections with stable IDs. Each attempt stores exactly the IDs given to the model, and the validator checks citations against that list.
- `evaluation/` reads labels separately from model input. Reports state split, mode, model, date, config hash and case count.
