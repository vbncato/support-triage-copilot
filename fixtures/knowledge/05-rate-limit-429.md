id: kb-rate-limit-429
title: 429 Too Many Requests and the Retry-After header
tags: 429, rate limit, too many requests, retry-after, backoff, throttling
---
Fernwick returns HTTP 429 when a workspace exceeds its request rate. A 429 response always includes a `Retry-After` header with the number of seconds to wait before retrying.

How the client should handle 429:

1. Stop sending requests on that key and wait for the number of seconds in `Retry-After`.
2. If `Retry-After` is missing (for example, removed by a proxy), use exponential backoff starting at 1 second, doubling up to 60 seconds, with random jitter.
3. Retry only idempotent requests automatically. For `POST` requests, send an `Idempotency-Key` header so a retry cannot create a duplicate object.

Retrying immediately in a tight loop keeps the workspace over the limit and extends the throttling period. The rate-limit headers on every response (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`) let clients slow down before they hit 429.
