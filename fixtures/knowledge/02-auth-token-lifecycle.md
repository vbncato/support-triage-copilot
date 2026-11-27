id: kb-auth-token-lifecycle
title: Access tokens: lifetime, refresh and clock skew
tags: token, refresh, oauth, client credentials, expiry, clock skew, 401
---
Access tokens are issued by `POST /v1/oauth/token` and expire 60 minutes after issue. The `expires_in` field in the token response gives the remaining lifetime in seconds.

Recommended client behaviour:

1. Cache the token and refresh it about 5 minutes before `expires_in` runs out.
2. If a call still returns 401 with `error.code = token_expired`, refresh once and retry the call once. Do not retry in a loop.
3. Refresh tokens are single-use. Using an old refresh token returns 400 `invalid_grant`, and the client must re-authenticate with its client credentials.

Clock skew: Fernwick validates token expiry against its own clock. If the customer's servers are more than 5 minutes off UTC, tokens can look valid locally but be rejected. Ask the customer to check that NTP is running on their servers.

Rotating the API key in the dashboard revokes all tokens issued with the old key. Calls then fail with `invalid_token`, not `token_expired`.
