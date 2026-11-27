id: kb-webhook-signatures
title: Verifying webhook signatures
tags: webhook, signature, hmac, fernwick-signature, signing secret, verification failed
---
Each webhook request carries a `Fernwick-Signature` header: `t=<unix timestamp>,v1=<hex HMAC-SHA256>`. The signed payload is `<timestamp>.<raw request body>`, signed with the endpoint's signing secret.

Common reasons verification fails:

- The body was parsed and re-serialised before verification. Verification must use the raw bytes exactly as received.
- The endpoint uses the signing secret of a different endpoint or of the sandbox environment.
- The timestamp tolerance is too strict. Fernwick recommends a tolerance of 5 minutes.

The signing secret can be rolled in the dashboard. During a roll, both the old and new secrets are valid for 24 hours.

Support must never ask a customer to send the signing secret. Ask for the event ID and the exact error message from their verification code instead.
