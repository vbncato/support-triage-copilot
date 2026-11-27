# Evaluation report: demo-holdout

> **DEMO MODE: mechanics only. Model quality NOT MEASURED.** Model rows come from a label-echo stand-in with injected faults (`evaluation/demo_mechanics.py`). Retrieval and keyword-baseline rows are real measurements of deterministic code.

| Field | Value |
|---|---|
| mode | `demo` |
| model | `demo-mechanics-label-echo` |
| split | `holdout` |
| cases | `12` |
| started_at_utc | `2026-11-27T00:01:50+00:00` |
| config_hash | `17972bad6e92` |
| top_k | `4` |
| min_terms | `2` |
| effort | `n/a` |
| injected_faults | `{'ho-03': 'wrong_category', 'ho-06': 'invented_source', 'ho-09': 'wrong_disposition', 'ho-12': 'missing_draft'}` |

## Metrics (numerator/denominator)

| Metric | Result | Notes |
|---|---|---|
| Retrieval: ≥1 allowed section in top-k | 10/12 (83.3%) | cases with labelled sources |
| Retrieval: allowed section at rank 1 | 8/12 (66.7%) | |
| Retrieval: no section above threshold (rule NO_KB_MATCH) | 0/12 (0.0%) | |
| Routing: keyword baseline, correct queue | 11/12 (91.7%) | cases with labelled category |
| Routing: stand-in (not a model metric), correct queue | 11/12 (91.7%) | failures count as wrong |
| Disposition correct | 10/12 (83.3%) | stand-in (not a model metric) |
| Cited IDs that were given to the model | 10/11 (90.9%) | raw output, before validation |
| Drafts citing an allowed (labelled) source | 8/8 (100.0%) | citation relevance by label, not text faithfulness |
| Must-manual cases routed to manual review | 3/3 (100.0%) | |
| Other cases sent to manual review | 0/9 (0.0%) | over-escalation |
| Decided by app rule (model not called) | 0/12 (0.0%) | |
| Failed attempts | 1/12 (8.3%) | {'CONTRACT_VIOLATION': 1} |

Routing comparison on labelled cases: `{'both_correct': 10, 'only_baseline': 1, 'only_model': 1}`

## Per-case results (expected vs actual)

| Case | Tags | Expected queue / disposition | Baseline queue | Model queue / disposition | Retrieved (top-k) | Cited | Check |
|---|---|---|---|---|---|---|---|
| ho-01 | clear | access-and-identity · DRAFT_REPLY | access-and-identity | access-and-identity · DRAFT_REPLY | kb-auth-401, kb-rate-limit-429, kb-permissions-403, kb-diagnostic-details | kb-auth-401 | OK |
| ho-02 | clear | access-and-identity · DRAFT_REPLY | access-and-identity | access-and-identity · DRAFT_REPLY | kb-auth-token-lifecycle, kb-auth-401, kb-permissions-403, kb-escalation-manual-review | kb-auth-token-lifecycle | OK |
| ho-03 | ambiguous, needs_info | access-and-identity · NEEDS_INFO | access-and-identity | access-and-identity · NEEDS_INFO | kb-permissions-403, kb-auth-401, kb-processing-delay, kb-auth-token-lifecycle | kb-permissions-403 | OK |
| ho-04 | clear | access-and-identity · DRAFT_REPLY | access-and-identity | access-and-identity · DRAFT_REPLY | kb-api-key-scopes, kb-permissions-403, kb-diagnostic-details, kb-rate-limit-429 | kb-permissions-403 | OK |
| ho-05 | clear | api-platform · DRAFT_REPLY | api-platform | api-platform · DRAFT_REPLY | kb-rate-limit-429, kb-rate-limit-tiers, kb-api-key-scopes, kb-diagnostic-details | kb-rate-limit-tiers | OK |
| ho-06 | clear | api-platform · DRAFT_REPLY | api-platform | — · CONTRACT_VIOLATION | kb-rate-limit-429, kb-webhook-retries, kb-auth-token-lifecycle, kb-auth-401 | kb-rate-limit-429, kb-does-not-exist | ✗ queue, disposition, CONTRACT_VIOLATION |
| ho-07 | clear | integrations · DRAFT_REPLY | integrations | integrations · DRAFT_REPLY | kb-webhook-retries, kb-webhook-delivery, kb-webhook-signatures, kb-diagnostic-details | kb-webhook-retries | OK |
| ho-08 | clear | integrations · DRAFT_REPLY | integrations | integrations · DRAFT_REPLY | kb-webhook-delivery, kb-webhook-signatures, kb-webhook-retries, kb-diagnostic-details | kb-webhook-delivery | OK |
| ho-09 | needs_info | data-pipeline · NEEDS_INFO | data-pipeline | data-pipeline · DRAFT_REPLY | kb-processing-delay | kb-processing-delay | ✗ disposition |
| ho-10 | must_manual | access-and-identity/general-support · MANUAL_REVIEW | general-support | access-and-identity · MANUAL_REVIEW | kb-api-key-scopes, kb-permissions-403, kb-auth-401, kb-escalation-manual-review | kb-escalation-manual-review | OK |
| ho-11 | out_of_scope, must_manual | general-support · MANUAL_REVIEW | integrations | general-support · MANUAL_REVIEW | kb-rate-limit-tiers | — | ✗ retrieval, baseline-queue |
| ho-12 | must_manual | general-support · MANUAL_REVIEW | general-support | general-support · MANUAL_REVIEW | kb-diagnostic-details, kb-rate-limit-429, kb-webhook-delivery, kb-auth-401 | — | ✗ retrieval |

## Text quality

Not assessed: demo drafts are placeholders, not model output.
