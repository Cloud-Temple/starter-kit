# PolicyStore specification

> Status: specification / no runtime change in this document  
> Scope: full PolicyStore design for the MCP starter-kit  
> Related work items: WI-015, WI-016  
> Depends on: `docs/owner-based-isolation.md`

## 1. Problem

The starter-kit currently stores token metadata such as:

```json
{
  "client_name": "agent-a",
  "permissions": ["read", "write"],
  "allowed_resources": [],
  "policy_id": "readonly"
}
```

`policy_id` is currently metadata only. It does not enforce tool-level or path-level restrictions.

A full PolicyStore is needed to define and enforce policy rules such as:

- which tools a token may call;
- which tools are explicitly denied;
- which paths or internal resources a token may access;
- how policies are stored, managed and migrated across S3 and MCP Vault backends.

This document specifies the PolicyStore design before implementation. It intentionally does **not** implement runtime changes.

## 2. Goals

- Define a clear `Policy` data model.
- Specify `policy_id` semantics.
- Separate resource-level access from policy-level access.
- Define tool-level policy rules:
  - `allowed_tools`
  - `denied_tools`
- Define path-level policy rules:
  - `allowed_paths`
  - `path_rules`
- Define backend abstraction:
  - `PolicyStoreProtocol`
  - `S3PolicyStore`
  - `VaultPolicyStore`
- Define admin API, CLI, shell and `/admin` UI requirements.
- Define enforcement order.
- Define compatibility and migration behavior.
- Define required tests before implementation merge.

## 3. Non-goals

This specification does **not** implement:

- runtime PolicyStore code;
- owner-based isolation code;
- a specific business ownership model;
- a complete policy admin UI design;
- policy migration automation;
- multi-policy composition beyond one `policy_id` per token.

Owner-based resource isolation is specified separately in:

```text
docs/owner-based-isolation.md
s3://company/mcp-starter-kit/06_OWNER_BASED_ISOLATION_SPEC.md
```

This PolicyStore spec must not recode or redefine that resource-level model.

## 4. Architecture decision

Use a dedicated PolicyStore abstraction, separate from TokenStore, but with the same backend-configurable pattern.

Recommended architecture:

```text
PolicyStoreProtocol
├── S3PolicyStore
└── VaultPolicyStore
```

In parallel with:

```text
TokenStoreProtocol
├── S3TokenStore
└── VaultTokenStore
```

Rationale:

- tokens and policies have different lifecycles;
- tokens contain credentials metadata and revocation/expiration state;
- policies contain reusable authorization rules;
- a policy can be referenced by multiple tokens;
- admin operations for policies should be explicit;
- policy absence/failure can have stricter fail-close rules.

## 5. Configuration

Proposed configuration:

```env
POLICY_STORE_BACKEND=s3|vault|disabled
POLICY_STORE_CACHE_TTL=300
POLICY_STORE_FAIL_MODE=fail_close

S3_POLICY_STORE_KEY=_system/policies.json
MCP_VAULT_POLICY_STORE_PATH=token-store/policies.json
```

`POLICY_STORE_BACKEND` is a dedicated configuration and should not automatically mirror `TOKEN_STORE_BACKEND`.

Rationale: TokenStore and PolicyStore have different lifecycles. Valid deployments may intentionally use:

```text
TOKEN_STORE_BACKEND=vault  + POLICY_STORE_BACKEND=disabled
TOKEN_STORE_BACKEND=s3     + POLICY_STORE_BACKEND=s3
TOKEN_STORE_BACKEND=vault  + POLICY_STORE_BACKEND=vault
```

Recommended behavior:

| Config | Meaning |
|---|---|
| unset | compatibility default: same as `disabled` |
| `POLICY_STORE_BACKEND=disabled` | no policy enforcement unless a token explicitly references `policy_id` |
| `POLICY_STORE_BACKEND=s3` | load policies from S3 JSON object |
| `POLICY_STORE_BACKEND=vault` | load policies from MCP Vault secret |

For backward compatibility, default must not unexpectedly deny existing tokens without `policy_id`. New strict templates can explicitly set `POLICY_STORE_BACKEND=s3` or `POLICY_STORE_BACKEND=vault`.

## 6. Policy data model

Recommended V1 format:

```json
{
  "policies": [
    {
      "id": "readonly",
      "name": "Read-only policy",
      "description": "Allows safe read-only tools",
      "enabled": true,
      "allowed_tools": ["system_*", "search_*", "get_*"],
      "denied_tools": ["*_admin_*", "delete_*", "write_*"],
      "allowed_paths": [],
      "path_rules": [
        {
          "effect": "allow",
          "pattern": "public/*",
          "actions": ["read"]
        },
        {
          "effect": "deny",
          "pattern": "private/*",
          "actions": ["read", "write"]
        }
      ],
      "created_at": "2026-01-01T00:00:00Z",
      "updated_at": "2026-01-01T00:00:00Z"
    }
  ]
}
```

### Required fields

| Field | Type | Required | Meaning |
|---|---:|---:|---|
| `id` | string | yes | stable policy id referenced by token `policy_id` |
| `enabled` | bool | yes | disabled policies must not allow access |
| `allowed_tools` | list[string] | no | allowlist patterns for tool names |
| `denied_tools` | list[string] | no | denylist patterns for tool names |
| `allowed_paths` | list[string] | no | simple allowlist for path-like resources |
| `path_rules` | list[object] | no | ordered or priority path rules |

### Optional metadata

| Field | Type |
|---|---:|
| `name` | string |
| `description` | string |
| `created_at` | ISO datetime |
| `updated_at` | ISO datetime |
| `created_by` | string |
| `tags` | list[string] |

## 7. `policy_id` semantics

### Token without `policy_id`

For compatibility, a token without `policy_id` should keep historical behavior by default:

```text
no policy_id -> no tool-level/path-level policy restriction
```

Resource-level checks still apply:

```text
permissions + allowed_resources + owner-based isolation configuration
```

Sensitive templates may choose a stricter mode later, but it must be explicit and documented.

### Token with `policy_id`

If a token has a non-empty `policy_id`:

1. PolicyStore must be configured and available.
2. The policy id must exist.
3. The policy must be enabled.
4. The policy must be enforced.

Recommended behavior:

| Case | Result |
|---|---|
| token has no `policy_id` | compatibility: no policy enforcement |
| token has `policy_id`, policy exists and enabled | enforce it |
| token has `policy_id`, policy missing | deny / fail-close |
| token has `policy_id`, policy disabled | deny / fail-close |
| token has `policy_id`, PolicyStore unavailable | deny / fail-close |
| `POLICY_STORE_BACKEND=disabled`, token has no `policy_id` | compatibility: no policy enforcement |
| `POLICY_STORE_BACKEND=disabled`, token has `policy_id` | deny / fail-close |

Rationale: a token referencing a policy that cannot be found or evaluated must not become unrestricted.

## 8. Separation of access layers

Keep these layers distinct:

### Resource-level access

Defined by token metadata and owner isolation spec:

```text
permissions
allowed_resources
MCP_EMPTY_ALLOWED_RESOURCES_POLICY
OwnershipProvider
```

This answers:

```text
Can this token access this business resource?
```

### Tool-level access

Defined by PolicyStore:

```text
allowed_tools
denied_tools
```

This answers:

```text
Can this token call this MCP tool?
```

### Path-level access

Defined by PolicyStore for tools that manipulate paths:

```text
allowed_paths
path_rules
```

This answers:

```text
Can this token access this path for this action?
```

## 9. Enforcement order

Recommended enforcement order:

```text
1. Authenticate token
2. Check general permissions (read/write/admin)
3. Resource-level access
   - allowed_resources
   - wildcard '*'
   - MCP_EMPTY_ALLOWED_RESOURCES_POLICY=all|owner|deny
   - OwnershipProvider if needed
4. PolicyStore tool-level access
   - denied_tools
   - allowed_tools
5. PolicyStore path-level access, only when a path exists
   - denied path_rules
   - allowed path_rules / allowed_paths
6. Execute tool
```

Admin tokens may bypass resource-level restrictions. Whether admin bypasses PolicyStore should be explicit:

Recommended V1:

```text
admin permission bypasses resource-level restrictions and PolicyStore restrictions for service administration
```

A future strict mode could constrain admin tokens too, but that is out of scope for V1 and must be explicit.

## 10. Tool-level rules

### Pattern matching

Tool rules should support simple glob-style patterns:

```text
system_*
search_*
get_document
legifrance_* 
```

Implementation should use a well-defined matcher such as Python `fnmatch.fnmatchcase`.

### `denied_tools` priority

`denied_tools` must always win over `allowed_tools`.

Example:

```json
{
  "allowed_tools": ["legifrance_*"],
  "denied_tools": ["legifrance_admin_*"]
}
```

Result:

```text
legifrance_search              -> allowed
legifrance_admin_refresh_index -> denied
```

### Empty `allowed_tools`

Recommended compatibility semantics:

| `allowed_tools` | Meaning |
|---|---|
| absent/null | no allowlist restriction |
| `[]` | no allowlist restriction |
| non-empty | tool must match at least one allow pattern |

`denied_tools` still applies regardless.

Sensitive templates may use explicit allowlists.

## 11. System tools

System tools need explicit treatment.

First distinguish HTTP health endpoints from MCP system tools:

| Endpoint/tool | PolicyStore relevance |
|---|---|
| HTTP `GET /health`, `/healthz`, `/ready` | public infrastructure health endpoints for WAF/Docker/load balancers; not governed by PolicyStore |
| MCP tool `system_health` | MCP tool; may be governed by token/policy depending on service choice |
| MCP tool `system_about` | MCP tool; may be governed by token/policy depending on service choice |
| MCP tool `system_whoami` | MCP tool; should require authentication |

MCP system tool examples:

```text
system_health
system_about
system_whoami
```

Recommended defaults:

| Tool | Recommended default |
|---|---|
| `system_health` | may be public or auth-light depending on MCP deployment |
| `system_about` | may be public or auth-light depending on MCP deployment |
| `system_whoami` | requires authentication |

Policy behavior:

- If a token has no `policy_id`, preserve historical behavior.
- If a token has a `policy_id`, policy may restrict `system_*` tools.
- Starter-kit example policies should include required diagnostic tools unless deliberately restricted.

This avoids breaking diagnostics unintentionally while still allowing sensitive MCPs to lock down system tools.

## 12. Path-level rules

`path_rules` are only relevant for tools that manipulate path-like resources, for example:

```text
vault path
S3 object key
document path
memory path
datasource path
filesystem-like namespace
```

Tools without a path argument should not be affected by path rules.

Recommended helper shape:

```python
def check_path_policy(
    policy: dict,
    tool_name: str,
    path: str | None = None,
    action: str = "read",
) -> dict | None:
    ...
```

If `path is None`, path-level checks should return `None` and defer to tool/resource checks.

### `allowed_paths`

`allowed_paths` is a simple V1/backward-compatible shortcut for common allowlist cases:

```json
{
  "allowed_paths": ["public/*", "shared/docs/*"]
}
```

Recommended relationship with `path_rules`:

- `allowed_paths` is equivalent to simple allow rules for `read` access;
- `path_rules` is the expressive/authoritative model when non-empty;
- deny rules from `path_rules` always win;
- if `path_rules` contains allow rules, those allow rules define the allowed path/action set;
- if `path_rules` is empty, `allowed_paths` can be used as the simple allowlist.

### `path_rules`

`path_rules` can express allow/deny and actions:

```json
{
  "path_rules": [
    {"effect": "deny", "pattern": "private/*", "actions": ["read", "write"]},
    {"effect": "allow", "pattern": "public/*", "actions": ["read"]}
  ]
}
```

Valid `effect` values:

```text
allow
deny
```

Recommended V1 `actions` values:

```text
read
write
delete
admin
```

`actions` should be required in V1. A missing, empty, or invalid `actions` field should make the rule invalid, and an invalid rule should make the policy fail closed when used.

Recommended V1 semantics:

1. matching deny rule wins;
2. if `path_rules` contains allow rules, at least one allow rule must match;
3. if `path_rules` is non-empty, it is authoritative for path-level decisions;
4. if `path_rules` is empty and `allowed_paths` is non-empty, `allowed_paths` acts as simple read allowlist;
5. if no path rules and no allowed paths exist, no path-level restriction is applied;
6. invalid rule format should fail closed when the policy is used.

## 13. PolicyStoreProtocol

Recommended protocol:

```python
from typing import Protocol

class PolicyStoreProtocol(Protocol):
    def load(self) -> None: ...
    def get(self, policy_id: str) -> dict | None: ...
    def list_all(self) -> list[dict]: ...
    def create(self, policy: dict) -> dict: ...
    def update(self, policy_id: str, patch: dict) -> dict: ...
    def delete(self, policy_id: str) -> bool: ...
    def count(self) -> int: ...
```

Recommended behavior:

- `load()` populates an in-memory TTL cache.
- `get(policy_id)` returns enabled/disabled policy metadata but does not decide access by itself.
- `create/update` validate schema before saving.
- `delete(policy_id)` should be used carefully; disabling may be safer than deletion.

## 14. S3PolicyStore

Recommended S3 path:

```text
_system/policies.json
```

Recommended payload:

```json
{
  "policies": []
}
```

Compatibility note: existing starter-kit and MCPs may already reserve `_system/policies.json`. Preserve this convention.

S3 requirements:

- same Cloud Temple / Dell ECS compatibility as `S3TokenStore`;
- path-style addressing and SigV2/SigV4 configuration inherited from S3 settings;
- missing object means empty policy store;
- permission errors fail closed if a token references a policy.

## 15. VaultPolicyStore

Recommended Vault path:

```text
token-store/policies.json
```

Recommended payload:

```json
{
  "policies": []
}
```

MCP Vault API shape should mirror `VaultTokenStore` usage:

```text
GET  /admin/api/vaults/{vault_id}/secrets/{encoded_path}
POST /admin/api/vaults/{vault_id}/secrets
```

The implementation must tolerate MCP Vault metadata in `data`:

```json
{
  "_type": "custom",
  "_tags": "",
  "_favorite": "false",
  "policies": []
}
```

Missing secret behavior:

| Case | Result |
|---|---|
| no token references policy | empty store acceptable |
| token references policy but store missing | deny / fail-close |
| 401/403 | deny / fail-close |
| timeout/5xx | deny / fail-close |

## 16. Admin API

Proposed admin REST endpoints:

```text
GET    /admin/api/policies
POST   /admin/api/policies
GET    /admin/api/policies/{policy_id}
PUT    /admin/api/policies/{policy_id}
DELETE /admin/api/policies/{policy_id}
```

Recommended response constraints:

- never expose raw tokens;
- expose policy metadata and rules;
- show whether a policy is enabled;
- warn if policies are referenced by tokens before deletion;
- prefer disabling over deletion when tokens reference the policy.

Token list responses should expose policy status when possible:

```json
{
  "policy_id": "readonly",
  "policy_status": "none|enabled|missing|disabled"
}
```

Admin health should expose non-sensitive PolicyStore status:

```json
{
  "policy_store": {
    "backend": "vault",
    "configured": true,
    "loaded": true,
    "policies_count": 3,
    "cache_ttl": 300,
    "path": "token-store/policies.json"
  }
}
```

## 17. CLI and shell

CLI and shell should use admin REST as the source of truth, consistent with token management.

Proposed commands:

```text
policy list
policy get <policy_id>
policy create
policy update <policy_id>
policy disable <policy_id>
policy delete <policy_id>
policy validate <file>
```

The interactive shell should expose equivalent commands and display:

- policy id;
- enabled status;
- allowed tools;
- denied tools;
- path rules summary;
- tokens referencing the policy if available.

## 18. `/admin` UI

The admin UI should provide:

- policy list;
- create/edit policy form;
- enable/disable policy;
- delete with warning;
- token list display showing `policy_id` and `policy_status`;
- effective policy status:
  - `none` — no policy_id;
  - `enabled` — policy found and enabled;
  - `missing` — policy_id references missing policy;
  - `disabled` — policy found but disabled.

For tokens with empty `allowed_resources`, UI should also display the resource-level behavior from `MCP_EMPTY_ALLOWED_RESOURCES_POLICY`, as specified in `owner-based-isolation.md`.

## 19. Enforcement helper design

Recommended high-level helpers:

```python
def check_tool_policy(tool_name: str, token_info: dict) -> dict | None:
    ...


def check_path_policy(tool_name: str, path: str | None, action: str, token_info: dict) -> dict | None:
    ...


def check_policy(tool_name: str, token_info: dict, path: str | None = None, action: str = "read") -> dict | None:
    ...
```

Recommended `check_policy` semantics:

1. If token has no `policy_id`, return `None` in compatibility mode.
2. Load policy by `policy_id`.
3. Missing/disabled/unavailable policy returns error.
4. Apply denied tools first.
5. Apply allowed tools if non-empty.
6. Apply path-level rules only if `path` is provided.

## 20. Error behavior

Recommended fail-close cases:

| Case | Result |
|---|---|
| token references missing policy | deny |
| token references disabled policy | deny |
| PolicyStore unavailable while policy is required | deny |
| invalid policy schema | deny for that policy |
| invalid path rule schema | deny for that policy/path check |

Recommended compatibility cases:

| Case | Result |
|---|---|
| token has no `policy_id` | no policy-level restriction by default |
| PolicyStore disabled and token has no `policy_id` | allow policy layer |
| PolicyStore disabled and token has `policy_id` | deny / fail-close |
| path is absent and only path_rules exist | no path-level denial |

## 21. Tests required before implementation merge

### Policy parsing and validation

- valid policy accepted;
- duplicate policy ids rejected;
- invalid pattern type rejected;
- invalid path rule rejected;
- disabled policy parsed but not allowed for enforcement.

### Tool-level enforcement

- no `policy_id` compatibility behavior;
- `policy_id` missing -> deny;
- disabled policy -> deny;
- `denied_tools` wins over `allowed_tools`;
- allowed tool matched by exact name;
- allowed tool matched by glob;
- non-matching allowed_tools -> deny;
- empty allowed_tools + no denied match -> allow.

### Path-level enforcement

- no path -> no path-level denial;
- allowed_paths match -> allow;
- allowed_paths non-match -> deny when allowed_paths configured;
- deny path_rule wins;
- allow path_rule matches action;
- action mismatch behavior explicit;
- invalid path rule fail-close.

### Resource-level interaction

- resource-level denial happens before policy allow;
- policy denial happens even if resource-level allowed;
- `MCP_EMPTY_ALLOWED_RESOURCES_POLICY` remains resource-level only;
- owner-based provider is not duplicated in PolicyStore.

### Store backends

- S3 missing `_system/policies.json` -> empty store;
- S3 permission denied + required policy -> fail-close;
- Vault missing `token-store/policies.json` + required policy -> fail-close;
- Vault response with metadata is parsed;
- create/update/delete/list for both backends;
- cache TTL refresh behavior.

### Admin / CLI / UI

- admin policy CRUD;
- CLI policy list/create/update/disable;
- shell equivalent commands;
- admin health status without secrets;
- token list shows policy status;
- deletion warning when tokens reference policy.

### E2E

- Docker Compose MinIO with S3PolicyStore;
- Docker Compose fake Vault with VaultPolicyStore;
- create policy;
- create token referencing policy;
- allowed tool succeeds;
- denied tool fails;
- missing policy fails closed.

## 22. Migration and compatibility

### Existing tokens

Existing tokens without `policy_id` should continue working by default.

### Existing S3 stores

If `_system/policies.json` does not exist:

- no token references policy -> compatible;
- token references policy -> fail-close for that token.

### S3 to Vault migration

Future migration should support:

```text
export S3 policies
import Vault policies
verify ids
switch POLICY_STORE_BACKEND
rollback if needed
```

Do not remove S3 policy support until VaultPolicyStore is proven.

## 23. Security rules

- Never log raw client tokens.
- Never expose Vault application tokens.
- Never expose full token store contents in health endpoints.
- Policy contents are not secrets by default, but may reveal internal tool/path names; avoid public exposure.
- Missing referenced policy must fail closed.
- Disabled policy must fail closed.
- Deny rules must win over allow rules.
- Invalid policy syntax must not fail open.

## 24. Recommended implementation phases

### Phase 1 — schema and enforcement helpers

- define Policy model/schema;
- implement matcher semantics;
- implement `check_tool_policy` and `check_path_policy` with unit tests;
- no admin UI yet.

### Phase 2 — PolicyStoreProtocol + S3PolicyStore

- implement store abstraction;
- add S3 backend with `_system/policies.json`;
- add admin API read/list;
- add tests.

### Phase 3 — VaultPolicyStore

- implement Vault backend with `token-store/policies.json`;
- add fake Vault coverage;
- add live/manual validation plan.

### Phase 4 — admin CRUD + CLI/shell/UI

- admin policy CRUD;
- CLI/shell commands;
- UI display/edit;
- deletion/disable warnings.

### Phase 5 — e2e and migration

- Docker Compose e2e for S3PolicyStore and VaultPolicyStore;
- migration helper specification or implementation;
- release notes.

## 25. Recommended decision

Implement a dedicated PolicyStore layer:

```text
PolicyStoreProtocol
├── S3PolicyStore
└── VaultPolicyStore
```

Keep it separate from TokenStore while allowing the same S3/Vault backend strategy.

Use `policy_id` as the link from token to policy.

Default compatibility behavior:

```text
token without policy_id -> no policy-level restriction
```

Fail-close behavior:

```text
token with policy_id but missing/disabled/unavailable policy -> deny
```

Tool-level policy:

```text
denied_tools always wins over allowed_tools
```

Path-level policy applies only when a tool exposes a meaningful path.

Do not mix PolicyStore implementation with owner-based isolation implementation. PolicyStore consumes the result of resource-level access; it does not redefine ownership.
