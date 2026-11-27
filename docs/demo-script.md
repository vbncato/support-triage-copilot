# Demo script (60–90 seconds)

Setup (before the call): `docker compose up -d`, then open <http://127.0.0.1:8091/>. To start from untouched tickets, run `scripts\demo.ps1 -NoStart` and use the links it prints, or use the seeded `DEMO-00x` tickets that are still `NEW`.

| Time | Do | Say |
|---|---|---|
| 0:00 | Point at the **DEMO MODE** badge and the queue. | "A support desk for a fictional B2B API. Tickets come in about auth, rate limits, webhooks and delays. In demo mode the model's answers are recorded synthetic responses, so it runs without a key." |
| 0:10 | Open **DEMO-001 (401)** → **Run analysis**. | "The app searches the knowledge base with PostgreSQL full-text search and gives only those sections to the model. The model returns structured JSON: disposition, category, draft, sources and a reason. The app validates it and picks the queue." |
| 0:25 | Expand the **cited** source and click **Open section**. | "Every claim in the draft can be checked against the source text. 'Cited' only means the section was given to the model, so the operator still reads it." |
| 0:35 | **Approve draft**. Point at the history. | "The decision, the status change and the history event are one transaction. A second click or a stale tab gets HTTP 409." |
| 0:45 | Open **DEMO-002 (ambiguous)** → Run analysis. Edit one line → **Save edited text & approve**. | "No status code or request ID, so it asks for specific details from a fixed list. It can never ask for keys or tokens." |
| 0:60 | Open **DEMO-003 (VAT on invoices)** → Run analysis. | "Billing isn't covered: manual review, and no draft is prepared." |
| 0:70 | *(Optional)* **DEMO-004** → Run analysis. | "Here the recorded output cites a section that was never given to the model. The validator rejects it and Approve is not offered." |
| 0:80 | Open `evaluation/results/demo-holdout/report.md`. | "The evaluation report shows expected against actual for each ticket. Retrieval and the keyword baseline are measured for real. For example, retrieval misses the outage ticket ho-12. Model quality isn't claimed until a live run." |

Fallback if something fails live: run `powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 -NoStart`. It prints every scenario, including the 409, in the terminal.
