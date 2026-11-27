# Evaluation: method, results, limits

## What is measured and what is not

| Component | Deterministic? | Measured here? |
|---|---|---|
| Retrieval (PostgreSQL FTS + overlap rule) | yes | **yes**: real numbers below |
| Keyword routing baseline (`evaluation/baseline.py`) | yes | **yes**: real numbers below |
| LLM routing, disposition, citations, manual-review recall, draft quality | no | **NOT MEASURED.** No live run was made, because no API key was issued for this project |

The demo-mode runs also fill the "model" columns. Those values come from `evaluation/demo_mechanics.py`, a stand-in that copies the labels and injects known faults: a wrong category, an invented source, a wrong disposition and a missing draft. They only show that the scoring code catches each fault type. **They are not model metrics** and must not be quoted as such.

## Dataset

`evaluation/cases/dev.jsonl` (18) and `evaluation/cases/holdout.jsonl` (12). All 30 tickets are synthetic.

| | dev | holdout |
|---|---|---|
| AUTHENTICATION | 3 (+1 injection with 401) | 2 (+1 ambiguous 401/403, +1 leaked key) |
| PERMISSIONS | 3 | 1 |
| RATE_LIMIT | 3 | 2 |
| WEBHOOK_DELIVERY | 4 | 2 |
| PROCESSING_DELAY | 2 | 1 |
| OTHER / out of scope | 1 GDPR + 1 "API is broken" (any category) | 1 roadmap question, 1 outage |
| Must go to manual review | 5 (suspension, limit increase, stuck job, GDPR, injection) | 3 (leaked key, roadmap, outage) |
| Expected NEEDS_INFO | 2 | 2 |

Each case has: `id`, `subject`, `body`, `expected_categories` (a list of acceptable categories, or `null` for "any"), `expected_dispositions` (acceptable list), `allowed_sources`, `must_manual`, `allowed_facts` (what a reply may claim) and `reasonable_requests` (details worth asking for). Queues are derived from categories by the app mapping. Labels are read only by `evaluation/run.py` and never sent to a model (`test_prompt_has_no_labels`).

## Metrics

All are reported as numerator/denominator.

- **Retrieval hit@k**: cases with labelled sources where ≥1 allowed section is among the sections actually given to the model (after the overlap rule, k = 4). Also hit@1.
- **Routing**: correct queue among cases with a labelled category (`expected_categories` not null). Failed attempts count as wrong. The baseline and the model are scored on the same cases.
- **Disposition**: the predicted disposition is in the acceptable list.
- **Valid source IDs**: cited IDs in the raw model output that were among the sections given, divided by all cited IDs.
- **Drafts with a relevant source**: drafts that cite at least one labelled allowed source. This checks the citation label only, not whether the text matches the source.
- **Manual-review recall**: must-manual cases whose outcome is MANUAL_REVIEW. Over-escalation (other cases sent to manual) is reported next to it.
- **Text quality**: manual review of live drafts against `allowed_facts` and the cited section. Not done: there are no live drafts.

## Configuration freeze

Retrieval (overlap rule, k = 4), KB wording and baseline keywords were tuned on **dev only**. Config hash **`17972bad6e92`** (computed by the dev run at 00:01:31 UTC) was written to `evaluation/config-freeze.json` at 00:01:47 UTC, before the first holdout run at 00:01:50 UTC. The hash covers the prompt, schema, model, effort, retrieval parameters, KB content and baseline keywords. The holdout run below used the same hash. **No change was made after looking at holdout.** The holdout errors below are left unfixed, so the holdout numbers stay an independent check.

A tuning note on dev (the change was made before the holdout run; this note was written afterwards): the first retrieval design dropped sections below an absolute `ts_rank` of 0.05. While writing integration tests I found that a single common word ("order" from "out of order") gave rank 0.11 on an unrelated ticket, higher than the real match on short dev-16 (0.076). The floor was replaced by the overlap rule (≥ 2 distinct query terms). On dev, every case still keeps an allowed section, and the unrelated ticket falls through to `NO_KB_MATCH`.

## Results (mode `demo`, run 2026-11-27, config `17972bad6e92`)

Reports: `evaluation/results/demo-dev/report.md` and `evaluation/results/demo-holdout/report.md`, with per-case `cases.jsonl`.

### Real measurements (deterministic components)

| Metric | dev | holdout |
|---|---|---|
| Retrieval: allowed section in top-k | 18/18 (100%) | **10/12 (83.3%)** |
| Retrieval: allowed section at rank 1 | 14/18 (77.8%) | 8/12 (66.7%) |
| Retrieval: no section left (rule NO_KB_MATCH) | 0/18 | 0/12 |
| Keyword baseline: correct queue | 17/17 (100%) | **11/12 (91.7%)** |

The dev numbers are optimistic by construction: the baseline keywords and the retrieval rule were written while looking at dev. Holdout is the honest estimate, and it shows the errors.

**Where retrieval fails (holdout):**
- `ho-11` "Do you plan a GraphQL API?" is labelled with `kb-escalation-manual-review` ("topics the knowledge base does not cover"). The ticket shares no words with that section. Only `kb-rate-limit-tiers` passes the overlap rule, on the words "plan" and "endpoint", so the model would see one irrelevant section.
- `ho-12` "All requests return 500 for every customer we know" should get the escalation section ("possible platform-wide outage"). The escalation section matches only generic words (`request(s)`, `customer`, `internal`) and is outranked by diagnostics, 429, webhook and 401 sections, so it misses the top 4. The word "outage" is not in the ticket ("Is Fernwick down?").

Both are lexical-search limits: a section about "what we don't cover" or "outages" cannot be found by word overlap with tickets that never use those words. Possible fixes, deliberately not applied to keep holdout clean: always append `kb-escalation-manual-review` to the model input, or add synonyms or tags to that section.

**Rank-1 misses with the right section still in top-k:** dev-01, dev-12, dev-17, dev-18, ho-04 and ho-10. Generic sections (`kb-diagnostic-details`, `kb-api-key-scopes`) often outrank the specific one, because they share many everyday words with tickets.

**Where the keyword baseline fails (holdout):** `ho-11` is routed to `integrations` because the word "endpoint" ("GraphQL endpoint") is a WEBHOOK_DELIVERY keyword. The correct queue is `general-support`. The baseline cannot tell the topic of a sentence from the words in it.

### Mechanics check (demo stand-in, NOT model quality)

| Metric | dev | holdout |
|---|---|---|
| Routing (stand-in) | 13/17 | 11/12 |
| Disposition (stand-in) | 14/18 | 10/12 |
| Valid cited IDs | 18/20 | 10/11 |
| Failed attempts | 3/18 (`CONTRACT_VIOLATION`) | 1/12 |

The injected faults appear exactly where they were injected:
- dev-06 and dev-18: invented source → `UNKNOWN_SOURCE`.
- dev-09: wrong disposition.
- dev-12: missing draft → `DRAFT_REQUIRED`.
- dev-15: wrong category → wrong queue.
- dev-03: wrong category inside the same queue (AUTHENTICATION → PERMISSIONS), so the queue metric correctly stays OK.

So the scoring code detects each fault type.

## Baseline vs model

**Not available.** Comparing them needs a live run. The command is in the README ("Live mode"). After a live run, `report.md` lists `routing_comparison` (both correct / only baseline / only model / both wrong) and every disagreement per case. If the baseline wins, check the `only_baseline` cases first.

## Text quality review

Not done: there are no live drafts to review. Planned procedure for a live run: for each DRAFT_REPLY or NEEDS_INFO draft in `cases.jsonl`, check that
1. every factual claim is in `allowed_facts` or in a cited section;
2. no secret is requested;
3. NEEDS_INFO asks for the details in `reasonable_requests`;
4. no date, limit or promise is invented.

Record typical errors here with case IDs, and do not give scores for drafts that were not read.

## Limits of this evaluation

- 30 synthetic cases written by one person. They are useful for finding failure modes, not for a statistically meaningful accuracy figure.
- Labels for ambiguous tickets accept several answers, so "correct" is generous there.
- "Drafts with a relevant source" checks the citation label, not faithfulness.
- The holdout has now been seen. Any future change to the system needs a new holdout to claim an independent check.
