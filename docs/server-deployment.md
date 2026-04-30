# Déployer un serveur MCP généré depuis le starter-kit

> Audience : administrateur du serveur MCP.  
> Ce guide décrit le déploiement d'un MCP construit à partir du starter-kit.  
> Il ne s'adresse pas à l'utilisateur final Cline/Cursor.

## 1. Rôle de l'administrateur serveur

L'administrateur serveur doit :

1. récupérer le repo du MCP ;
2. préparer la configuration `.env` ;
3. préparer les secrets nécessaires côté serveur ;
4. démarrer le serveur MCP derrière le WAF ;
5. vérifier `/health`, `/admin` et `/mcp` ;
6. créer un token client MCP ;
7. transmettre au client final uniquement l'URL MCP et son token client MCP.

Le client final ne reçoit jamais de token Vault applicatif, de clé S3 ou de secret d'infrastructure.

## 2. Deux tokens à ne pas confondre

| Token | Utilisé par | Sert à | Où le mettre ? |
|---|---|---|---|
| Token Vault applicatif | serveur MCP | lire/écrire les secrets autorisés dans MCP Vault | fichier secret local, Docker secret, K8s secret |
| Token client MCP | Cline, Cursor, portail, client MCP | appeler `/mcp` | configuration du client MCP |

Ces deux tokens ont des rôles et des permissions différentes.

## 3. Configuration minimale

Copier l'exemple :

```bash
cp .env.example .env
```

À vérifier au minimum :

```env
MCP_SERVER_NAME=<mcp-name>
MCP_BRAND=ct|dgy|isec
ADMIN_BOOTSTRAP_KEY=<long-random-admin-bootstrap-key>
TOKEN_STORE_BACKEND=s3|vault
```

`ADMIN_BOOTSTRAP_KEY` doit être changé avant tout déploiement partagé ou production.

## 4. TokenStore : S3 ou Vault

Le starter-kit supporte deux backends pour les tokens clients MCP :

```env
TOKEN_STORE_BACKEND=s3
TOKEN_STORE_BACKEND=vault
```

### Option S3

À configurer si les tokens clients MCP sont persistés dans un bucket S3 :

```env
S3_ENDPOINT_URL=...
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
S3_BUCKET_NAME=...
S3_REGION_NAME=fr1
S3_SIGNATURE_VERSION=s3
S3_ADDRESSING_STYLE=path
```

Ne pas commiter ces valeurs.

### Option Vault

À configurer si les tokens clients MCP sont persistés dans MCP Vault :

```env
TOKEN_STORE_BACKEND=vault
MCP_VAULT_URL=https://vault.mcp.cloud-temple.app
MCP_VAULT_ID=<mcp-vault-id>
MCP_VAULT_TOKEN_STORE_PATH=token-store/tokens.json
MCP_VAULT_TOKEN_FILE=/run/secrets/mcp_vault_token
```

Préférer `MCP_VAULT_TOKEN_FILE` à `MCP_VAULT_TOKEN` pour éviter de laisser le token dans l'environnement ou l'historique shell.

## 5. Obtenir un token Vault applicatif serveur

Le token Vault applicatif est créé par un administrateur MCP Vault.

Pour `TOKEN_STORE_BACKEND=vault`, le token doit avoir au minimum :

```text
permissions: ["read", "write"]
allowed_resources: ["<mcp-vault-id>"]
allowed path: token-store/tokens.json
```

Pour un MCP métier qui lit d'autres secrets serveur, par exemple un secret S3 read-only, le token doit être limité aux paths strictement nécessaires.

Exemple de cible conceptuelle :

```text
vault: <mcp-vault-id>
path:  <service-secret-path>
permission: read
```

Règles :

- ne pas donner d'accès global au Vault ;
- ne pas donner `write` si le serveur n'a besoin que de `read` ;
- vérifier que les accès non autorisés retournent `403` ;
- ne jamais transmettre ce token à un client final.

## 6. Préparer un fichier secret local

Exemple local :

```bash
mkdir -p .secrets
umask 077
printf '%s' '<MCP_VAULT_APP_TOKEN>' > .secrets/mcp_vault_token
chmod 600 .secrets/mcp_vault_token
```

Vérifier :

```bash
ls -l .secrets/mcp_vault_token
git status --short --ignored
```

`.secrets/` doit être ignoré par Git.

## 7. Démarrer avec Docker Compose

```bash
docker compose up -d --build
```

ou avec un compose local dédié si le projet en fournit un :

```bash
docker compose -f docker-compose.local.yml up -d --build
```

Vérifier les logs :

```bash
docker compose logs -f
```

## 8. Vérifications serveur minimales

```bash
curl http://localhost:8082/health
curl http://localhost:8082/admin/api/brand
```

Console admin :

```text
http://localhost:8082/admin
```

Connexion initiale : utiliser `ADMIN_BOOTSTRAP_KEY`.

## 9. Créer un token client MCP

Le token client MCP est celui qui sera transmis au client final.

Via CLI :

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/mcp_cli.py \
  --url http://localhost:8082 \
  --token "$ADMIN_BOOTSTRAP_KEY" \
  token create <client-name> --permissions read --expires 30
```

Copier le `raw_token` affiché une seule fois.

Transmettre au client final uniquement :

```text
MCP URL: http://localhost:8082/mcp
MCP client token: <raw_token>
```

## 10. Checklist production

Avant de considérer le serveur prêt :

- [ ] `ADMIN_BOOTSTRAP_KEY` a été changé ;
- [ ] aucun secret n'est commité ;
- [ ] le WAF est le seul service exposé ;
- [ ] `/health` répond ;
- [ ] `/admin` est accessible ;
- [ ] `/admin/api/brand` retourne la marque attendue ;
- [ ] un token client MCP peut être créé ;
- [ ] `/mcp` répond avec ce token ;
- [ ] le token peut être révoqué ;
- [ ] les logs ne contiennent pas de secret ;
- [ ] les secrets Vault/S3 sont limités au strict nécessaire.

## 11. Ce que l'administrateur ne doit pas transmettre au client final

Ne jamais transmettre :

- token Vault applicatif ;
- access key S3 ;
- secret key S3 ;
- bootstrap key admin ;
- contenu brut des secrets Vault ;
- chemins internes sensibles non nécessaires.
