# Changelog

## v2.0.8 — 2026-09-17 — The real-S3 layer that never ran

### Fixed

- **`tests/integration/` was green because it never executed.** The one test marked
  `real_s3` was collected by default CI and skipped there on every run, for want of
  `RUN_REAL_S3=1`. No workflow set it, and `tests/README.md` described a nightly
  workflow that did not exist. The layer meant to validate compatibility with the
  real Cloud Temple / Dell ECS endpoint had validated nothing, and a skip is green.
  The new `Real S3 (Dell ECS)` workflow runs it on manual dispatch against the
  `nightly-real-s3` environment, whose secrets were already provisioned. It also
  fails when a `real_s3` test skips, so an incomplete environment can no longer
  pass for a successful run.

### Added

- **`tests/integration/test_ecriture_conditionnelle_ecs.py`.** Does the real ECS
  honour `If-Match` and `If-None-Match`, and does it honour them atomically? The
  question has blocked the token store fix on `starter-kit#28`, `mcp-office#4` and
  `mcp-teleport#146` since June, and `RFC 0001` in `agentic-platform` has carried it
  as undecided since 2026-07-08.

  It is sharper than it looks. Cloud Temple requires SigV2 for object data
  operations, and `HmacV1Auth` signs only `content-md5`, `content-type`, `date` and
  the `x-amz-*` headers. `If-Match` is not covered. A backend that does not
  implement it returns no error at all: it ignores the header, the PUT succeeds, and
  the caller believes it holds a lock it never had. The fix would reintroduce the
  very failure it closes.

  So the tests require rather than observe, and a failure is the answer. A correct
  `If-Match` must be accepted, without which a server refusing everything would look
  like a server protecting something. A deliberately wrong one must be refused with
  412, which is the case that decides. `If-None-Match: *` is tested separately
  because it is a distinct primitive, and the one an object-per-token layout would
  need. Both signature versions are covered.

  A 412 in sequence does not prove atomicity: a server may compare then write in two
  steps and lose the race in between. The concurrency test puts writers behind a
  barrier so they all issue their PUT from the same ETag, where exactly one must win.
  Without the barrier, measured against MinIO with 8 writers over 50 rounds, 277
  writes out of 400 succeeded with no loss at all, because the slow writer read
  after another's PUT and never contended. A compare-and-swap breaking one time in
  a thousand would have passed. The file's own defaults are smaller, 8 writers over
  25 rounds, to fit a manual run; `SONDE_ECRIVAINS` and `SONDE_RONDES` raise them.

- **`scripts/mutations_ecriture_conditionnelle.py`.** Removing a conditional header
  reproduces exactly what an ignoring server does, so the matching test must fall.
  Three mutations out of three are detected against MinIO, each target first run
  unmutated so that a broken environment cannot pass for three kills. The two
  control tests carry no mutation and the harness says why: their subject is a server that refuses
  conditions outright, a failure that cannot be produced by mutating the client. An
  anchor that no longer matches fails loudly rather than reading as a detection.

### Changed

- **The `Real S3 (Dell ECS)` workflow now runs only the conditional-write tests by
  default.** They write under a `_sonde/<uuid>/` prefix and delete it afterwards.
  `test_real_s3_tokenstore.py` writes to the live `_system/tokens.json` key, backs it
  up and restores it, and that backup-and-restore code has never executed once. A
  first run against the real bucket should not be the occasion to find out whether it
  works. The `selection` input includes it once access is established. The input
  reaches pytest through the environment rather than the command line, since a
  `workflow_dispatch` input written straight into a `run` block is a command
  injection.

### Also found

- **Listing fails under SigV2.** The fixture's own cleanup surfaced it on its first
  run: `list_objects_v2` and `list_objects` both return `SignatureDoesNotMatch`
  against MinIO when the client signs with SigV2, which is the fleet default for
  object data operations. The fleet already works around this by keeping a separate
  SigV4 client for listing (`mcp_tools/auth/token_store.py`,
  `mcp_office/scripts/utils/sync_assets.py`); the fixture now does the same, while
  the tests themselves stay on the signature under test. Before the cleanup was made
  to warn, this failure was swallowed and left probe objects behind on every SigV2
  run.

### Notes

- The real bucket's IP whitelist was the documented reason for not wiring this to
  GitHub-hosted CI. It has never been observed, because the test never ran. The
  workflow makes it measurable, and its `executant` input moves the run to a
  self-hosted runner with a fixed egress IP once one exists.

## v2.0.7 — 2026-09-15 — What the counter-review found in v2.0.6

An adversarial review of the v2.0.6 backports turned up three defects. The first
is the one that stings: the suite claimed to cover a behaviour it never touched.

### Fixed

- **The middleware's `503` was never tested.** Removing the whole
  `except TokenStoreUnavailable` from `AuthMiddleware` left all 128 tests green,
  in every repository of the fleet. The CHANGELOG entries and pull request
  descriptions for v2.0.5 and v2.0.6 claimed otherwise. Six tests now cover the
  refusal itself: `503` and not `401`, the downstream app never runs, the store's
  message stays out of the body, `Retry-After` is set, a websocket is closed with
  1013, and the contextvar does not leak. Three more pin the behaviours around it
  that must not change: a public path never consults the store, anonymous traffic
  still passes, and a valid token still passes.
- **`TOKEN_STORE_CACHE_TTL=0` no longer serves the cache for five minutes.**
  A null TTL forced a reload, but when that reload failed `_guard_stale` still
  allowed the cache for `0 + TOKEN_STORE_STALE_GRACE`, which defaults to 300
  seconds. It handed back the revocation window the operator had just refused,
  through a second door, after v2.0.6 had closed the first. A null TTL now
  short-circuits the grace window. The test that should have caught this set
  `token_store_stale_grace=0` as well, so it exercised a configuration nobody
  writes.
- **A read in flight could erase a revocation that had already been written.**
  `load()` performs its `GET` outside the lock and only takes it to overwrite
  `_tokens`. A read that started before a revocation could return after it,
  reinject the unrevoked version and give it a fresh TTL, so the revoked token
  stayed valid on that instance for the length of the cache. `load()` now takes a
  copy of a mutation counter before its `GET` and discards a body that has become
  stale, marking the cache for revalidation.

### Fixed after the independent review

The reviewer refused the first version. It was right to.

- **The Vault backend never got the race fix.** The first version declared
  `_mutation_seq` in `VaultTokenStore.__init__` and never used it in `_load()`.
  The attribute existed only to stop `_serialise` raising `AttributeError`. Vault
  is a supported production backend, so it stayed open to the exact defect this
  work claims to close, with the appearance of a protection that was not there.
  Both backends now go through one shared helper, because two copies of the same
  invariant is how this defect happened in the first place.
- **`VaultTokenStore.load()` cleared `_needs_reload` unconditionally** after a
  successful `_load()`, erasing the flag the helper had just set when it
  discarded a stale body. Found by the new Vault race test, not by reading.
- **A test claimed to prove the `503` and proved nothing.** It was called "the
  health check stays green during an outage", but `/health` is in `PUBLIC_PATHS`,
  so the middleware returns before `_validate_token` and the fake outage could
  never fire. It stayed green with or without the fix. Rewritten around the
  property that is real and worth protecting: a public path never consults the
  store at all.
- **The race tests could hang CI instead of failing.** The call to `revoke()`
  had no bound, so a regression making the lock non-reentrant would have burned
  the GitHub default of six hours. The reader threads are now daemons and the
  bound is honoured. This is a partial answer, and the next section says why.
- **`TOKEN_STORE_CACHE_TTL=0` also closes the admin console** during an outage,
  because `_guard_stale` serves `list_all` as well as `get_by_hash`. Only the
  bootstrap key still gets in. The behaviour is consistent with fail-close and is
  kept, but it was undocumented. Now stated in the README and `.env.example`, and
  pinned by a test.

### Fixed after the second independent review

The second reviewer took the bound above apart. It was right too.

- **The bound was in the wrong place.** Making the reader threads daemons only
  covers threads the test file creates. The deadlock a non-reentrant lock
  produces is on the thread running pytest: `_serialise` takes `_lock`, the
  mutation calls `load()`, and `load()` takes it again through the shared
  helper. Measured, replacing `RLock` with `Lock` hangs
  `test_une_creation_dont_l_ecriture_echoue_n_expose_aucun_token`, which creates
  a token and touches no thread at all. The claim that both
  race tests fail in ten seconds was true in isolation and false for the suite.
  Two things fix it. A test now asserts the lock is reentrant, using
  `acquire(timeout=1)`, so the regression is a red in one second instead of a
  hang, and it is placed first in the file so it is the first thing that fails.
  And `pytest.ini` sets `timeout = 60` with `timeout_method = thread`, which
  prints every thread's stack and kills the process. `signal` was tried first
  and does not interrupt a blocked `acquire()` here; that was measured, not
  assumed. The three CI jobs also get `timeout-minutes: 20`, ten times their
  real duration, for what pytest cannot see: a `pip install` or a
  `docker compose` that never returns.
- **The Vault store still wiped its cache on a read failure.** Every error path
  in `_load()` replaced `_tokens` with `{}` and refreshed `_cache_time` before
  raising, so an outage presented itself as an empty store and the bounded stale
  window had nothing left to serve. It was listed as a known limitation and
  tracked in issue #30; it belongs here instead, because this release is the one
  that claims the store fails closed rather than lying. The five error paths now
  leave the cache alone, and the `404`, where the secret really is gone, still
  clears it. Six tests, one per path.
- **The mutation harness was producing numbers it had not measured.** Two
  separate flaws. It inserted a malformed line into the source, collection
  failed, pytest reported one error, and the counter read that as one test
  detecting the mutation. And it left stale bytecode behind: CPython validates a
  `.pyc` on the pair (mtime in whole seconds, size), so two mutations inserting
  the same line into two different branches produce files of identical size, and
  the second run reused the first one's bytecode. The same mutation measured 1
  red on one run and 2 on the next. The harness now parses the mutated source
  before running anything, purges `__pycache__`, and runs with `python -B`. Two
  consecutive measurements are now identical: 30 mutations, 53 tests, none
  undetected.

### Changed

- The documented limit was wrong. "The lock only serialises this process"
  suggested everything inside one process was safe. It serialises mutations
  against each other, nothing more. The docstring now says so, and names the
  generation counter as what protects reads.

### Known limitations, unchanged

- No conditional write on the store side, so two instances can still overwrite
  each other. See issue #28.


## v2.0.6 — 2026-09-15 — Two defects found while backporting v2.0.5

Both were found by porting the v2.0.5 fix to `mcp-office` and `mcp-agent`, and
both were in the boilerplate that every derived service copies.

### Fixed

- **`TOKEN_STORE_CACHE_TTL=0` is honoured.** It means "never serve from cache".
  The fallback `int(...) or 300` turned it silently into 300 seconds, handing an
  operator back the five-minute revocation window they had just refused. A `0`
  now stands; an unreadable value still falls back to the default. The S3 store,
  the Vault store and `get_token_store_status()` share one helper, so the three
  can no longer drift apart.
- **The admin API no longer returns the store's error message.** `handle_admin_api`
  put `str(e)` in the body of its `503`/`502` responses. The admin guard runs
  *inside* `_dispatch_admin_api`, so during an outage an unauthenticated caller
  presenting any bearer received the boto3 error, which names the internal S3
  endpoint and the bucket. The body is now generic; the detail stays on stderr.

### Fixed — findings from the independent review of the mcp-office backport

- **A failed write is ambiguous.** The store may have applied the request and
  lost its response on the way back. Undoing the mutation in memory while
  keeping the cache fresh made a revoked token valid again on that instance,
  for up to a full TTL, while the store already held it as dead. The rule is now
  to keep whichever state denies more: a refused creation or elevation is
  removed, a revocation is kept. Either way the cache is marked for
  revalidation, so the next read reloads instead of serving an uncertain state.
- **Mutations are serialised inside the process.** `create()`, `revoke()` and
  `update()` are read-modify-write. Two concurrent calls could interleave and
  lose one. A reentrant lock serialises them, and `load()` publishes its result
  under that same lock.

### Tests

- `tests/test_token_store_fail_close.py` grows to 30 cases. The mutation table in
  its docstring is re-measured on the new suite: twelve mutations of the fixed
  code, each caught by at least one test, none silent.

### Known limit

The store is a single JSON file rewritten whole, with no conditional write. Two
**instances** can read the same state, each write their own, and the last one
overwrite the other. Reloading before mutating narrows that window; closing it
needs an ETag and `If-Match`, tracked separately.

## v2.0.5 — 2026-09-15 — Token store fails closed

### Fixed

- **The S3 token store no longer swallows its failures.** `load()` and `_save()`
  printed a warning and returned. A token creation therefore answered `201` with
  a `raw_token` that no one had persisted, a revocation reported success without
  writing anything, and a read failure looked exactly like an empty store, so
  every token became unknown. Both now raise `TokenStoreUnavailable`.
- **A read failure no longer refreshes the cache timestamp.** Leaving it untouched
  is what makes the outage visible to the next caller instead of passing for a
  successful load of an empty store.
- **Mutations reload before writing.** Writing from a stale cache overwrote tokens
  created meanwhile by another instance.
- **A token whose write failed no longer survives in memory.** It would have been
  valid on that one instance and unknown to every other.

- **Neither an error message containing `404` nor a bare HTTP 404 is read as an
  empty store.** A failing proxy turned into "no token exists". Detection now
  relies on the S3 error code alone.
- **A refused mutation no longer stays applied in memory.** A permission
  elevation whose write failed left the instance granting rights the admin had
  seen refused with a 502. Both backends restore the touched entry, and only
  that entry, so a mutation on another token is not swept away with it.
- **An unreachable store at startup no longer prevents the service from
  starting.** Refusing to start would also cost `/health`, the admin console and
  the bootstrap key, which are the means to diagnose the outage. The service
  starts degraded and token authentication answers 503.
- **`TOKEN_STORE_CACHE_TTL` is now honoured by the S3 store**, which used a
  hardcoded 300s while the status endpoint reported the configured value.
- **A malformed Vault payload is an unavailability, not a bare `ValueError`.**
  It used to escape the startup guard and stop the service.

### Changed

- `TOKEN_STORE_FAIL_MODE` is now actually read. It was declared in `config.py`,
  documented in the README and the `.env` example, and referenced by no code path.
  Default stays `fail_close`: past `TOKEN_STORE_STALE_GRACE` (new, 300s), an
  unreachable store denies access rather than serving a stale cache forever.
  `fail_open` keeps the old behaviour as an explicit, documented choice.
- Retries during an outage use an exponential backoff from 1s to 60s, instead of
  one S3 call per incoming request.
- An unverifiable access is now HTTP 503, not 401. Not being able to check a
  credential is not the same as the credential being invalid.
- Admin API write failures return 502, read failures 503.
- `VaultTokenStore` raises `TokenStoreUnavailable` instead of a bare `RuntimeError`,
  so its outages get the same HTTP translation. `TokenStoreUnavailable` subclasses
  `RuntimeError`, so existing callers and tests are unaffected.

- The token store status now reports `reachable`, `cache_age_seconds` and
  `never_loaded`. It used to report `loaded: true` and a token count while
  authentication was already answering 503. The underlying error message stays
  in the logs and does not travel through an HTTP response.

### Known limitation

A `VaultTokenStore` outage still empties the cache and stamps it as fresh, so it
denies every token for the remainder of the TTL without retrying. That is closed,
not open, but it deserves the same bounded stale window and backoff as the S3
store. Tracked separately rather than widened into this fix.

## v2.0.4 — 2026-09-11 — Agentic rules refresh

### Changed

- Replaced the previous `boilerplate/DESIGN/AGENTIC_RULES/` corpus with the
  simplified rules under `boilerplate/AGENTIC_RULES/`, directly at the generated
  project root. `PROJECT_RULES.md` replaces `WORKSPACE_ADVANCE_RULES.md`.
- Kept external Live Memory mandatory: missing configuration, failed context
  loading or failed required writes stop ordinary agent work, including local
  work. Only bounded memory diagnosis and recovery remain permitted.
- Adopted task-based rule loading, risk-based independent reviews and an
  optional EPIC/Project/RC workflow; removed the hard-coded reviewer model and
  duplicate reviews of unchanged content.
- Changed the template's human-approval policy explicitly: a GO remains required
  for each PR merge, including RC targets. Pushes, issues, PRs, Project updates
  and memory consolidation no longer require separate GO requests within the
  mandate. Releases, deployments and operations require an existing delivery
  mandate; this policy grants no technical permission or out-of-scope authority.

### Documentation

- Updated the agent entry files, both READMEs, the project-creation guide and
  DESIGN with mandatory memory setup, real access checks and migration steps
  preserving project-specific rules and memory identifiers.
- Aligned version `2.0.4`, README release links and release metadata.
  No MCP application or dependency change.

---

## v2.0.3 — 2026-09-08 — Security version hardening

### Security

- Replaced floating Python and Caddy base tags with explicit, patched runtime
  versions pinned by image digest: Python 3.11.16, Caddy 2.11.4 and Alpine 3.23.
- Pinned Coraza-Caddy 2.5.0, the reviewed caddy-ratelimit commit and corrected
  Go crypto, network and gRPC modules used to compile the WAF.
- Upgraded the Python packaging toolchain to corrected versions before the
  hash-locked application dependencies are installed.
- Pinned CI actions to full commit SHAs and exact Python/test-tool versions.
- Replaced the archived MinIO server/client images with a non-root Moto 5.2.3
  S3 fixture built from its own hash-locked dependency set.
- Persisted Caddy ACME state in a named volume, preserved non-root execution
  with the minimal bind-service capability and narrowed the WAF bypass to
  `/mcp` and its subpaths only.

### Validation

- Both the application and Moto fixture locks report no known vulnerability
  with `pip-audit` 2.10.1.
- Added regression tests for security-sensitive pins and floating image tags.
- The Moto bucket bootstrap is idempotent across repeated Compose starts.

Moto is an isolated CI fixture, not a production artifact. The production S3
target remains Cloud Temple / Dell ECS and retains its separate live tests.

---

## v2.0.2 — 2026-09-07 — Agentic rules onboarding

### Documentation

- Reworked the agentic-rules onboarding in both the starter-kit and generated
  project READMEs: rationale, source-of-truth model, root `AGENTS.md` bootstrap,
  scope and verification are now documented for newcomers.
- Documented the equivalent Claude Code (`CLAUDE.md`) and Qwen Code (`QWEN.md`)
  discovery mechanisms without duplicating the canonical project rules.
- Added ready-to-use `AGENTS.md`, `CLAUDE.md` and `QWEN.md` bootstraps to the
  generated-project boilerplate.
- Repaired the obsolete root README link to the MCP client setup guide.

This patch changes project onboarding and documentation only; the MCP v2
runtime is identical to `v2.0.1`.

---

## v2.0.1 — 2026-09-06 — Release metadata alignment

### Fixed

- Added the missing repository-level `v2.0.0` Changelog entry.
- Corrected the boilerplate `v2.0.0` release date to the actual publication
  date and exposed the current release from the root README.
- Updated the GitHub repository description and release notes to refer to MCP
  SDK v2 instead of the removed FastMCP architecture.

This patch changes release metadata and documentation only; the MCP v2 runtime
is identical to `v2.0.0`.

---

## v2.0.0 — 2026-09-06 — Migration to MCP Python SDK v2

### Changed

- Migrated the generated MCP server and CLI transport to MCP Python SDK
  `2.1.1`: `MCPServer`, `streamable_http_client` and `httpx2` replace the
  removed FastMCP v1 import and legacy `streamablehttp_client` transport.
- Pinned `mcp==2.1.1` and `mcp-types==2.1.1`; added the Python 3.11
  hash-locked `requirements.lock` used by Docker and CI.
- Preserved the historical one-`ClientSession`-per-call CLI lifecycle. This
  release deliberately introduces neither pooling, sessionless MCP nor
  `mode="auto"`.

### Security

- `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` are mandatory deployment
  configuration; server startup fails closed when either policy is absent.
- The intentional Coraza bypass remains limited to `/mcp` for Streamable HTTP.
  Compensating controls are Caddy per-IP rate limiting, a 4 MiB request-body
  limit, Host/Origin checks, application authentication and secret-free logs.
- The CLI rejects input-required, claimed and all other non-`CallToolResult`
  responses; an unavailable configured internal CA bundle also fails closed.

### Compatibility and validation

- A real isolated MCP `1.28.1` client is validated against the v2 server.
- Unit and ASGI tests passed: 98 passed, 3 skipped, 2 deselected.
- MinIO and Vault Compose E2E paths passed through Caddy/Coraza. The active
  Coraza/OWASP CRS filter blocked a controlled XSS request on the admin API
  with HTTP 403 while `/mcp` streaming remained functional.

---

## v1.2.2 — 2026-06-25 — Admin console activity and rendering hardening

### Fixed

- Fixed the admin Activity page rendering failure caused by numeric ring-buffer
  timestamps while the frontend expected ISO 8601 strings.
- Fixed the interactive shell `token update --resources ...` alias so it matches
  the Click CLI behavior and no longer depends on the legacy `--vaults` spelling.

### Changed

- Hardened admin console rendering by moving dynamic values to DOM
  `textContent` instead of HTML interpolation.
- Replaced inline admin UI event handlers with delegated `data-action` /
  `data-page` handlers so the console can run under a strict script CSP.
- Tightened the app and WAF Content Security Policy to use `script-src 'self'`
  without `unsafe-inline` or CDN script sources.

### Tests

- Added regression coverage for:
  - ISO 8601 UTC timestamps in the activity ring buffer,
  - strict admin and WAF script CSP,
  - absence of inline handlers and obvious HTML injection sinks in admin assets,
  - Click CLI and interactive shell token administration through REST
    `/admin/api/*` rather than MCP tools.

### Documentation

- Documented the admin / MCP / Click CLI / interactive shell contract:
  `/mcp` remains for business and safe system tools, while token administration
  stays under REST `/admin/api/*`.

---

## v1.2.1 — 2026-06-22 — Generic agentic rules templates

This patch release cleans up and finalizes the generated-project agentic rule
templates introduced in v1.2.0.

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
