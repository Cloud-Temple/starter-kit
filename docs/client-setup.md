# Configurer un client MCP

> Audience : utilisateur final Cline, Cursor, portail ou autre client MCP.  
> Ce guide suppose que le serveur MCP est déjà déployé.

## 1. Ce que vous devez recevoir

L'administrateur du serveur MCP doit vous fournir uniquement :

```text
MCP server URL
MCP client token
```

Exemple :

```text
URL:   https://example.mcp.company/mcp
Token: <MCP_CLIENT_TOKEN>
```

## 2. Ce que vous ne devez pas recevoir

Vous n'avez pas besoin de :

- token MCP Vault ;
- clé S3 ;
- secret S3 ;
- bootstrap key admin ;
- secret Docker/K8s ;
- token d'administration du serveur.

Si on vous demande de configurer un token Vault dans Cline/Cursor, ce n'est probablement pas la bonne procédure : le token Vault est un secret serveur.

## 3. Exemple de configuration Cline / Cursor

Adapter le nom du serveur, l'URL et le token :

```json
{
  "mcpServers": {
    "<mcp-name>": {
      "url": "https://example.mcp.company/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_CLIENT_TOKEN>"
      },
      "timeout": 600
    }
  }
}
```

Pour un test local :

```json
{
  "mcpServers": {
    "my-mcp-local": {
      "url": "http://localhost:8082/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_CLIENT_TOKEN>"
      },
      "timeout": 600
    }
  }
}
```

## 4. Vérifier que le serveur répond

Dans le client MCP, appeler d'abord les tools système si disponibles :

```text
system_health
system_about
system_whoami
```

`system_whoami` doit confirmer l'identité associée au token client MCP.

## 5. Utiliser les tools métier

Le starter-kit fournit le socle technique. Chaque MCP ajoute ses propres tools métier.

Bonnes pratiques côté client :

- lire la description des tools ;
- respecter les limites `max_chars`, `limit`, `offset` ou équivalentes ;
- ne pas supposer qu'un tool peut lire tout un corpus en une fois ;
- citer les IDs/paths retournés par les tools quand le domaine l'exige ;
- ne pas inventer des informations non retournées par les tools.

## 6. Expiration ou révocation du token

Si le token expire ou est révoqué, les appels `/mcp` échoueront.

Demander à l'administrateur du serveur MCP :

- un nouveau token client MCP ; ou
- la prolongation / recréation du token selon la politique du service.

Ne demandez pas de token Vault : ce n'est pas un token client.
