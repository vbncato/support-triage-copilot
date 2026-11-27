id: kb-api-key-scopes
title: API key scopes and workspace roles
tags: api key, scope, roles, admin, developer, viewer, permissions
---
Each API key has a list of scopes, such as `invoices:read`, `invoices:write`, `customers:read`, `webhooks:manage` and `exports:run`. Scopes are chosen when the key is created and can be edited later by a workspace admin.

Workspace roles control who can manage keys:

- Admin: can create, edit and rotate API keys and manage webhook endpoints.
- Developer: can create keys with read scopes only and view webhook delivery logs.
- Viewer: cannot create keys.

If a customer says a teammate "has no access" to a dashboard page, it is a role question, not an API error. Role changes are made by a workspace admin in Dashboard > Settings > Members. Support cannot change roles on the customer's behalf.
