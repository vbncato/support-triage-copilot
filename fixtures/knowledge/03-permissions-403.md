id: kb-permissions-403
title: 403 Forbidden and how it differs from 401
tags: 403, forbidden, permissions, scope, role, insufficient_scope, 401
---
HTTP 403 means the request was authenticated, but the credentials are not allowed to perform this action. HTTP 401 means Fernwick could not establish who is calling at all. Refreshing the token fixes some 401 errors but never fixes a 403.

The 403 response body contains `error.code`:

- `insufficient_scope`: the API key does not have the scope the endpoint needs. For example, `POST /v1/invoices` needs `invoices:write`. The response lists the missing scope in `error.required_scope`. A workspace admin can add the scope to the key in Dashboard > Developers > API keys.
- `workspace_mismatch`: the key belongs to a different workspace than the resource ID in the URL.
- `account_suspended`: the workspace is suspended. This case must go to manual review by the account team. Support must not explain the suspension reason in the first reply.

To diagnose, ask for the endpoint and method, the `error.code`, and the `X-Request-Id` header of a failing call.
