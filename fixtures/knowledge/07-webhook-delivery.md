id: kb-webhook-delivery
title: Webhook not delivered: troubleshooting checklist
tags: webhook, delivery, endpoint, event, not received, callback, 2xx, timeout
---
Fernwick sends webhook events as `POST` requests to the endpoint URL configured in Dashboard > Developers > Webhooks. A delivery counts as successful only when the endpoint returns a 2xx status within 10 seconds.

Checklist when a customer says an event was not received:

1. Ask for the event ID (prefix `evt_`) or the object ID and the approximate UTC time. With the event ID, support can see every delivery attempt in the delivery log.
2. Check that the endpoint is enabled and subscribed to the event type. New event types are not added to existing endpoints automatically.
3. Check the endpoint's response in the delivery log. Common failures: a 3xx redirect (redirects are not followed), a 401/403 from the customer's own auth layer, a TLS certificate error, or a timeout because the endpoint does work before responding. The endpoint should respond 2xx first and process the event afterwards.
4. Check that the customer's firewall allows the Fernwick sending IP ranges listed in the dashboard.

The customer can also see delivery attempts themselves in Dashboard > Developers > Webhooks > Delivery log.
