# Architecture — Mon Service MCP

> Adapter ce document au contexte de votre service.
> Ce fichier sert de référence pour les développeurs et les agents IA (Cline, etc.).

---

## Vue d'ensemble

```
┌──────────────────────────────────────────────────────────────┐
│  Client MCP (Cline, Claude, agent autonome)                  │
│  CLI (mcp_cli.py) / Console Admin (navigateur)               │
└─────────────────────────┬────────────────────────────────────┘
                          │ HTTPS (port 8082)
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  WAF — Caddy + Coraza                                        │
│  TLS · Rate Limiting · OWASP CRS · HSTS                     │
└─────────────────────────┬────────────────────────────────────┘
                          │ HTTP (port 8002, réseau interne)
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  Pile ASGI (ordre ext → int)                                 │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ LoggingMiddleware   — ring buffer 200 requêtes       │   │
│  │ AdminMiddleware     — /admin, /admin/static/, /admin/api/ │
│  │ HealthCheckMiddleware — /health, /healthz, /ready    │   │
│  │ AuthMissionJWTMiddleware — mission_token ES256/JWKS  │   │
│  │ AuthMiddleware      — Bearer/JWT → ContextVars       │   │
│  │ MCPServer (SDK v2)  — /mcp (Streamable HTTP)         │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────┬────────────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
┌─────────────────────┐   ┌─────────────────────────────────┐
│ S3 Token Store      │   │  Services Métier                 │
│ _system/tokens.json │   │  (vos APIs, bases de données...) │
│ cache TTL 5 min     │   └─────────────────────────────────┘
└─────────────────────┘
```

---

## Les 3 couches d'interface

Toute fonctionnalité doit être exposée dans les 3 couches :

| Couche | Fichier | Consommateur |
|--------|---------|--------------|
| **Outil MCP** | `server.py` | Agents IA (Cline, Claude Desktop) |
| **CLI Click** | `scripts/cli/commands.py` | DevOps, scripts CI/CD |
| **Shell interactif** | `scripts/cli/shell.py` | Exploration interactive |

### Admin / MCP / Click / Shell boundary

Administration and business tools are intentionally separated:

| Surface | Boundary |
|---------|----------|
| `/mcp` | Business tools and safe system tools exposed to MCP clients. |
| `/admin/api/*` | Server administration: tokens, health, identity, branding and activity logs. |
| Click CLI | Uses MCP tool calls for system/business commands; token commands call `/admin/api/*`. |
| Interactive shell | Same contract as Click; token commands call `/admin/api/*`. |
| `/admin` web console | Human administration UI backed by `/admin/api/*`. |

There must be no MCP tool named `token` for token administration. Keeping token
CRUD under REST `/admin/api/*` avoids exposing server administration as an agent
business capability.

---

## Authentification

### Token Store S3
- Fichier : `_system/tokens.json` dans le bucket S3
- Cache mémoire : TTL 5 minutes (`token_store.py`)
- Hash : SHA-256 du token brut (jamais stocké en clair)

### Bootstrap Key
- Variable d'env : `ADMIN_BOOTSTRAP_KEY`
- Utilisée pour le premier accès (avant création de tokens S3)
- Comparaison : `hmac.compare_digest()` (anti timing-attack)

### Mission Token PEP
- Activation : `STARTER_KIT_AUTH_MODE=jwt|dual-stack`
- Module : `infra/auth_mission_jwt_middleware.py`
- Rôle : valider un `mission_token` ES256 émis par mcp-mission via JWKS dynamique
- Claims obligatoires : `iss`, `aud`, `iat`, `exp`, `mission_id`, `jti`, `scope`
- Contexte exposé :
  - `request.scope["mission_context"]`
  - `current_mission_context`
  - `current_token_info` avec `auth_type=mission_token`
  - `mission_scope` séparé de `allowed_resources`

Le pont vers `current_token_info` permet aux outils existants de rester
request-scoped via les helpers du starter-kit, sans conférer de droit `admin`.
Il ne convertit pas le scope mcp-mission en ressources legacy.
Par défaut, `check_access(resource_id)` échoue en fail-close sous
`mission_token` tant qu'aucune policy locale ne mappe la ressource.
`check_write_permission()` échoue aussi : le `scope` mission ne confère pas de
droit `write` legacy.
L'enforcement fin `mission_id` actif / OPA Rego reste à implémenter dans le MCP
consommateur quand le domaine l'exige.

### Cycle de vie d'une requête MCP legacy
```
1. Client HTTP → Bearer Token dans header Authorization
2. AuthMiddleware → hash SHA-256 → lookup cache TTL
3. Token valide → current_token_info.set(info)  [ContextVar]
4. Outil MCP → check_access(resource_id)  [lit le ContextVar]
5. Réponse → {"status": "ok", "data": ...}
```

### Cycle de vie d'une requête mission_token
```
1. Client HTTP → Authorization: Bearer <mission_token JWT>
2. AuthMissionJWTMiddleware → validation ES256/JWKS + aud/component_id
3. Claims valides → scope["mission_context"]
4. AuthMiddleware → current_mission_context + current_token_info mission_token
5. Outil MCP → helpers ContextVar + contrôles métier/OPA éventuels
```

---

## Fichiers clés

```
src/mon_service/
├── server.py          # Outils MCP + pile ASGI + bannière
├── config.py          # Variables d'env (pydantic-settings)
├── admin/
│   ├── middleware.py  # AdminMiddleware (static + API routing)
│   └── api.py         # REST API admin (health, tokens, logs)
├── auth/
│   ├── middleware.py  # AuthMiddleware + LoggingMiddleware
│   ├── context.py     # check_access(), ContextVars legacy + mission
│   └── token_store.py # Token Store S3 + cache
├── infra/
│   └── auth_mission_jwt_middleware.py # PEP mission_token ES256/JWKS
└── static/
    ├── admin.html     # SPA HTML
    ├── css/admin.css  # Design System Cloud Temple
    ├── img/logo-cloudtemple.svg
    └── js/
        ├── config.js  # Variables globales
        ├── api.js     # Client HTTP
        ├── dashboard.js
        ├── tokens.js
        ├── activity.js
        └── app.js     # Navigation + auth + init
```

### Règles agentiques livrées

```
DESIGN/AGENTIC_RULES/
├── MAIN_RULES.md                    # Point d'entrée des règles projet
├── WORKSPACE_ADVANCE_RULES.md       # Live Memory + Graph Memory
├── WORKFLOW_ENGINEERING.md          # Review adversariale + tests
├── WORKFLOW_GIT.md                  # Branches, issues, PR
└── WORKFLOW_GIT_EPIC.md             # EPIC, RC flow, gates humains
```

Ces fichiers sont des templates à adapter dans le projet dérivé. Ils relient le
travail des agents IA à Live Memory pour le contexte court, à Graph Memory pour
l'index sémantique durable, et aux fichiers du repository comme source finale
de vérité.

---

## Sécurité — Points d'attention

| Risque | Mitigation |
|--------|-----------|
| Timing attack sur le bootstrap key | `hmac.compare_digest()` |
| Token via query string | Bearer header uniquement |
| mission_token malformé | Validation stricte ES256/JWKS, `jti` et `scope` obligatoires |
| mission_token validé mais outils anonymes | Pont ContextVar mission dans `AuthMiddleware` |
| Body trop volumineux (OOM) | `_MAX_BODY_SIZE = 10 MB` |
| CORS wildcard sur l'admin | Same-origin, pas d'`Access-Control-Allow-Origin: *` |
| XSS via admin dynamic values | DOM `textContent`, delegated actions, strict `script-src 'self'` CSP |
| Activity page blank render | ISO 8601 UTC timestamps in the logging ring buffer |
| Hash prefix trop court → collision | Min 8 caractères pour revoke |
| Bootstrap key par défaut en prod | Warning au démarrage |
| LoggingMiddleware en innermost | Placé outermost (premier exécuté) |
| DNS rebinding / Origin sur `/mcp` | `MCP_ALLOWED_HOSTS` et `MCP_ALLOWED_ORIGINS` explicites, fournis par le déploiement |
| Bypass Coraza nécessaire au streaming `/mcp` | Bypass limité à `/mcp` ; rate limiting Caddy, auth, logs sans secrets et limite SDK `MCP_MAX_REQUEST_BODY_SIZE` (4 MiB) |

---

## Conventions

### Format de retour des outils MCP
```python
{"status": "ok", "data": ...}        # Succès
{"status": "error", "message": "..."}  # Erreur
{"status": "created", ...}            # Création
{"status": "deleted", ...}            # Suppression
```

### Logs serveur
Toujours sur `stderr` (jamais `stdout` qui pollue le flux MCP) :
```python
print(f"🔧 [MonOutil] Message", file=sys.stderr)
```

---

## Décisions architecturales

| Décision | Raison |
|----------|--------|
| Streamable HTTP (pas SSE) | Standard MCP v2024+, bidirectionnel, proxy-friendly |
| MCPServer (SDK MCP v2) | Décorateurs `@mcp.tool()`, compatibilité avec les serveurs MCP v1 déployés et transport Streamable HTTP maintenu |
| Une `ClientSession` par appel CLI | Contrat historique préservé ; ni pool ni client sessionless introduits par la migration |
| Host/Origin configurés au déploiement | Le serveur écoute derrière Caddy ; les noms publics exacts ne sont pas codés dans le template |

### Transport MCP v2 et edge

`MCPServer.streamable_http_app()` reçoit une politique de transport explicite.
`MCP_ALLOWED_HOSTS` et `MCP_ALLOWED_ORIGINS` sont des listes JSON : en
production elles doivent contenir les valeurs publiques exactes vues par Caddy,
par exemple `["mcp.example.fr"]` et `["https://mcp.example.fr"]`. Les valeurs
`localhost` du `.env.example` ne sont qu’un confort de développement ; leur
absence bloque le démarrage du serveur.

Le bypass Coraza de `/mcp` est intentionnel : l’inspection WAF peut bufferiser
le SSE et provoquer des faux positifs sur les charges JSON/encodées. Il ne
s’étend pas aux routes admin, health ou statiques : elles restent sous
Coraza/OWASP CRS. Les compensations obligatoires pour `/mcp` sont : limitation
Caddy à 300 requêtes/minute/IP, limite applicative de 4 MiB (configurable),
validation Host/Origin, authentification applicative et journalisation
outermost sans secret. Les validations Compose MinIO et Vault démontrent que le
module Coraza est chargé et que CRS bloque une requête malveillante hors `/mcp`.

Le CLI repose sur le magasin d’AC système fourni par `httpx2`/`truststore`. Si
l’AC interne n’y est pas installée, le déploiement monte un bundle PEM en
lecture seule et renseigne `MCP_CLIENT_CA_BUNDLE`; un chemin absent échoue fermé.

Le verrou exact des dépendances est dans `requirements.lock`, généré sous
Python 3.11 avec hashes. Les versions exactes `mcp==2.1.1` et
`mcp-types==2.1.1` sont revues dans la veille sécurité des dépendances afin de
ne pas différer un correctif CVE upstream.
| ContextVar pour l'auth | Thread-safe en asyncio, zéro couplage |
| AuthMissionJWT en amont du Bearer legacy | Validation PEP avant fallback dual-stack |
| S3 pour les tokens | Persistance sans base de données, compatible cloud |
| Ring buffer 200 entrées | Activité temps réel sans surcharge mémoire |
| Sidebar + JS séparés | Maintenabilité, extensibilité métier |
