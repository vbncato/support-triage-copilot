id: kb-diagnostic-details
title: Which diagnostic details to request from a customer
tags: request id, timestamp, status code, event id, job id, diagnostics, missing information
---
Ask for details only when the ticket does not contain enough to investigate. Ask for the smallest set that lets support find the request in the logs:

- API errors: the `X-Request-Id` response header (prefix `req_`), the UTC timestamp, the HTTP status code, the endpoint and method, and the `error.code` from the response body.
- Webhook problems: the event ID (prefix `evt_`), the endpoint URL, and the approximate UTC time.
- Delayed jobs: the job ID (prefix `job_`) and the creation time.
- Client libraries: the library name and version.

Never ask for passwords, API keys, access tokens, refresh tokens or webhook signing secrets. If a customer pastes a secret into a ticket, tell them to rotate it in the dashboard.

When a ticket only says that something "doesn't work" without a status code or ID, reply with a short question for these details rather than guessing the cause.
