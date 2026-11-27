id: kb-webhook-retries
title: Webhook retries, ordering and replay
tags: webhook, retry, replay, duplicate, ordering, idempotency, disabled endpoint
---
Failed webhook deliveries are retried with exponential backoff for up to 72 hours: after about 1 minute, 5 minutes, 30 minutes, 2 hours, and then every 6 hours.

- Events may arrive more than once and out of order. Endpoints should deduplicate by event ID and use the object's `updated_at` to decide which state is newer.
- An endpoint that fails every delivery for 72 hours is disabled automatically and the workspace admins receive an email. Re-enabling the endpoint does not resend old events.
- Events from the last 30 days can be replayed from Dashboard > Developers > Webhooks > Delivery log, or with `POST /v1/webhook_events/{event_id}/replay`.

Support can explain retries and replay. Support should not promise that a specific event will be redelivered before checking the delivery log.
