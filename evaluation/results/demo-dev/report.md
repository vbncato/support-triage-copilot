# Evaluation report: demo-dev

> **DEMO MODE: mechanics only. Model quality NOT MEASURED.** Model rows come from a label-echo stand-in with injected faults (`evaluation/demo_mechanics.py`). Retrieval and keyword-baseline rows are real measurements of deterministic code.

| Field | Value |
|---|---|
| mode | `demo` |
| model | `demo-mechanics-label-echo` |
| split | `dev` |
| cases | `18` |
| started_at_utc | `2026-11-27T00:01:31+00:00` |
| config_hash | `17972bad6e92` |
| top_k | `4` |
| min_terms | `2` |
| effort | `n/a` |
| injected_faults | `{'dev-03': 'wrong_category', 'dev-06': 'invented_source', 'dev-09': 'wrong_disposition', 'dev-12': 'missing_draft', 'dev-15': 'wrong_category', 'dev-18': 'invented_source'}` |

## Metrics (numerator/denominator)

| Metric | Result | Notes |
|---|---|---|
| Retrieval: ≥1 allowed section in top-k | 18/18 (100.0%) | cases with labelled sources |
| Retrieval: allowed section at rank 1 | 14/18 (77.8%) | |
| Retrieval: no section above threshold (rule NO_KB_MATCH) | 0/18 (0.0%) | |
| Routing: keyword baseline, correct queue | 17/17 (100.0%) | cases with labelled category |
| Routing: stand-in (not a model metric), correct queue | 13/17 (76.5%) | failures count as wrong |
| Disposition correct | 14/18 (77.8%) | stand-in (not a model metric) |
| Cited IDs that were given to the model | 18/20 (90.0%) | raw output, before validation |
| Drafts citing an allowed (labelled) source | 13/13 (100.0%) | citation relevance by label, not text faithfulness |
| Must-manual cases routed to manual review | 2/5 (40.0%) | |
| Other cases sent to manual review | 0/13 (0.0%) | over-escalation |
| Decided by app rule (model not called) | 0/18 (0.0%) | |
| Failed attempts | 3/18 (16.7%) | {'CONTRACT_VIOLATION': 3} |

Routing comparison on labelled cases: `{'both_correct': 13, 'only_baseline': 4}`

## Per-case results (expected vs actual)

| Case | Tags | Expected queue / disposition | Baseline queue | Model queue / disposition | Retrieved (top-k) | Cited | Check |
|---|---|---|---|---|---|---|---|
| dev-01 | clear | access-and-identity · DRAFT_REPLY | access-and-identity | access-and-identity · DRAFT_REPLY | kb-diagnostic-details, kb-auth-401, kb-auth-token-lifecycle, kb-permissions-403 | kb-auth-401 | OK |
| dev-02 | clear | access-and-identity · DRAFT_REPLY | access-and-identity | access-and-identity · DRAFT_REPLY | kb-auth-401, kb-auth-token-lifecycle, kb-permissions-403, kb-api-key-scopes | kb-auth-token-lifecycle | OK |
| dev-03 | clear | access-and-identity · DRAFT_REPLY | access-and-identity | access-and-identity · DRAFT_REPLY | kb-auth-401, kb-auth-token-lifecycle, kb-permissions-403, kb-rate-limit-429 | kb-auth-401 | OK |
| dev-04 | clear | access-and-identity · DRAFT_REPLY | access-and-identity | access-and-identity · DRAFT_REPLY | kb-permissions-403, kb-api-key-scopes, kb-processing-delay, kb-diagnostic-details | kb-permissions-403 | OK |
| dev-05 | clear | access-and-identity · DRAFT_REPLY | access-and-identity | access-and-identity · DRAFT_REPLY | kb-api-key-scopes, kb-permissions-403, kb-rate-limit-429, kb-rate-limit-tiers | kb-api-key-scopes | OK |
| dev-06 | must_manual | access-and-identity/general-support · MANUAL_REVIEW | access-and-identity | — · CONTRACT_VIOLATION | kb-permissions-403, kb-diagnostic-details, kb-rate-limit-429, kb-api-key-scopes | kb-permissions-403, kb-does-not-exist | ✗ queue, disposition, CONTRACT_VIOLATION |
| dev-07 | clear | api-platform · DRAFT_REPLY | api-platform | api-platform · DRAFT_REPLY | kb-rate-limit-429, kb-webhook-retries, kb-escalation-manual-review, kb-diagnostic-details | kb-rate-limit-429 | OK |
| dev-08 | clear | api-platform · DRAFT_REPLY | api-platform | api-platform · DRAFT_REPLY | kb-rate-limit-429, kb-diagnostic-details, kb-auth-401, kb-auth-token-lifecycle | kb-rate-limit-429 | OK |
| dev-09 | must_manual | api-platform · MANUAL_REVIEW | api-platform | api-platform · DRAFT_REPLY | kb-rate-limit-tiers, kb-rate-limit-429, kb-diagnostic-details, kb-escalation-manual-review | kb-rate-limit-tiers | ✗ disposition |
| dev-10 | clear | integrations · DRAFT_REPLY | integrations | integrations · DRAFT_REPLY | kb-webhook-delivery, kb-webhook-signatures, kb-webhook-retries, kb-diagnostic-details | kb-webhook-delivery | OK |
| dev-11 | clear | integrations · DRAFT_REPLY | integrations | integrations · DRAFT_REPLY | kb-webhook-signatures, kb-webhook-delivery, kb-diagnostic-details, kb-webhook-retries | kb-webhook-signatures | OK |
| dev-12 | clear | integrations · DRAFT_REPLY | integrations | — · CONTRACT_VIOLATION | kb-webhook-delivery, kb-webhook-retries, kb-webhook-signatures, kb-diagnostic-details | kb-webhook-retries | ✗ queue, disposition, CONTRACT_VIOLATION |
| dev-13 | needs_info | integrations · NEEDS_INFO | integrations | integrations · NEEDS_INFO | kb-diagnostic-details, kb-auth-401 | kb-diagnostic-details | OK |
| dev-14 | clear | data-pipeline · DRAFT_REPLY | data-pipeline | data-pipeline · DRAFT_REPLY | kb-processing-delay, kb-diagnostic-details, kb-rate-limit-tiers, kb-api-key-scopes | kb-processing-delay | OK |
| dev-15 | must_manual | data-pipeline · MANUAL_REVIEW | data-pipeline | general-support · MANUAL_REVIEW | kb-processing-delay, kb-diagnostic-details, kb-api-key-scopes, kb-permissions-403 | kb-processing-delay | ✗ queue |
| dev-16 | ambiguous, needs_info | any · NEEDS_INFO/MANUAL_REVIEW | general-support | general-support · NEEDS_INFO | kb-diagnostic-details, kb-auth-401, kb-webhook-delivery | kb-diagnostic-details | OK |
| dev-17 | out_of_scope, must_manual | general-support · MANUAL_REVIEW | general-support | general-support · MANUAL_REVIEW | kb-diagnostic-details, kb-escalation-manual-review, kb-rate-limit-429, kb-webhook-delivery | kb-escalation-manual-review | OK |
| dev-18 | injection, must_manual | access-and-identity/general-support · MANUAL_REVIEW | access-and-identity | — · CONTRACT_VIOLATION | kb-api-key-scopes, kb-permissions-403, kb-escalation-manual-review, kb-auth-401 | kb-escalation-manual-review, kb-does-not-exist | ✗ queue, disposition, CONTRACT_VIOLATION |

## Text quality

Not assessed: demo drafts are placeholders, not model output.
