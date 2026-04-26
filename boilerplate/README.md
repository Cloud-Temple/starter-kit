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
# → {"status":"healthy","service":"mon-mcp-service","version":"1.0.0"}

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

Ce service suit le pattern **3 couches + 5 middlewares** Cloud Temple.
Voir [DESIGN/ARCHITECTURE.md](DESIGN/ARCHITECTURE.md) pour les détails.

### 3 couches d'interface

| Couche           | Fichier                       | Rôle                               |
| ---------------- | ----------------------------- | ---------------------------------- |
| Outils MCP       | `src/mon_service/server.py`   | API MCP (Streamable HTTP `/mcp`)   |
| CLI Click        | `scripts/cli/commands.py`     | Interface scriptable               |
| Shell interactif | `scripts/cli/shell.py`        | Interface interactive              |
| Affichage        | `scripts/cli/display.py`      | Rich partagé (couches 2+3)         |

### 5 middlewares ASGI

```
LoggingMiddleware → AdminMiddleware → HealthCheckMiddleware → AuthMiddleware → FastMCP
```

| Middleware              | Rôle                                         |
| ----------------------- | -------------------------------------------- |
| LoggingMiddleware       | Log stderr + ring buffer 200 entrées (outer) |
| AdminMiddleware         | Console admin web `/admin` (SPA + API REST)  |
| HealthCheckMiddleware   | `/health`, `/healthz`, `/ready` (sans auth)  |
| AuthMiddleware          | Bearer Token → ContextVar (request-scoped)   |
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
│   │   ├── context.py         # check_access(), ContextVar
│   │   └── token_store.py     # Token Store S3 + cache TTL 5min
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
│   └── ARCHITECTURE.md        # Schémas + décisions architecturales
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
