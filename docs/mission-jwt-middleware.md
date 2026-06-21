# AuthMissionJWTMiddleware — validation du `mission_token` (PEP côté MCP)

> Composant de sécurité. Lire entièrement avant activation en production.

Ce middleware ASGI permet à n'importe quel MCP Cloud Temple de valider un
**`mission_token`** — un JWT signé **ES256** (ECDSA P-256 + SHA-256) émis par
`mcp-mission` — **avant** d'exécuter une requête. Il matérialise le *PEP* (Policy
Enforcement Point) décrit dans `Cloud-Temple/mcp-mission` ARCHITECTURE §17.10
(enforcement d'instance) et §17.12 (migration dual-stack des PEPs).

Module : `mon_service/infra/auth_mission_jwt_middleware.py`
Classe publique : `AuthMissionJWTMiddleware`.

## 1. Ce que le middleware vérifie

À réception d'un `Authorization: Bearer <JWT>`, dans cet ordre (fail-close à
chaque étape, cf. §17.10 « enforcement en 9 étapes ») :

1. Header → `kid` + `alg`. **`alg` doit être `ES256`** (refus de `none`, de RS256,
   de toute confusion d'algorithme).
2. Sélection de la clé publique via `kid` dans le JWKS. Un `kid` inconnu/révoqué
   (absent du JWKS publié) → refus.
3. Signature ES256 + `exp` (**grâce 0 côté PEP** — le refresh est géré côté agent).
4. `iat <= now + leeway` (anti-skew d'horloge avancée, `MISSION_JWT_IAT_LEEWAY_SECONDS`).
5. `mission_id`, `jti` et `scope` sont présents et correctement typés.
6. `aud` **contient** `MCP_INSTANCE_ID` (sinon `403`).
7. `component_id[MCP_COMPONENT_KIND] == MCP_INSTANCE_ID` (sinon `403`).

Les étapes 7-9 du contrat mcp-mission (vérification `mission_id` actif, OPA Rego,
enforcement applicatif) relèvent du MCP consommateur et **ne sont pas** dans le
périmètre du middleware. Voir [Limites](#5-limites-et-hypothèses).

En cas de succès, les claims utiles sont projetés dans
`request.scope["mission_context"]` :

```python
{
  "aud": [...], "component_id": {...}, "scope": [...],
  "mission_id": "...", "jti": "...", "tenant_id": "...",
  "template_id": "...", "provenance": [...]
}
```

`AuthMiddleware` lit ensuite ce `mission_context` et alimente les `ContextVar`
du starter-kit :

- `current_mission_context` contient les claims mission utiles ;
- `current_token_info` reçoit une identité compatible avec les helpers existants,
  avec `auth_type=mission_token` et sans permission `admin`.
- `scope` est exposé comme `mission_scope` ; il n'est pas converti en
  `allowed_resources` legacy.

Cela évite qu'un `mission_token` validé arrive dans les outils MCP comme une
requête anonyme. L'autorisation fine reste volontairement séparée : le pont
ContextVar fournit une identité de requête, pas une décision OPA complète.
Par défaut, `check_access(resource_id)` échoue en fail-close pour une identité
`mission_token` si aucune policy locale ne mappe explicitement cette ressource.
`check_write_permission()` échoue également en fail-close : le `scope`
mcp-mission ne confère aucun droit `write` legacy.

Réponses d'échec (corps JSON minimal, sans oracle d'attaque) :

| Cas                                                   | Statut |
| ----------------------------------------------------- | ------ |
| JWT malformé / signature / `exp` / `iat` / `iss` / `kid` inconnu | `401` |
| `mission_id`, `jti` ou `scope` absent / mal typé          | `401` |
| `aud` ou `component_id` non conforme                  | `403`  |
| JWKS indisponible **et** cache expiré (fail-close)    | `503`  |
| Bearer legacy sur appel mission, en mode `jwt`        | `401`  |

## 2. Modes d'activation (`STARTER_KIT_AUTH_MODE`)

| Mode         | Comportement                                                                 | Cible           |
| ------------ | ---------------------------------------------------------------------------- | --------------- |
| `bearer`     | Middleware **non inséré** dans la pile. Auth Bearer legacy uniquement.       | défaut          |
| `jwt`        | **Seul** le `mission_token` est accepté. Bearer legacy **refusé** (`401`).   | P1/P2 (vault, teleport) |
| `dual-stack` | JWT validé s'il est présenté ; sinon **fallback** Bearer legacy.             | P3-P7           |

Le discriminant JWT vs Bearer legacy est syntaxique : un Bearer **legacy opaque
ne contient jamais de point**. Tout token contenant un `.` est donc considéré
comme une **tentative de `mission_token`** et validé strictement — un JWT
tronqué/cassé (ex. 2 segments) est refusé, jamais délégué au Bearer legacy (pas
de fail-open par token JWT malformé).

## 3. Risque fail-open — à comprendre absolument

- **`bearer`** : aucune validation `mission_token`. C'est l'état historique ;
  acceptable uniquement pour un MCP qui n'est pas (encore) un PEP mission.
- **`dual-stack`** : un appelant peut présenter un **Bearer legacy** et être
  autorisé par l'`AuthMiddleware` legacy. C'est un fail-open *contrôlé* et
  *temporaire* : interdit sur P1/P2, borné/audité sur P3-P7 (cf. §17.12).
  ⚠️ En revanche, présenter un **JWT invalide** en `dual-stack` ne retombe
  **jamais** sur le Bearer legacy : le token est refusé. Pas de fail-open par
  JWT cassé.
- **`jwt`** : pas de fail-open. Tout ce qui n'est pas un `mission_token` valide
  pour cette instance est refusé.

> **P1/P2 (mcp-vault, mcp-teleport)** : mode `jwt` obligatoire avant la GA V1 de
> mcp-mission. Le dual-stack y est **interdit** (risque de fuite de Bearer = secrets).

La configuration est **fail-close à la construction** : en mode `jwt`/`dual-stack`,
si `MCP_INSTANCE_ID`, `MCP_COMPONENT_KIND` ou `MCP_MISSION_JWKS_URL` manquent, le
service **refuse de démarrer** (plutôt que de valider sans contrôle d'instance).

## 4. Configuration

```env
STARTER_KIT_AUTH_MODE=jwt            # bearer | jwt | dual-stack
MCP_INSTANCE_ID=vault-prod-eu-tenant-acme   # identité unique de CETTE instance
MCP_COMPONENT_KIND=vault             # clé dans le claim component_id
MCP_MISSION_JWKS_URL=https://mcp-mission.internal/.well-known/jwks.json
JWKS_CACHE_TTL_SECONDS=300           # cache JWKS (défaut 5 min)
MISSION_JWT_IAT_LEEWAY_SECONDS=60    # anti-skew sur iat (défaut 60 s)
```

`MCP_INSTANCE_ID` est **le** paramètre de sécurité central : c'est l'identité que
le middleware exige dans `aud` et `component_id`. Deux instances du même type de
MCP (ex. deux vaults) doivent avoir des `MCP_INSTANCE_ID` distincts pour que
l'enforcement d'instance ait du sens (anti audience confusion T03).

`MCP_MISSION_JWKS_URL` doit être en **`https://`** (anti MITM / cache-poisoning du
JWKS, T09). Le schéma `http://` n'est toléré que pour un hôte **interne**
(loopback ou nom de service Docker court sans point, ex. `http://mcp-mission/...`) ;
tout autre schéma (`file://`, `data://`, http vers un hôte routable) fait
**échouer le démarrage**.

### Cache JWKS

- **TTL** : sert depuis le cache tant que `now < fetched_at + TTL`.
- **ETag / 304** : à expiration, revalidation conditionnelle (`If-None-Match`) ;
  un `304 Not Modified` prolonge la fraîcheur sans re-télécharger.
- **Backoff exponentiel** sur échec de fetch : 1, 2, 4, 8, 16, 30 s (cap) + jitter ±20 %.
- **Fail-close** : cache expiré **et** fetch en échec → `503` (jamais de cache périmé servi).
- **Rotation de `kid`** : un token avec un `kid` absent du cache déclenche un
  refresh forcé (hors fenêtre de backoff) avant de conclure « kid inconnu ».

## 5. Endpoint admin de reload

```
POST /admin/auth/jwks/reload      (Authorization: Bearer <token admin>)
→ 200 {"status":"ok","keys":<n>}
→ 401 si token non admin
→ 503 si JWKS injoignable
```

Force un refresh JWKS **immédiat** (réinitialise le backoff). Utile pour propager
une révocation urgente de `kid` sans attendre l'expiration du TTL. L'endpoint
réutilise le contrôle d'auth admin du starter-kit (bootstrap key / token admin,
comparaison en temps constant).

## 6. Pile ASGI

Le middleware est inséré **en amont** de l'`AuthMiddleware` Bearer legacy, et
n'est ajouté que si `STARTER_KIT_AUTH_MODE != "bearer"` :

```
Logging → Admin → HealthCheck → AuthMissionJWT → AuthBearer(legacy) → FastMCP
```

Responsabilités :

- `AuthMissionJWTMiddleware` valide le JWT et écrit `scope["mission_context"]`.
- `AuthMiddleware` reste le point unique d'injection des `ContextVar` utilisés
  par les outils (`current_token_info`, `current_mission_context`).
- `system_whoami` expose `auth_type=mission_token`, `mission_id`, `jti`,
  `tenant_id` et `template_id` quand la requête est authentifiée par mission.
- Les outils métier qui appellent `check_access(resource_id)` doivent ajouter un
  mapping/policy local pour autoriser une ressource sous `mission_token`.
- Les outils métier qui appellent `check_write_permission()` doivent aussi
  ajouter une policy locale explicite avant d'autoriser une mutation.

## 7. Limites et hypothèses (V1)

- **Pas de vérification de `mission_id` actif** (étape 7 du contrat) ni d'**OPA
  Rego** (étape 8) : hors périmètre du middleware réutilisable. Le filet de
  sécurité reste le `exp` court du token (≤ 60 min). À implémenter dans le MCP
  consommateur si requis (cache 30 s côté PEP, cf. §17.10).
- **`sub`/`jti` non recoupés** avec un store local : le middleware est un PEP
  *stateless* (signature + claims). La détection de replay par `jti` est laissée
  au consommateur.
- **EdDSA non supporté** : V1 = ES256 uniquement (arbitrage mcp-mission v0.4.2).
  L'API est conçue pour être étendue à EdDSA en V1.1 sans refactor.
- **`aud` peut être une chaîne ou une liste** : les deux formes sont acceptées.
