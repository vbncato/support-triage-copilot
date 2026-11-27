id: kb-rate-limit-tiers
title: Rate limit tiers and limit increases
tags: rate limit, quota, tier, limit increase, burst, 429
---
Default limits per workspace:

- Standard plan: 100 requests per second, burst up to 200.
- Scale plan: 500 requests per second, burst up to 1000.
- Export endpoints (`/v1/exports`): 5 concurrent export jobs on every plan.

Limits apply per workspace, not per API key. Creating more keys does not raise the limit.

Requests for a limit increase are handled by the account team, not by support. Support should route limit-increase requests to manual review and include the customer's stated peak rate and use case. Support must not promise a new limit or a date.
