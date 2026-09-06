# Créer un nouveau MCP depuis le starter-kit

> Objectif : partir du starter-kit et obtenir un MCP minimal qui démarre, expose `/mcp`, `/admin`, une CLI, un shell et au moins un tool métier.

Ce guide est volontairement générique. Les règles métier d'un MCP concret doivent rester dans le repo du MCP concret.

## 1. Créer le nouveau repo

Options possibles :

- utiliser GitHub comme template si le repo est configuré ainsi ;
- forker le repo ;
- copier le contenu du dossier `boilerplate/` vers un nouveau repo.

Nom recommandé :

```text
mcp-<domain>
```

Exemples :

```text
mcp-legifrance
mcp-tools
mcp-office
```

## 2. Renommer le service

À adapter :

```text
mon_service        -> <python_package>
mon-mcp-service    -> <mcp-service-name>
Mon Service MCP    -> <display name>
```

Fichiers typiques :

```text
src/mon_service/
scripts/mcp_cli.py
Dockerfile
docker-compose.yml
.env.example
README.md
```

Après renommage, vérifier :

```bash
python -m <python_package>
python scripts/mcp_cli.py --help
```

## 3. Choisir le branding

Dans `.env.example` et `.env` :

```env
MCP_BRAND=ct
```

Valeurs supportées :

```text
ct    Cloud Temple
dgy   Dragonfly
isec  Intrinsec
```

Vérifier :

```bash
curl http://localhost:8082/admin/api/brand
```

## 4. Ajouter le premier tool métier

Ajouter le tool dans :

```text
src/<python_package>/server.py
```

Règles :

- utiliser `@mcp.tool()` ;
- typer les paramètres avec `Annotated[..., Field(description=...)]` ;
- retourner un dict JSON-serializable ;
- borner les réponses (`limit`, `max_chars`, `offset` si nécessaire) ;
- refuser les chemins ou entrées dangereuses ;
- ne pas exposer de secrets.

Exemple minimal :

```python
from typing import Annotated
from pydantic import Field

@mcp.tool()
async def my_readonly_lookup(
    identifier: Annotated[str, Field(description="Business identifier to resolve")],
) -> dict:
    """Resolve a business identifier."""
    return {
        "status": "ok",
        "identifier": identifier,
    }
```

## 5. Ajouter CLI et shell si le tool est structurant

Le starter-kit fournit déjà les commandes système et token.

Pour un tool métier important, ajouter une commande dans :

```text
scripts/cli/commands.py
scripts/cli/shell.py
scripts/cli/display.py
```

Le starter-kit ne doit pas contenir toutes les commandes métier possibles, mais il doit montrer comment en ajouter.

## 6. Choisir le TokenStore

### Développement simple

Sans S3/Vault configuré, seul le bootstrap admin fonctionne.

### TokenStore S3

Configurer :

```env
TOKEN_STORE_BACKEND=s3
S3_ENDPOINT_URL=...
S3_BUCKET_NAME=...
```

### TokenStore Vault

Configurer :

```env
TOKEN_STORE_BACKEND=vault
MCP_VAULT_ID=<mcp-vault-id>
MCP_VAULT_TOKEN_FILE=/run/secrets/mcp_vault_token
MCP_VAULT_TOKEN_STORE_PATH=token-store/tokens.json
```

Voir aussi :

```text
docs/server-deployment.md
```

## 7. Lancer les tests

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
pip install pytest pytest-asyncio
python -m pytest tests -q
```

Si le projet a des tests Docker Compose :

```bash
docker compose -f docker-compose.ci.yml up -d --build
RUN_COMPOSE_E2E=1 python -m pytest tests/e2e -q
docker compose -f docker-compose.ci.yml down -v
```

## 8. Lancer Docker Compose

```bash
docker compose up -d --build
```

Vérifier :

```bash
curl http://localhost:8082/health
curl http://localhost:8082/admin/api/brand
```

Console admin :

```text
http://localhost:8082/admin
```

## 9. Créer un token client MCP

```bash
python scripts/mcp_cli.py \
  --url http://localhost:8082 \
  --token "$ADMIN_BOOTSTRAP_KEY" \
  token create local-client --permissions read --expires 7
```

Tester ensuite le client MCP avec :

```text
http://localhost:8082/mcp
Authorization: Bearer <MCP_CLIENT_TOKEN>
```

## 10. Checklist avant production

- [ ] package renommé proprement ;
- [ ] service name configuré ;
- [ ] branding choisi ;
- [ ] `/health` OK ;
- [ ] `/admin` OK ;
- [ ] `/mcp` OK avec token client ;
- [ ] CLI et shell OK ;
- [ ] token create/list/revoke OK ;
- [ ] au moins un tool métier testé ;
- [ ] réponses métier bornées ;
- [ ] aucun secret en git ;
- [ ] WAF actif ;
- [ ] docs serveur/client adaptées au MCP.

## 11. Retour d'expérience

Le MCP `mcp-legifrance` a servi de premier smoke test réel du starter-kit : un MCP métier read-only créé depuis ce socle, déployé en production et utilisé avec un client MCP.

La leçon principale est que le starter-kit doit fournir des guides opérationnels courts :

- déploiement serveur ;
- distinction token Vault applicatif vs token client MCP ;
- configuration client final ;
- smoke test de création d'un MCP réel.
