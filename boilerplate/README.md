# 🔧 Mon Service MCP

> Service MCP Cloud Temple — [décrire le domaine métier ici].

Avant tout travail courant avec un agent IA, configurer et vérifier sa
[mémoire externe obligatoire](#configurer-la-mémoire-externe-obligatoire).
Ce prérequis concerne le harnais de développement ; il n'ajoute pas de dépendance
mémoire au serveur MCP lancé ci-dessous.

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
# → {"status":"healthy","service":"mon-mcp-service","version":"2.0.4"}

# Console d'administration (logo Cloud Temple + sidebar)
open http://localhost:8082/admin
```

### 3. CLI

```bash
pip install --require-hashes -r requirements.lock

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
pip install --require-hashes -r requirements.lock
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
LoggingMiddleware → AdminMiddleware → HealthCheckMiddleware → [AuthMissionJWTMiddleware] → AuthMiddleware → MCPServer
```

| Middleware              | Rôle                                         |
| ----------------------- | -------------------------------------------- |
| LoggingMiddleware       | Log stderr + ring buffer 200 entrées (outer) |
| AdminMiddleware         | Console admin web `/admin` (SPA + API REST)  |
| HealthCheckMiddleware   | `/health`, `/healthz`, `/ready` (sans auth)  |
| AuthMissionJWTMiddleware *(optionnel)* | `mission_token` ES256/JWKS mcp-mission |
| AuthMiddleware          | Bearer/JWT → ContextVars (request-scoped)    |
| MCPServer (SDK v2)      | Protocole MCP (Streamable HTTP)              |

### Infrastructure

```
Internet → WAF Caddy+Coraza (:8082) → mon-mcp (:8002, réseau interne)
```

Les images de base sont verrouillées par version et digest SHA-256. Le runtime
est construit sur Python `3.11.16-slim-trixie`. Le WAF compile
Caddy `2.11.4` avec Coraza-Caddy `2.5.0` (Coraza `3.7.0`, OWASP CRS `4.25.0`)
et s'exécute sans privilèges sur Alpine `3.23`. Les dépendances applicatives
sont installées depuis `requirements.lock` avec hashes ; la CI les contrôle
avec `pip-audit`. La fixture S3 Moto `5.2.3` a son propre lock audité et tourne
également sans privilèges. Les images et actions CI sont versionnées, sans tag
`latest`.

Le volume nommé `caddy-data` conserve les certificats et l'état ACME entre les
recréations du conteneur. Le binaire Caddy possède uniquement la capacité
`NET_BIND_SERVICE`, nécessaire au mode TLS direct sur les ports 80/443, tout en
restant exécuté par l'utilisateur non privilégié `caddy`.

Ne pas ajouter `no-new-privileges` au conteneur dans le mode TLS direct : cette
option neutraliserait la capacité fichier nécessaire aux ports 80/443. Avec un
runtime qui l'impose, utiliser le mode derrière reverse proxy amont et conserver
un port interne non privilégié (`8082`).

---

## Structure des fichiers

```
boilerplate/
├── AGENTS.md                 # Bootstrap commun vers AGENTIC_RULES/
├── CLAUDE.md                 # Import du bootstrap pour Claude Code
├── QWEN.md                   # Compléments éventuels propres à Qwen Code
├── AGENTIC_RULES/             # Règles partagées ; mémoire externe obligatoire
│   ├── MAIN_RULES.md          # Point d'entrée et index de lecture
│   └── PROJECT_RULES.md       # Configuration et protocole mémoire
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
│   └── ARCHITECTURE.md        # Schémas + décisions architecturales
├── CHANGELOG.md               # Historique des versions
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements.lock          # résolution Python 3.11 avec hashes
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
| `MCP_ALLOWED_HOSTS` | Liste JSON des Host publics acceptés par `/mcp` | **obligatoire** |
| `MCP_ALLOWED_ORIGINS` | Liste JSON des Origin publics acceptés par `/mcp` | **obligatoire** |
| `MCP_MAX_REQUEST_BODY_SIZE` | Taille maximale d’un POST `/mcp` | `4194304` (4 MiB) |
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
| `MCP_CLIENT_CA_BUNDLE` | Chemin d’un bundle PEM d’AC interne pour le CLI | magasin système |

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

### Admin / MCP / Click / Shell contract

The generated service keeps a strict separation between runtime surfaces:

| Surface | Endpoint / file | Contract |
| ------- | --------------- | -------- |
| MCP tools | `/mcp`, `src/mon_service/server.py` | Business tools and safe system tools only. |
| Admin web console | `/admin`, `src/mon_service/static/js/*.js` | Human administration, rendered with DOM APIs and `textContent` for dynamic values. |
| Admin REST API | `/admin/api/*` | Token administration, health, identity, branding and activity logs. |
| Click CLI | `scripts/cli/commands.py` | Scriptable client for MCP tools; token commands call REST `/admin/api/*`. |
| Interactive shell | `scripts/cli/shell.py` | Exploratory client; token commands also call REST `/admin/api/*`. |

Token administration must not be exposed as a MCP tool named `token`. Click CLI
and interactive shell token commands use the admin REST API so `/mcp` stays
focused on agent business capabilities.

The admin Activity page expects ISO 8601 UTC timestamps from the logging ring
buffer. The admin frontend renders request paths, token metadata and other
dynamic values via `textContent`, not HTML interpolation. The app and WAF CSP use
`script-src 'self'` without inline event handlers; custom admin pages should use
`data-action` / delegated listeners instead of `onclick=`.

---

## Ajouter un outil métier

Pour chaque outil, modifier **4 fichiers** :

1. **`server.py`** — `@mcp.tool()` avec `Annotated[type, Field(description="...")]`
2. **`display.py`** — Fonction `show_mon_outil_result()` Rich
3. **`commands.py`** — Commande Click avec `@cli.command("mon-outil")`
4. **`shell.py`** — Handler `cmd_mon_outil()` + dispatch + autocomplétion

Voir le guide complet : [Starter Kit MCP Cloud Temple](https://github.com/Cloud-Temple/starter-kit#readme)

---

## Règles agentiques du projet

### Pourquoi et où commencer ?

Les **Agentic Rules** donnent à chaque session le même cadre : sources de vérité,
workflow Git, sécurité, tests et validations. La mémoire externe conserve les
décisions et le contexte entre les sessions ; les règles disent comment l'utiliser.
**Le harnais agentique ne doit jamais fonctionner sans mémoire externe.**

[`AGENTS.md`](AGENTS.md) demande de lire
[`AGENTIC_RULES/MAIN_RULES.md`](AGENTIC_RULES/MAIN_RULES.md), puis
[`PROJECT_RULES.md`](AGENTIC_RULES/PROJECT_RULES.md) pour le démarrage mémoire
obligatoire. L'index indique ensuite les compléments utiles à la tâche ; il
n'impose pas de charger tout le corpus systématiquement.

[`CLAUDE.md`](CLAUDE.md) importe `@AGENTS.md` et
[`QWEN.md`](QWEN.md) rappelle le point d'entrée commun. Conserver ces fichiers
courts : les règles détaillées restent uniquement dans `AGENTIC_RULES/`, à la
racine du projet, et non sous DESIGN.

Pour le chargement propre à chaque outil, consulter les guides officiels :
[Codex — AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
[Claude Code — mémoire](https://code.claude.com/docs/en/memory) et
[Qwen Code — mémoire](https://qwenlm.github.io/qwen-code-docs/en/users/features/memory/).

Ces consignes orientent le modèle, sans remplacer les permissions techniques,
la CI ni les protections de branche.

### Configurer la mémoire externe obligatoire

Le protocole canonique est dans [`PROJECT_RULES.md`](AGENTIC_RULES/PROJECT_RULES.md).
Avant toute tâche courante confiée à un agent :

1. Faire configurer dans son client un serveur MCP Live Memory autorisé pour
   les données du projet et un espace persistant dédié, avec accès en lecture
   et en écriture. La copie du boilerplate ne les crée pas.
2. Remplacer **toutes les occurrences** des marqueurs ci-dessous dans
   `PROJECT_RULES.md`, y compris dans les exemples d'appels.
3. Exécuter la procédure « Au démarrage » de ce fichier. À l'installation,
   enregistrer une première note utile de cadrage et la relire pour vérifier
   l'écriture réelle. Un espace nouvellement créé peut être vide ; un identifiant
   fictif ou un espace inaccessible n'est jamais acceptable.
4. Garder les règles partagées dans Git, mais les secrets de connexion dans la
   configuration du client MCP ou le coffre.

| Marqueur | Valeur à fournir |
| --- | --- |
| `{{LIVE_MCP_SERVER}}` | Nom exact du serveur MCP Live Memory configuré dans le client |
| `{{SPACE_ID}}` | Identifiant réel de l'espace Live Memory du projet |
| `{{GRAPH_MCP_SERVER}}` | Nom du serveur MCP Graph Memory pour l'index documentaire |
| `{{GRAPH_MEMORY_ID}}` | Identifiant réel de cet index Graph Memory |

Live Memory est la mémoire de travail **obligatoire**. Graph Memory est son
complément documentaire : si le projet n'utilise pas cet index, retirer sa
ligne de configuration et sa section de procédure, jamais Live Memory.
Aucun marqueur non renseigné ne doit subsister dans les procédures installées.

En cas d'absence ou de panne de Live Memory, y compris un échec d'écriture en
cours de session, appliquer la section « Mémoire absente ou en panne » de
`PROJECT_RULES.md` : arrêt du travail courant, diagnostic/rétablissement bornés,
puis rechargement du contexte avant reprise. Aucun repli sur le chat ou le dépôt.

Références des services :
[Live Memory](https://github.com/Cloud-Temple/live-memory) et
[Graph Memory](https://github.com/Cloud-Temple/graph-memory).

### Adapter et vérifier les workflows

| Fichier dans `AGENTIC_RULES/` | Rôle |
| --- | --- |
| `MAIN_RULES.md` | Socle, mémoire obligatoire, index et autorisation humaine |
| `PROJECT_RULES.md` | Identifiants du projet et protocole mémoire |
| `WORKFLOW_ENGINEERING.md` | Tests et revues proportionnés aux effets du changement |
| `WORKFLOW_GIT.md` | Branches, issues, PR et merge |
| `WORKFLOW_GIT_EPIC.md` | Complément pour EPIC, Project ou train RC existants |

Le merge d'une PR est le seul GO humain ajouté par ce corpus. Les autres
opérations doivent rester dans le mandat ; une demande de modification locale
n'autorise pas implicitement une release ou un déploiement. Les permissions
techniques et le prérequis mémoire continuent de s'appliquer.

Adapter les conventions et le relecteur à la réalité du projet. Le flux nominal
est une PR vers `main` ; ne pas créer de Project ou de train RC pour satisfaire
le template. Une revue du plan et du résultat est requise pour les changements
sensibles, dont les règles de pilotage elles-mêmes.

Après installation ou modification, ouvrir une nouvelle session et demander les
sources chargées ainsi que le résultat réel du démarrage mémoire, sans secret.
Vérifier aussi que les cinq fichiers de règles ne sont pas ignorés par Git.

### Migrer les anciennes règles

Dans un projet existant, sauvegarder ou versionner les personnalisations avant
de remplacer le corpus. Reporter chaque consigne encore pertinente dans le
nouveau fichier correspondant, puis faire relire le résultat.

| Ancien fichier sous `DESIGN/AGENTIC_RULES/` | Nouveau fichier sous `AGENTIC_RULES/` |
| --- | --- |
| `MAIN_RULES.md` | `MAIN_RULES.md` |
| `WORKSPACE_ADVANCE_RULES.md` | `PROJECT_RULES.md` |
| `WORKFLOW_ENGINEERING.md` | `WORKFLOW_ENGINEERING.md` |
| `WORKFLOW_GIT.md` | `WORKFLOW_GIT.md` |
| `WORKFLOW_GIT_EPIC.md` | `WORKFLOW_GIT_EPIC.md` |

Transférer les valeurs mémoire existantes : l'ancien `SPACE` devient
`{{SPACE_ID}}` ; les trois autres marqueurs gardent leur nom. Ne jamais
remplacer un identifiant réel par celui d'un autre projet.

Mettre à jour les fichiers d'entrée et tous les renvois au corpus. Les chemins
sont désormais explicites : l'ancienne variable `{AGENTIC_RULES_DIR}` est retirée.
Vérifier le démarrage mémoire avant de retirer l'ancien dossier et ne pas garder
deux versions actives des règles.

La migration remplace les GO externes systématiques par un GO au merge seulement
(dans les limites du mandat), autorise la consolidation selon le protocole mémoire,
retire le modèle de revue imposé et les doubles revues de contenu inchangé,
et rend EPIC/RC conditionnels. Conserver explicitement les contrôles plus stricts
que le projet exige, sans perdre l'obligation de mémoire externe.
Le nouveau corpus n'impose pas de nouveau seuil de couverture CI : conserver
les seuils et contrôles déjà requis par le projet, sans les affaiblir lors de
la migration.

---

## Console Admin Web

La console `/admin` inclut :
- **Page de login** : logo Cloud Temple + gradient sombre, token persisté en localStorage
- **Header** : logo + nom de service (dynamique) + version + identité utilisateur
- **Sidebar** : navigation verticale (Dashboard, Tokens, Activité)
- **Dashboard** : stats (outils, version, S3), liste des outils MCP
- **Tokens** : liste avec statut, création (modal), révocation
- **Activité** : ring buffer des requêtes, auto-refresh 5s

Security baseline:
- activity log timestamps are emitted as ISO 8601 UTC strings;
- request paths and token metadata are rendered with `textContent`;
- admin navigation and actions use delegated `data-page` / `data-action`
  handlers, not inline `onclick=`;
- the app and WAF CSP keep `script-src 'self'`.

Pour ajouter une page métier :
1. Ajouter un `<button data-page="ma-page">` dans la sidebar (`admin.html`)
2. Ajouter un `<div id="page-ma-page">` dans la zone de contenu
3. Créer `static/js/ma-page.js` avec `async function loadMaPage()` et rendre les
   données dynamiques via `textContent`
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

### Comportement en cas de panne du magasin

Le magasin de tokens ne masque pas ses pannes. Une lecture ou une écriture qui
échoue lève une erreur au lieu d'imprimer un avertissement, et l'appelant la
traduit en réponse HTTP.

| Situation | Comportement |
| --- | --- |
| Objet de tokens absent | Magasin vide, pas une panne. |
| Lecture en échec, cache plus jeune que `TOKEN_STORE_CACHE_TTL` | Le cache est servi, rien n'est tenté. |
| Lecture en échec, cache périmé depuis moins de `TOKEN_STORE_STALE_GRACE` | Le cache périmé est servi, la panne est journalisée. |
| Lecture en échec au-delà de cette fenêtre, `fail_close` | Accès refusé, HTTP 503. Une révocation ne peut pas être ignorée indéfiniment. |
| Lecture en échec au-delà de cette fenêtre, `fail_open` | Le cache périmé reste servi. Choix explicite, révocations ignorées pendant la panne. |
| Écriture en échec | HTTP 502. Aucun token n'est rendu à l'appelant, et le token créé ne survit pas en mémoire. |

Pendant une panne, les tentatives sont espacées par un backoff exponentiel
d'une à soixante secondes, au lieu d'un appel S3 par requête entrante.

### Backend S3

Variables principales :

```env
TOKEN_STORE_BACKEND=s3
TOKEN_STORE_CACHE_TTL=300
TOKEN_STORE_FAIL_MODE=fail_close
TOKEN_STORE_STALE_GRACE=300

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
TOKEN_STORE_STALE_GRACE=300

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
