# Changelog — Mon Service MCP

Tous les changements notables de ce projet sont documentés ici.
Format : [SemVer](https://semver.org/) — `[version] — date`

---

## [Unreleased]

### Changed
- Généralisation des templates `DESIGN/AGENTIC_RULES/` :
  - suppression des formulations spécifiques au Terraform provider ;
  - modèle EPIC générique avec issues enfants et PR reliées ;
  - clarification des champs Project configurables par projet ;
  - wording Live Memory / Graph Memory rendu indépendant du workspace source.
- Ajout de `DESIGN/AGENTIC_RULES/MAIN_RULES.md` comme point d'entrée anglais
  des règles projet.
- Renommage du template mémoire workspace pour supprimer la mention d'un outil
  agentique spécifique.

---

## [1.2.0] — 2026-06-21

### Added
- **`AuthMissionJWTMiddleware`** (`infra/auth_mission_jwt_middleware.py`) —
  middleware ASGI réutilisable qui fait d'un MCP un PEP (Policy Enforcement Point)
  validant le `mission_token` ES256 émis par mcp-mission via son JWKS public.
  - Validation ES256 stricte (`kty=EC`, `crv=P-256`, `alg=ES256`) ; refus de
    `alg=none` et de toute confusion d'algorithme.
  - Sélection de clé par `kid` (refus si inconnu/révoqué), `exp` (grâce 0), `iat`
    anti-skew, `aud` ⊇ `MCP_INSTANCE_ID`, `component_id[<kind>] == MCP_INSTANCE_ID`.
  - Cache JWKS avec TTL, ETag/`304 Not Modified`, backoff exponentiel + jitter,
    **fail-close** (HTTP 503) si cache expiré et fetch en échec.
  - Claims obligatoires : `mission_id`, `jti`, `scope` en plus de `iss`, `aud`,
    `iat`, `exp`.
  - Mapping des claims validés vers `request.scope["mission_context"]`,
    `current_mission_context` et une identité `current_token_info`
    `auth_type=mission_token` compatible avec les helpers existants.
    Le `scope` mission est exposé comme `mission_scope`, pas converti en
    `allowed_resources` legacy.
  - Modes `STARTER_KIT_AUTH_MODE=bearer|jwt|dual-stack` (config fail-close au boot).
  - Endpoint admin `POST /admin/auth/jwks/reload` (réservé admin) pour reload immédiat.
  - Documentation : `docs/mission-jwt-middleware.md`.
  - Tests d'acceptation : `tests/test_auth_mission_jwt_middleware.py`.
- **Règles agentiques projet** (`DESIGN/AGENTIC_RULES/`) :
  - bootstrap Live Memory + Graph Memory pour les projets dérivés ;
  - discipline engineering avec reviewer adversarial et tests RED/GREEN ;
  - workflow GitHub issues/PR et workflow EPIC/RC ;
  - gates humains pour merge, release et opérations irréversibles.
- Documentation README reliant ces templates aux dépôts Cloud Temple
  `live-memory` et `graph-memory`.

### Fixed
- Correction de l'intégration ASGI complète : un `mission_token` validé ne laisse
  plus les outils MCP avec `current_token_info=None` après passage dans
  `AuthMiddleware`.
- Alignement du contrat d'acceptation : absence ou mauvais type de `jti`/`scope`
  refusé en fail-close (`401`).
- `check_access(resource_id)` échoue en fail-close pour `mission_token` tant
  qu'aucune policy locale ne mappe explicitement la ressource.
- `check_write_permission()` échoue également en fail-close pour `mission_token` ;
  le `scope` mission n'est pas traité comme permission `write` legacy.

---

## [1.1.0] — 2026-06-15

### Added
- Backend `VaultTokenStore` optionnel (`TOKEN_STORE_BACKEND=vault`) avec tests
  mock HTTP, fake serveur local et configuration fail-close.
- Configuration proxy HTTP sortant (`PROXY_URL`) sans pollution globale de
  l'environnement process.
- Validation JWT/OIDC offline via fichier JWKS local.
- Runbooks opérationnels racine : création de MCP, déploiement serveur,
  configuration client, owner-based isolation future et PolicyStore futur.

### Changed
- Compatibilité Cloud Temple / Dell ECS S3 par défaut :
  `S3_SIGNATURE_VERSION=s3`, `S3_ADDRESSING_STYLE=path`.
- API admin tokens alignée avec `policy_id`, update/revoke et health non sensible.

### Fixed
- Warning `ADMIN_BOOTSTRAP_KEY` aligné sur la valeur `.env.example`.
- Correction de la persistance S3 SigV2/path-style pour les opérations objet.

---

## [1.0.0] — 2026-04-26

### Added — Console Admin Web (refonte complète)
- Logo Cloud Temple officiel (`static/img/logo-cloudtemple.svg`) dans la page de login et le header
- CSS externe séparé (`static/css/admin.css`) — Design System Cloud Temple dark theme complet (300+ lignes, variables CSS, sidebar, modals, badges, tables, forms, responsive)
- JS en 6 fichiers séparés, chargés dans l'ordre :
  - `static/js/config.js` — variables globales (`API_BASE`, `AUTH_TOKEN`, `headers()`)
  - `static/js/api.js` — client HTTP (`apiGet`, `apiPost`, `apiPut`, `apiDelete`)
  - `static/js/dashboard.js` — page Dashboard (stats, liste outils, état S3)
  - `static/js/tokens.js` — page Tokens (liste, création, révocation)
  - `static/js/activity.js` — page Activité (ring buffer, auto-refresh 5s)
  - `static/js/app.js` — navigation, auth, modals, init, localStorage
- `admin.html` refactoré : sidebar verticale, gradient de login, header avec logo + service name dynamique + version, modal "token créé" avec bouton copier, fermeture Escape/backdrop
- Guide "ajouter une page métier" dans le `README.md` boilerplate (5 étapes)

### Added — API REST Admin
- `PUT /admin/api/tokens/{hash_prefix}` — modifier les permissions et ressources autorisées d'un token existant
- `GET /admin/api/whoami` retourne désormais `allowed_resources`, `email`, `created_at`, `expires_at` pour les tokens S3
- `TokenStore.update()` — méthode de modification d'un token (permissions, allowed_resources) avec invalidation de cache

### Added — Sécurité
- `_MAX_BODY_SIZE = 10 MB` dans `_read_body()` — protection anti-OOM sur tous les endpoints POST/PUT
- CORS étendu à `GET, POST, PUT, DELETE, OPTIONS` dans `AdminMiddleware`
- Validation whitelist des permissions (`read`, `write`, `admin`) à la création et modification de tokens
- Protection path traversal dans `_serve_file` : double vérification (`..`/`//` + `resolve()` vs `static_dir`)

### Added — Fichiers projet
- `.gitignore` — Python, IDE, OS, secrets (`.env`, `.clinerules/`)
- `CHANGELOG.md` — template SemVer (ce fichier)
- `DESIGN/ARCHITECTURE.md` — schéma architecture, authentification, fichiers clés, table sécurité, décisions architecturales

### Fixed — Bug path traversal
- **Bug critique** : `AdminMiddleware._serve_file` utilisait `Path(rel).resolve()` relatif au CWD (working directory du process Python) au lieu de `(self.static_dir / filename).resolve()` — tous les fichiers CSS/JS/SVG retournaient 403. Corrigé : vérification canonique via `static_root = self.static_dir.resolve()`.

### Changed
- `admin/api.py` entièrement réécrit : routes documentées en docstring, validation des permissions, messages d'erreur explicites, `_extract_admin_token` strips le token
- `admin/middleware.py` : route PUT dédiée avant le bloc API, protection path traversal simplifiée et corrigée
- `token_store.py` : `count()` docstring précisée "non révoqués"
- `boilerplate/README.md` : structure complète avec les nouveaux fichiers, tableau API Admin, guide pages métier
- `README.md` racine (starter-kit) : section boilerplate mise à jour

---

## [0.1.0] — 2026-01-01

### Initial Release
- Boilerplate MCP Cloud Temple complet
- Serveur MCP avec FastMCP (Streamable HTTP)
- 5 middlewares ASGI : LoggingMiddleware → AdminMiddleware → HealthCheckMiddleware → AuthMiddleware → FastMCP
- Outils système : `system_health`, `system_about`, `system_whoami`
- Console admin web basique (login + dashboard + tokens + activité, CSS inline)
- CLI Click : `health`, `about`, `whoami`, `shell`, `token create/list/revoke`
- Shell interactif (prompt_toolkit) avec autocomplétion et historique
- Token Store S3 avec cache TTL 5 minutes
- WAF Caddy + Coraza (OWASP CRS, rate limiting, HSTS)
- Sécurité : `hmac.compare_digest`, CORS same-origin

---

<!--
Format des entrées :
## [x.y.z] — YYYY-MM-DD

### Added
- Nouvelle fonctionnalité

### Changed
- Modification de comportement existant

### Fixed
- Correction de bug

### Security
- Correction de vulnérabilité

### Deprecated
- Fonctionnalité marquée comme obsolète

### Removed
- Fonctionnalité supprimée
-->
