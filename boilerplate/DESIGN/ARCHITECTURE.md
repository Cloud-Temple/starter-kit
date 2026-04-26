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
│  │ AuthMiddleware      — Bearer Token → ContextVar      │   │
│  │ FastMCP             — /mcp (Streamable HTTP)         │   │
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

### Cycle de vie d'une requête MCP
```
1. Client HTTP → Bearer Token dans header Authorization
2. AuthMiddleware → hash SHA-256 → lookup cache TTL
3. Token valide → current_token_info.set(info)  [ContextVar]
4. Outil MCP → check_access(resource_id)  [lit le ContextVar]
5. Réponse → {"status": "ok", "data": ...}
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
│   ├── context.py     # check_access(), ContextVar
│   └── token_store.py # Token Store S3 + cache
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

---

## Sécurité — Points d'attention

| Risque | Mitigation |
|--------|-----------|
| Timing attack sur le bootstrap key | `hmac.compare_digest()` |
| Token via query string | Bearer header uniquement |
| Body trop volumineux (OOM) | `_MAX_BODY_SIZE = 10 MB` |
| CORS wildcard sur l'admin | Same-origin, pas d'`Access-Control-Allow-Origin: *` |
| Hash prefix trop court → collision | Min 8 caractères pour revoke |
| Bootstrap key par défaut en prod | Warning au démarrage |
| LoggingMiddleware en innermost | Placé outermost (premier exécuté) |

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
| FastMCP (pas MCP SDK bas niveau) | Décorateurs `@mcp.tool()`, moins de boilerplate |
| ContextVar pour l'auth | Thread-safe en asyncio, zéro couplage |
| S3 pour les tokens | Persistance sans base de données, compatible cloud |
| Ring buffer 200 entrées | Activité temps réel sans surcharge mémoire |
| Sidebar + JS séparés | Maintenabilité, extensibilité métier |
