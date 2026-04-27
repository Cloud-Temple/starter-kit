# Owner-based isolation and `allowed_resources` semantics

> Status: specification / no runtime change in this document  
> Scope: MCP starter-kit access model before full PolicyStore  
> Related work items: WI-016, WI-015

## 1. Problem

The starter-kit currently exposes token metadata such as:

```json
{
  "permissions": ["read", "write"],
  "allowed_resources": [],
  "policy_id": "readonly"
}
```

`allowed_resources=[]` is ambiguous:

- it can mean "all resources" in simple services;
- it can mean "no explicit resource grants";
- it can mean "use owner-based isolation" in multi-tenant services;
- it can mean "deny resource-level access unless explicit grants exist" in sensitive services.

A starter-kit must not silently teach unsafe semantics, but it also must not break existing MCPs.

This document specifies a future-compatible model. It intentionally does **not** implement a PolicyStore or tool-level policy enforcement.

## 2. Observed reference behavior: MCP Vault

In MCP Vault live patterns, the useful model is:

```text
admin token                         -> global access
allowed_resources non-empty          -> access to listed resources
allowed_resources empty, non-admin    -> owner-based isolation when resources have an owner
```

That model works because MCP Vault has a clear business ownership concept, for example:

```text
vault.created_by == current client_name
```

The generic starter-kit cannot assume that every future MCP has such a resource owner model. It must provide a hook, not magic.

## 3. Goals

- Clarify `allowed_resources=[]` semantics.
- Preserve compatibility for existing/simple MCPs.
- Provide a safe model for new multi-tenant MCPs.
- Make global access explicit with `allowed_resources=["*"]`.
- Define an ownership extension point.
- Ensure owner mode fails closed when no ownership provider is configured.
- Avoid blocking tools that have no business `resource_id`.
- Keep `policy_id` as metadata only until a real PolicyStore exists.

## 4. Non-goals

This specification does **not** define or implement:

- full PolicyStore;
- `allowed_tools`;
- `denied_tools`;
- `path_rules`;
- policy enforcement;
- data-model-specific ownership logic;
- migration scripts.

## 5. Proposed configuration

Introduce a configuration variable:

```env
MCP_EMPTY_ALLOWED_RESOURCES_POLICY=all|owner|deny
```

Meaning:

| Value | Meaning when `allowed_resources=[]` and a `resource_id` is checked |
|---|---|
| `all` | allow access for compatibility/simple public-resource MCPs |
| `owner` | allow only if the configured `OwnershipProvider` confirms ownership |
| `deny` | deny resource-level access unless explicit resources are listed |

Recommended usage:

| Scenario | Recommended value |
|---|---|
| Legacy/existing MCP compatibility | `all` |
| New multi-tenant MCP with ownership model | `owner` |
| Sensitive MCP / least privilege | `deny` or explicit `allowed_resources` |
| Public read-only MCP with no per-client resources | `all` or no resource-level checks |

Important convention:

```json
"allowed_resources": ["*"]
```

means explicit global resource access.

An empty list should not be used to express global access in new MCPs. It should be interpreted according to `MCP_EMPTY_ALLOWED_RESOURCES_POLICY`.

## 6. Ownership provider contract

Owner-based isolation must be supplied by the business MCP.

Recommended contract:

```python
from typing import Protocol

class OwnershipProvider(Protocol):
    def is_owner(
        self,
        resource_id: str,
        client_name: str,
        token_info: dict,
    ) -> bool:
        ...
```

Default starter-kit behavior should be explicit. If owner mode is configured but no provider is registered, fail closed.

Example default implementation:

```python
class OwnershipNotSupported(RuntimeError):
    pass

class NoOwnershipProvider:
    def is_owner(self, resource_id: str, client_name: str, token_info: dict) -> bool:
        raise OwnershipNotSupported(
            "Owner-based isolation requested but no ownership provider configured"
        )
```

## 7. `check_access` contract

`check_access` must support tools with and without a business resource.

Recommended signature:

```python
def check_access(resource_id: str | None = None) -> dict | None:
    ...
```

### 7.1 Tools without `resource_id`

Examples:

```text
system_health
system_about
system_whoami
generate_password
```

For `resource_id=None`, no resource-level check is performed. Authentication, permission checks, and future tool-level policy checks may still apply elsewhere.

### 7.2 Tools with `resource_id`

For tools that operate on a business resource, call:

```python
access_err = check_access(resource_id="resource-a")
if access_err:
    return access_err
```

### 7.3 Recommended algorithm

```python
def check_access(resource_id: str | None = None) -> dict | None:
    token_info = current_token_info.get()

    if token_info is None:
        return {"status": "error", "message": "Authentication required"}

    permissions = token_info.get("permissions", [])

    if "admin" in permissions:
        return None

    if resource_id is None:
        return None

    allowed = token_info.get("allowed_resources", []) or []

    if "*" in allowed:
        return None

    if resource_id in allowed:
        return None

    if allowed:
        return {
            "status": "error",
            "message": f"Access denied to {resource_id}",
        }

    mode = settings.mcp_empty_allowed_resources_policy

    if mode == "all":
        return None

    if mode == "deny":
        return {
            "status": "error",
            "message": f"Access denied to {resource_id}: no allowed_resources",
        }

    if mode == "owner":
        if ownership_provider is None:
            return {
                "status": "error",
                "message": "Owner-based isolation requested but no ownership provider configured",
            }

        if ownership_provider.is_owner(
            resource_id,
            token_info.get("client_name", ""),
            token_info,
        ):
            return None

        return {
            "status": "error",
            "message": f"Access denied to {resource_id}: not owner",
        }

    return {
        "status": "error",
        "message": f"Invalid MCP_EMPTY_ALLOWED_RESOURCES_POLICY={mode}",
    }
```

## 8. Effective resource-scope rules

| Case | Expected result |
|---|---|
| admin + any resource | allow |
| `resource_id=None` | no resource-level check |
| `allowed_resources=["*"]` | allow |
| `resource_id` in `allowed_resources` | allow |
| `allowed_resources` non-empty but no match | deny |
| `allowed_resources=[]` + mode `all` | allow |
| `allowed_resources=[]` + mode `deny` | deny |
| `allowed_resources=[]` + mode `owner` + owner true | allow |
| `allowed_resources=[]` + mode `owner` + owner false | deny |
| `allowed_resources=[]` + mode `owner` + no provider | deny / fail-close |
| invalid mode | deny / fail-close |

## 9. Admin UI / CLI display

A token with:

```json
"allowed_resources": []
```

should not be displayed as simply "all" or "none".

Recommended display:

```text
Resource scope: empty
Effective behavior: all|owner|deny
Configured by: MCP_EMPTY_ALLOWED_RESOURCES_POLICY
```

If the token has:

```json
"allowed_resources": ["*"]
```

recommended display:

```text
Resource scope: global (*)
```

This avoids admin misunderstanding.

## 10. Examples by MCP type

### MCP Vault

Ownership can be implemented as:

```text
vault.created_by == token_info.client_name
```

Recommended mode:

```env
MCP_EMPTY_ALLOWED_RESOURCES_POLICY=owner
```

### Public read-only MCP, e.g. public legal data

If resources are public and not tenant-owned, owner isolation may not apply.

Possible mode:

```env
MCP_EMPTY_ALLOWED_RESOURCES_POLICY=all
```

or avoid resource-level checks for tools that do not expose client-specific resources.

### Sensitive multi-tenant MCP

Recommended mode:

```env
MCP_EMPTY_ALLOWED_RESOURCES_POLICY=owner
```

with a real `OwnershipProvider`, or:

```env
MCP_EMPTY_ALLOWED_RESOURCES_POLICY=deny
```

with explicit `allowed_resources` grants.

## 11. Tests to implement when coding

Resource access tests:

| Test | Expected |
|---|---|
| admin bypass | allow |
| explicit resource allowed | allow |
| wildcard `*` | allow |
| explicit non-matching resource | deny |
| empty + mode `all` | allow |
| empty + mode `deny` | deny |
| empty + mode `owner`, provider true | allow |
| empty + mode `owner`, provider false | deny |
| empty + mode `owner`, no provider | deny / fail-close |
| tool without `resource_id` | no resource-level denial |
| invalid mode | deny / fail-close |

Admin/API display tests:

- token with empty `allowed_resources` exposes effective behavior;
- token with `allowed_resources=["*"]` displays global access explicitly;
- no secrets or raw tokens are exposed.

## 12. Migration notes

Existing MCPs should not be broken by default. For a migration release, `all` can be kept as runtime compatibility default if needed.

However, new generated templates and documentation should strongly recommend:

- `owner` for multi-tenant MCPs with a real ownership model;
- `deny` or explicit resources for sensitive MCPs;
- `allowed_resources=["*"]` for intentional global access.

## 13. Recommended implementation phases

### Phase 1 — config + access helper

- add `mcp_empty_allowed_resources_policy` setting;
- add ownership provider interface and registration hook;
- update `check_access(resource_id: str | None = None)`;
- add unit tests.

### Phase 2 — admin visibility

- expose effective resource policy in admin health/token list;
- update admin UI and CLI display.

### Phase 3 — integrate with future PolicyStore

- combine resource-level access with `allowed_tools`, `denied_tools`, and `path_rules`;
- keep ownership logic separate from policy metadata.

## 14. Recommended decision

Use a configurable model:

```env
MCP_EMPTY_ALLOWED_RESOURCES_POLICY=all|owner|deny
```

Use explicit global access:

```json
"allowed_resources": ["*"]
```

For new multi-tenant MCPs, prefer:

```env
MCP_EMPTY_ALLOWED_RESOURCES_POLICY=owner
```

provided that a business `OwnershipProvider` exists. If owner mode is configured without a provider, fail closed.

## 15. Implementation guardrails

Before coding this specification, keep these guardrails explicit:

1. Do not mix this work with the future full PolicyStore.
   - `check_access(resource_id)` remains resource-level access control.
   - `allowed_tools`, `denied_tools`, and `path_rules` belong to `PolicyStore`.

2. Use one configuration name only:

```env
MCP_EMPTY_ALLOWED_RESOURCES_POLICY
```

Do not introduce competing aliases such as `TOKEN_RESOURCE_EMPTY_POLICY` or `MCP_DEFAULT_RESOURCE_SCOPE`.

3. Document defaults clearly:

```text
default compatibility: all
recommended for multi-tenant MCP: owner
recommended for sensitive MCP: deny
```

4. Expose the effective behavior in APIs before or during implementation, at minimum in:

```text
/admin/api/health
/admin/api/whoami
system_whoami
token list responses
```

5. Do not merge runtime changes without resource-level tests for:

```text
admin bypass
explicit resource allowed
wildcard *
explicit non-matching resource denied
empty + all
empty + deny
empty + owner provider true
empty + owner provider false
empty + owner no provider fail-close
resource_id None
invalid mode fail-close
```

## 16. Specification status

This specification is validated as cadrage for implementation.

It closes the design ambiguity around `allowed_resources=[]` and provides the required hook for owner-based isolation.

It does not authorize or imply implementation of full PolicyStore in the same change.
