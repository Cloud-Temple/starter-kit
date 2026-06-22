# 🔧 Mon Service MCP

> Service MCP Cloud Temple — [décrire le domaine métier ici].

## Démarrage rapide

### 1. Configuration

```bash
cp .env.example .env
# Éditer .env avec vos paramètres
# ⚠️  Changer ADMIN_BOOTSTRAP_KEY (min 64 caractères)
```

### 2. Lancement (Docker)

```bash
docker compose build
docker compose up -d

# Vérification
curl http://localhost:8082/health
# → {"status":"healthy","service":"mon-mcp-service","version":"1.2.0"}

# Console d'administration (logo Cloud Temple + sidebar)
open http://localhost:8082/admin
```

### 3. CLI

```bash
pip install -r requirements.txt

# Santé du service
python scripts/mcp_cli.py health

# Informations
python scripts/mcp_cli.py about

# Identité du token courant
python scripts/mcp_cli.py whoami

# Shell interactif (autocomplétion + historique)
python scripts/mcp_cli.py shell

# Gestion des tokens
python scripts/mcp_cli.py token create mon-agent --permissions read,write
python scripts/mcp_cli.py token list
python scripts/mcp_cli.py token revoke <hash_prefix>
```

### 4. Lancement local (sans Docker)

```bash
pip install -r requirements.txt
python -m src.mon_service
```

---

## Architecture

Ce service suit le pattern **3 couches + middlewares ASGI** Cloud Temple.
Voir [DESIGN/ARCHITECTURE.md](DESIGN/ARCHITECTURE.md) pour les détails.

### 3 couches d'interface

| Couche           | Fichier                       | Rôle                               |
| ---------------- | ----------------------------- | ---------------------------------- |
| Outils MCP       | `src/mon_service/server.py`   | API MCP (Streamable HTTP `/mcp`)   |
| CLI Click        | `scripts/cli/commands.py`     | Interface scriptable               |
| Shell interactif | `scripts/cli/shell.py`        | Interface interactive              |
| Affichage        | `scripts/cli/display.py`      | Rich partagé (couches 2+3)         |

### Middlewares ASGI

```
LoggingMiddleware → AdminMiddleware → HealthCheckMiddleware → [AuthMissionJWTMiddleware] → AuthMiddleware → FastMCP
```

| Middleware              | Rôle                                         |
| ----------------------- | -------------------------------------------- |
| LoggingMiddleware       | Log stderr + ring buffer 200 entrées (outer) |
| AdminMiddleware         | Console admin web `/admin` (SPA + API REST)  |
| HealthCheckMiddleware   | `/health`, `/healthz`, `/ready` (sans auth)  |
| AuthMissionJWTMiddleware *(optionnel)* | `mission_token` ES256/JWKS mcp-mission |
| AuthMiddleware          | Bearer/JWT → ContextVars (request-scoped)    |
| FastMCP                 | Protocole MCP (Streamable HTTP)              |

### Infrastructure

```
Internet → WAF Caddy+Coraza (:8082) → mon-mcp (:8002, réseau interne)
```

---

## Structure des fichiers

```
boilerplate/
├── src/mon_service/
│   ├── server.py              # Outils MCP + pile ASGI + bannière
│   ├── config.py              # pydantic-settings (S3, WAF, auth)
│   ├── __main__.py            # python -m mon_service
│   ├── admin/
│   │   ├── middleware.py      # AdminMiddleware ASGI
│   │   └── api.py             # REST API (health, tokens CRUD, logs)
│   ├── auth/
│   │   ├── middleware.py      # AuthMiddleware + LoggingMiddleware
│   │   ├── context.py         # check_access(), ContextVars legacy + mission
│   │   └── token_store.py     # Token Store S3 + cache TTL 5min
│   ├── infra/
│   │   └── auth_mission_jwt_middleware.py # PEP mission_token ES256/JWKS
│   └── static/                # Console admin web
│       ├── admin.html         # SPA HTML (login + sidebar)
│       ├── css/
│       │   └── admin.css      # Design System Cloud Temple (dark theme)
│       ├── img/
│       │   └── logo-cloudtemple.svg
│       └── js/
│           ├── config.js      # Variables globales (chargé en 1er)
│           ├── api.js         # Client HTTP (apiGet/Post/Put/Delete)
│           ├── dashboard.js   # Page Dashboard
│           ├── tokens.js      # Page Tokens (CRUD)
│           ├── activity.js    # Page Activité (auto-refresh 5s)
│           └── app.js         # Navigation + auth + init (chargé en dernier)
├── scripts/
│   ├── mcp_cli.py             # Point d'entrée CLI
│   └── cli/
│       ├── client.py          # Client HTTP MCP
│       ├── commands.py        # Commandes Click
│       ├── shell.py           # Shell interactif
│       └── display.py         # Affichage Rich partagé
├── waf/
│   ├── Dockerfile             # Caddy + Coraza
│   └── Caddyfile              # OWASP CRS + HSTS + rate limiting
├── DESIGN/
│   ├── ARCHITECTURE.md        # Schémas + décisions architecturales
│   └── AGENTIC_RULES/         # Règles agentiques à adapter au projet
├── MAIN_RULES.md              # Point d'entrée des règles projet
├── CHANGELOG.md               # Historique des versions
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── .gitignore
└── VERSION
```

---

## Variables d'environnement

| Variable               | Description                        | Défaut                    |
| ---------------------- | ---------------------------------- | ------------------------- |
| `MCP_SERVER_NAME`      | Nom du service                     | `mon-mcp-service`         |
| `MCP_SERVER_PORT`      | Port d'écoute (interne)            | `8002`                    |
| `WAF_PORT`             | Port WAF (externe)                 | `8082`                    |
| `ADMIN_BOOTSTRAP_KEY`  | Token admin (⚠️ changer !)        | `change_me_in_production` |
| `STARTER_KIT_AUTH_MODE` | Mode auth MCP (`bearer`, `jwt`, `dual-stack`) | `bearer` |
| `MCP_INSTANCE_ID`      | Audience/instance attendue dans le `mission_token` | (vide) |
| `MCP_COMPONENT_KIND`   | Clé `component_id` attendue (`vault`, `teleport`, etc.) | (vide) |
| `MCP_MISSION_JWKS_URL` | URL JWKS public mcp-mission       | (vide) |
| `S3_ENDPOINT_URL`      | Endpoint S3 (optionnel)            | (vide)                    |
| `S3_ACCESS_KEY_ID`     | Clé d'accès S3                     | (vide)                    |
| `S3_SECRET_ACCESS_KEY` | Secret S3                          | (vide)                    |
| `S3_BUCKET_NAME`       | Bucket S3 pour les tokens          | (vide)                    |

**Variables CLI** (shell) :

| Variable    | Description        | Défaut                   |
| ----------- | ------------------ | ------------------------ |
| `MCP_URL`   | URL du serveur     | `http://localhost:8002`  |
| `MCP_TOKEN` | Token d'auth       | (vide)                   |

---

## API Admin

| Méthode   | Endpoint                        | Description                    | Auth    |
| --------- | ------------------------------- | ------------------------------ | ------- |
| `GET`     | `/admin/api/health`             | État du service + outils       | Admin   |
| `GET`     | `/admin/api/whoami`             | Identité du token courant      | Admin   |
| `GET`     | `/admin/api/tokens`             | Liste des tokens               | Admin   |
| `POST`    | `/admin/api/tokens`             | Créer un token                 | Admin   |
| `PUT`     | `/admin/api/tokens/{hash}`      | Modifier permissions/ressources| Admin   |
| `DELETE`  | `/admin/api/tokens/{hash}`      | Révoquer un token              | Admin   |
| `GET`     | `/admin/api/logs`               | Activité récente (50 dernières)| Admin   |

---

## Ajouter un outil métier

Pour chaque outil, modifier **4 fichiers** :

1. **`server.py`** — `@mcp.tool()` avec `Annotated[type, Field(description="...")]`
2. **`display.py`** — Fonction `show_mon_outil_result()` Rich
3. **`commands.py`** — Commande Click avec `@cli.command("mon-outil")`
4. **`shell.py`** — Handler `cmd_mon_outil()` + dispatch + autocomplétion

Voir le guide complet : [Starter Kit MCP Cloud Temple](../README.md)

---

## Règles agentiques du projet

Le fichier [`MAIN_RULES.md`](MAIN_RULES.md) est le point d'entrée à lire en
premier. Le dossier [`DESIGN/AGENTIC_RULES/`](DESIGN/AGENTIC_RULES/) contient
les règles détaillées pour les agents IA qui travailleront dans ce projet une
fois le boilerplate copié.

| Fichier | À adapter pour le projet |
| ------- | ------------------------ |
| `MAIN_RULES.md` | Point d'entrée des règles projet, obligations non négociables et index |
| `DESIGN/AGENTIC_RULES/WORKSPACE_ADVANCE_RULES.md` | Live Memory, Graph Memory, identifiants `SPACE` / `GRAPH_MEMORY_ID`, protocole de consolidation |
| `DESIGN/AGENTIC_RULES/WORKFLOW_ENGINEERING.md` | Reviewer indépendant, cycle adversarial, tests RED/GREEN non complaisants |
| `DESIGN/AGENTIC_RULES/WORKFLOW_GIT.md` | Branches, issues, PR, liens GitHub, règles de merge |
| `DESIGN/AGENTIC_RULES/WORKFLOW_GIT_EPIC.md` | EPIC, RC flow, statuts Project, gates humains |

Le modèle mémoire sépare :

- **Live Memory** : contexte court de session, notes atomiques, consolidation.
- **Graph Memory** : index sémantique durable des documents canoniques.
- **Repository files** : source finale de vérité.

Références Cloud Temple :

- [Cloud-Temple/live-memory](https://github.com/Cloud-Temple/live-memory)
- [Cloud-Temple/graph-memory](https://github.com/Cloud-Temple/graph-memory)

Avant de rendre ces règles obligatoires, remplacer les placeholders du template
par les valeurs du projet. Ne jamais versionner de token, endpoint sensible ou
secret MCP dans ces fichiers.

---

## Console Admin Web

La console `/admin` inclut :
- **Page de login** : logo Cloud Temple + gradient sombre, token persisté en localStorage
- **Header** : logo + nom de service (dynamique) + version + identité utilisateur
- **Sidebar** : navigation verticale (Dashboard, Tokens, Activité)
- **Dashboard** : stats (outils, version, S3), liste des outils MCP
- **Tokens** : liste avec statut, création (modal), révocation
- **Activité** : ring buffer des requêtes, auto-refresh 5s

Pour ajouter une page métier :
1. Ajouter un `<button>` dans la sidebar (`admin.html`)
2. Ajouter un `<div id="page-ma-page">` dans la zone de contenu
3. Créer `static/js/ma-page.js` avec `async function loadMaPage()`
4. Ajouter `else if (name === 'ma-page') loadMaPage()` dans `app.js`
5. Ajouter `<script src="...ma-page.js"></script>` dans `admin.html`

---

## Licence

Cloud Temple — Usage interne.

---

## Token Store backend: S3 ou MCP Vault

Le starter-kit supporte un backend de Token Store configurable.

Par défaut :

```env
TOKEN_STORE_BACKEND=s3
```

Le backend S3 conserve le comportement historique :

```text
_system/tokens.json
```

sur le bucket S3 configuré.

### Backend S3

Variables principales :

```env
TOKEN_STORE_BACKEND=s3
TOKEN_STORE_CACHE_TTL=300
TOKEN_STORE_FAIL_MODE=fail_close

S3_ENDPOINT_URL=
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
S3_BUCKET_NAME=
S3_REGION_NAME=fr1
S3_SIGNATURE_VERSION=s3
S3_ADDRESSING_STYLE=path
```

Note Cloud Temple / Dell ECS :

```env
S3_SIGNATURE_VERSION=s3
S3_ADDRESSING_STYLE=path
```

est le défaut recommandé pour les opérations objet (`GET`, `PUT`, `DELETE`).

### Backend MCP Vault

Pour stocker les tokens clients MCP dans MCP Vault :

```env
TOKEN_STORE_BACKEND=vault
TOKEN_STORE_CACHE_TTL=300
TOKEN_STORE_FAIL_MODE=fail_close

MCP_VAULT_URL=https://vault.mcp.cloud-temple.app
MCP_VAULT_TOKEN_FILE=/run/secrets/mcp_vault_token
MCP_VAULT_TOKEN=
MCP_VAULT_ID=my-mcp-vault
MCP_VAULT_TOKEN_STORE_PATH=token-store/tokens.json
MCP_VAULT_TIMEOUT=5
```

Le token applicatif Vault sert au serveur MCP pour lire/écrire son Token Store dans MCP Vault.

Priorité :

```text
MCP_VAULT_TOKEN_FILE > MCP_VAULT_TOKEN
```

En production, préférer `MCP_VAULT_TOKEN_FILE`.

### Format Vault V1

Le backend Vault V1 utilise un secret JSON unique :

```text
vault: <MCP_VAULT_ID>
path: token-store/tokens.json
```

Payload :

```json
{
  "tokens": [
    {
      "hash": "sha256(raw_token)",
      "client_name": "agent",
      "permissions": ["read"],
      "allowed_resources": [],
      "policy_id": "",
      "email": "",
      "created_at": "...",
      "expires_at": "...",
      "revoked": false,
      "revoked_at": ""
    }
  ]
}
```

Le token brut n'est jamais stocké. Il est affiché une seule fois à la création.

### Fail-close

Le comportement recommandé est sécurisé par défaut :

```text
Vault indisponible + token absent du cache = authentification refusée
Vault indisponible au démarrage = aucun token client chargé
Bootstrap key local = reste utilisable
```

Si Vault est indisponible, l'admin peut encore se connecter via la bootstrap key, mais `token create/update/revoke` ne pourra pas persister tant que Vault n'est pas disponible.

### Health

`GET /admin/api/health` expose un statut non sensible :

```json
{
  "token_store": {
    "backend": "vault",
    "configured": true,
    "loaded": true,
    "tokens_count": 3,
    "cache_ttl": 300,
    "vault_id": "my-mcp-vault",
    "path": "token-store/tokens.json"
  }
}
```

Ne sont jamais exposés :

- `MCP_VAULT_TOKEN`
- le contenu de `tokens.json`
- les tokens clients bruts
- les secrets S3

### Policies

`policy_id` peut être stocké comme métadonnée de token.

Cette version ne fournit pas encore de `PolicyStore` complet ni d'enforcement `allowed_tools` / `denied_tools` / `path_rules`.
