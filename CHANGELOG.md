# Changelog

## Unreleased

### Changed

- Generalized the `boilerplate/DESIGN/AGENTIC_RULES/WORKFLOW_GIT_EPIC.md`
  template by removing Terraform-provider-specific wording and strengthening
  EPIC -> child issue -> PR traceability rules.
- Added `boilerplate/DESIGN/AGENTIC_RULES/MAIN_RULES.md` as the English
  entrypoint for generated project rules.
- Virtualized generated-project rule paths with `{AGENTIC_RULES_DIR}` so
  projects can relocate the rule directory without rewriting rule semantics.
- Replaced remaining provider-specific references in the generated project
  Git and memory rule templates with project-generic wording, and renamed the
  workspace memory template to remove tool-specific naming.

---

## v1.2.0 — 2026-06-21 — Mission token PEP baseline

This release adds the reusable `AuthMissionJWTMiddleware` for mcp-mission
`mission_token` validation and closes the integration gap with the starter-kit
auth context.

---

## Added

- Optional `AuthMissionJWTMiddleware` in the ASGI stack when
  `STARTER_KIT_AUTH_MODE=jwt|dual-stack`.
- ES256/P-256 `mission_token` validation via dynamic mcp-mission JWKS.
- JWKS cache with TTL, ETag/304 revalidation, exponential backoff, and
  fail-close behavior when the cache is expired and the JWKS cannot be fetched.
- Admin endpoint `POST /admin/auth/jwks/reload` for immediate JWKS refresh.
- Agentic project-rule templates under `boilerplate/DESIGN/AGENTIC_RULES/`,
  covering Live Memory, Graph Memory, adversarial engineering reviews, GitHub
  issue/PR workflow, EPIC/RC flow, and human gates for generated projects.
- Mission authentication context:
  - `request.scope["mission_context"]`,
  - `current_mission_context`,
  - `current_token_info` bridge with `auth_type=mission_token`.
- Documentation for mission-token operation, ASGI integration, and how generated
  projects should connect their agent rules to Cloud Temple Live Memory and
  Graph Memory.

---

## Fixed

- Mission tokens validated by `AuthMissionJWTMiddleware` now reach existing MCP
  tools as an authenticated request instead of leaving `current_token_info=None`
  after passing through `AuthMiddleware`.
- `jti` and `scope` are now required and type-checked, aligning the implementation
  with the acceptance contract from Cloud-Temple/starter-kit#14.

---

## Security

- `mission_token` identities never receive `admin` permission through the
  compatibility bridge, and `scope` is exposed as `mission_scope` rather than
  converted into legacy `allowed_resources`.
- Resource-level `check_access(resource_id)` fails closed for `mission_token`
  identities unless a local policy explicitly maps the resource.
- `check_write_permission()` also fails closed for `mission_token`; mission
  `scope` is not treated as a legacy write permission.
- Invalid, missing, or malformed `jti` / `scope` claims fail closed with `401`.

---

## v1.1.0 — Starter-kit industrialization baseline

This release consolidates the Cloud Temple MCP starter-kit after the v1.0.0 baseline.
It introduces a stronger testing foundation, fixes token administration consistency,
adds multi-company branding, improves Cloud Temple S3 compatibility, and adds an
optional MCP Vault Token Store backend.

---

## Added

### CI and tests

- GitHub Actions CI workflow.
- Unit/contract tests for CLI and interactive shell token administration.
- ASGI integration tests for `/admin/api/tokens`.
- Docker Compose e2e stack with:
  - WAF Caddy/Coraza,
  - MCP server,
  - MinIO S3-compatible backend.
- E2E test covering:
  - create token via `/admin/api/tokens`,
  - persist token store to S3-compatible MinIO,
  - call `/mcp` with created token,
  - revoke token,
  - verify revoked token is refused.
- Manual real S3 TokenStore integration test for Cloud Temple / Dell ECS.
- VaultTokenStore tests with:
  - mocked HTTP calls,
  - local fake MCP Vault HTTP server,
  - config validation tests.

### Multi-company branding

- `MCP_BRAND` setting with supported values:
  - `ct` — Cloud Temple,
  - `dgy` — Dragonfly,
  - `isec` — Intrinsec.
- Dynamic admin UI branding via:
  - `GET /admin/api/brand`,
  - dynamic logo,
  - dynamic accent colors,
  - dynamic document title.
- Brand assets:
  - `logo-ct.svg`,
  - `logo-dgy.svg`,
  - `logo-isec.svg`.

### Token Store backend selection

- `TOKEN_STORE_BACKEND=s3|vault`.
- Explicit `S3TokenStore` class while keeping `TokenStore = S3TokenStore` alias for compatibility.
- `VaultTokenStore` V1 using one MCP Vault JSON secret:

  ```text
  token-store/tokens.json
  ```

- Vault configuration variables:

  ```env
  MCP_VAULT_URL=https://vault.mcp.cloud-temple.app
  MCP_VAULT_TOKEN_FILE=
  MCP_VAULT_TOKEN=
  MCP_VAULT_ID=
  MCP_VAULT_TOKEN_STORE_PATH=token-store/tokens.json
  MCP_VAULT_TIMEOUT=5
  ```

- Vault application token priority:

  ```text
  MCP_VAULT_TOKEN_FILE > MCP_VAULT_TOKEN
  ```

- Non-sensitive token store status in `/admin/api/health`.

---

## Changed

### Hybrid admin architecture

Token administration now follows the validated hybrid architecture:

```text
/mcp         -> business tools + safe system tools
/admin/api/* -> server administration
```

CLI and interactive shell token commands now use REST admin endpoints instead of
calling a non-existent MCP tool named `token`.

Token commands now use:

- `POST /admin/api/tokens`
- `GET /admin/api/tokens`
- `PUT /admin/api/tokens/{hash_prefix}`
- `DELETE /admin/api/tokens/{hash_prefix}`

### CLI / shell token options

- Added `token update`.
- Added generic `--resources` option.
- Kept `--vaults` as an alias for compatibility with MCP Vault terminology.
- Normalized payloads:
  - `expires_in_days`,
  - `permissions` as list,
  - `allowed_resources`.

### S3 compatibility

Cloud Temple / Dell ECS compatibility is now the default for object data operations:

```env
S3_SIGNATURE_VERSION=s3
S3_ADDRESSING_STYLE=path
```

This is required for real Cloud Temple / Dell ECS object operations (`GET`, `PUT`, `DELETE`).

---

## Fixed

- Fixed CLI/shell token management calling a non-existent MCP tool `token`.
- Fixed WAF blocking legitimate admin REST verbs, especially:

  ```text
  DELETE /admin/api/tokens/{hash_prefix}
  ```

- Fixed bootstrap key default warning mismatch:
  - `.env.example` uses `change_me_in_production`,
  - `server.py` now checks the same value.
- Fixed Cloud Temple S3 `XAmzContentSHA256Mismatch` by configuring SigV2/path-style for `S3TokenStore`.

---

## Security

- Token administration remains under `/admin/api/*`, not exposed as MCP tools.
- `/mcp` remains dedicated to business/system tools and can keep its WAF bypass for Streamable HTTP.
- Health output does not expose:
  - raw client MCP tokens,
  - `MCP_VAULT_TOKEN`,
  - `S3_SECRET_ACCESS_KEY`,
  - token store contents.
- `VaultTokenStore` follows fail-close behavior:
  - 404 -> empty store,
  - 401/403 -> permission error,
  - 5xx/timeout -> Vault unavailable,
  - bootstrap key remains local emergency/admin path.
- Real S3 tests are not run on GitHub-hosted runners because the Cloud Temple bucket uses strict IP whitelisting.

---

## Testing strategy

Default GitHub-hosted CI:

```text
unit/integration tests + e2e MinIO
```

Real Cloud Temple S3 validation:

```text
manual from a whitelisted IP
or future self-hosted runner with whitelisted fixed egress IP
```

Validated results during v1.1 work:

- GitHub Actions default CI: passing.
- Local Docker Compose MinIO e2e: passing.
- Real Cloud Temple S3 TokenStore test from whitelisted IP: passing.
- VaultTokenStore V1: tested with mock HTTP and local fake MCP Vault HTTP server.

---

## Operational notes

### MinIO vs real S3

MinIO is used in default CI as a reproducible, secretless S3-compatible backend.
It validates functional non-regression but does not replace real Cloud Temple / Dell ECS certification.

### Real S3

The real S3 validation uses a dedicated starter-kit real S3 test bucket.

Credentials are stored in MCP Vault and GitHub environment secrets, not in git.

Because the dedicated test bucket uses a custom access policy with IP whitelisting, GitHub-hosted runners receive `AccessDenied`.
Real S3 validation must therefore be manual from a whitelisted IP or use a future self-hosted runner.

### VaultTokenStore V1

`VaultTokenStore` stores tokens in one JSON secret.
This is intentionally close to the S3 token store format.

This release does **not** implement full PolicyStore enforcement.
`policy_id` is stored as token metadata only.

---

## Out of scope / future work

- Owner-based isolation hook / default behavior.
- Full PolicyStore:
  - `allowed_tools`,
  - `denied_tools`,
  - `path_rules`,
  - enforcement in tools/admin routes.
- Live MCP Vault validation in a controlled environment.
- Self-hosted runner for real S3 validation.
- Automatic migration from S3 token store to Vault token store.
