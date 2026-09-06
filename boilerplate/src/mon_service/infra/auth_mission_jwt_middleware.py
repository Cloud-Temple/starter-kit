# -*- coding: utf-8 -*-
"""
AuthMissionJWTMiddleware — validation du `mission_token` (PEP côté MCP).

Ce middleware ASGI permet à n'importe quel MCP Cloud Temple de valider, AVANT
exécution, un `mission_token` : un JWT signé **ES256** (ECDSA P-256 + SHA-256)
émis par `mcp-mission`, via le JWKS public de l'émetteur. Il matérialise le
« PEP » (Policy Enforcement Point) décrit dans
`Cloud-Temple/mcp-mission` ARCHITECTURE §17.10 (enforcement d'instance en 9
étapes) et §17.12 (plan de migration dual-stack des PEPs).

Garanties de sécurité (fail-CLOSE par défaut) :

- Signature **ES256** uniquement (`kty=EC`, `crv=P-256`, `alg=ES256`). Tout autre
  algorithme est refusé (anti `alg=none`, anti confusion d'algo RS↔ES).
- Sélection de clé par `kid` : un `kid` inconnu/révoqué (absent du JWKS) → refus.
- Vérification `exp` (grâce 0 côté PEP — le refresh est géré côté agent),
  `iat` anti-skew, `mission_id`, `jti`, `scope`, `aud` contient
  `MCP_INSTANCE_ID`, `component_id[<kind>] == MCP_INSTANCE_ID`.
- JWKS récupéré dynamiquement avec **cache TTL**, support **ETag/`304 Not
  Modified`**, **backoff exponentiel** + jitter en cas d'échec de fetch.
- **Fail-close** : si le cache est expiré ET le fetch JWKS échoue → HTTP 503
  (jamais de fallback sur un cache périmé, jamais de fallback Bearer en mode
  `jwt`).
- Aucun token ni claim sensible n'est journalisé (seul le TYPE d'erreur l'est).

Le middleware s'insère dans la pile ASGI en amont de l'`AuthMiddleware` Bearer
legacy (cf. §17.12) :

    Logging → Admin → HealthCheck → AuthMissionJWT → AuthBearer(legacy) → MCPServer

Modes (`STARTER_KIT_AUTH_MODE`) :

- ``bearer``      : middleware inactif (cf. `create_app` — non inséré).
- ``jwt``         : SEUL le mission_token JWT est accepté ; tout le reste est
                    refusé (P1/P2 — vault, teleport — dual-stack INTERDIT).
- ``dual-stack``  : JWT validé s'il est présent ; sinon délégation au Bearer
                    legacy (P3-P7 — migration progressive).
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from ..config import get_settings

logger = logging.getLogger(__name__)

# Vérifie la disponibilité de PyJWT au chargement (pas d'import dur — le
# middleware n'est activé que si la dépendance est présente).
try:
    import jwt as _pyjwt
    from jwt.algorithms import ECAlgorithm

    _PYJWT_OK = True
except ImportError:  # pragma: no cover
    _PYJWT_OK = False
    logger.warning(
        "PyJWT non installé — AuthMissionJWTMiddleware désactivé. "
        "Ajouter PyJWT[cryptography]>=2.8.0 dans requirements.txt."
    )


# Algorithme et type de clé imposés par le contrat mission_token V1 (§17.10).
_ALG = "ES256"
_KTY = "EC"
_CRV = "P-256"
_ISS = "mcp-mission"

# Backoff exponentiel sur échec de fetch JWKS (§17.10 : 1, 2, 4, 8 … max 30s).
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 30.0
_BACKOFF_JITTER = 0.20  # ±20 %


# =============================================================================
# Erreurs internes (jamais propagées telles quelles vers le client)
# =============================================================================

class JWKSUnavailable(Exception):
    """Le JWKS est indisponible ET le cache est expiré → fail-close 503."""


class MissionTokenInvalid(Exception):
    """Token absent, malformé, signature/exp/iat invalides → 401."""


class MissionTokenForbidden(Exception):
    """Token authentique mais `aud`/`component_id` non conforme → 403."""


# =============================================================================
# Fetch HTTP du JWKS (injectable pour les tests — aucun secret en jeu)
# =============================================================================

def _default_http_fetch(url: str, etag: Optional[str], timeout: float) -> tuple[int, Optional[str], Optional[bytes]]:
    """
    Récupère le document JWKS via HTTP GET conditionnel.

    Args:
        url:     URL du JWKS (`/.well-known/jwks.json`).
        etag:    Valeur d'ETag connue → envoyée en `If-None-Match` (revalidation).
        timeout: Timeout réseau en secondes.

    Returns:
        (status_code, etag_renvoyé, corps_bytes).
        Sur 304, le corps est None (le cache reste valide).

    Raises:
        urllib.error.URLError / OSError sur erreur réseau (gérée par l'appelant).
    """
    request = urllib.request.Request(url, method="GET")  # noqa: S310 (URL = config opérateur)
    request.add_header("Accept", "application/json")
    if etag:
        request.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            status = resp.getcode()
            resp_etag = resp.headers.get("ETag")
            body = resp.read()
            return status, resp_etag, body
    except urllib.error.HTTPError as exc:  # 304, 4xx, 5xx
        if exc.code == 304:
            return 304, etag, None
        # Toute autre erreur HTTP est un échec de fetch (déclenche le backoff).
        raise


# =============================================================================
# Cache JWKS — TTL + ETag + backoff exponentiel + fail-close
# =============================================================================

class JWKSCache:
    """
    Cache thread-safe du JWKS de mcp-mission.

    - Récupère le JWKS via HTTP conditionnel (ETag/If-None-Match → 304).
    - Sert depuis le cache tant qu'il est frais (`now < fetched_at + ttl`).
    - Re-fetch à expiration ; sur succès → nouveau document + nouvel ETag.
    - Sur échec : backoff exponentiel (jitter), et tant que le cache reste frais
      il continue de servir l'ancien JWKS. **Mais** si le cache est EXPIRÉ et que
      le fetch échoue → `JWKSUnavailable` (fail-close, pas de cache périmé servi).
    - Recharge manuelle immédiate via `force_reload()` (endpoint admin).

    Aucun secret n'est manipulé : un JWKS ne contient que des clés PUBLIQUES.
    """

    def __init__(
        self,
        url: str,
        ttl_seconds: int,
        *,
        fetch: Callable[[str, Optional[str], float], tuple[int, Optional[str], Optional[bytes]]] = _default_http_fetch,
        timeout: float = 5.0,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._fetch = fetch
        self._timeout = timeout
        self._now = time_func
        self._lock = threading.Lock()

        # État du cache.
        self._keys_by_kid: dict[str, dict] = {}
        self._etag: Optional[str] = None
        self._fetched_at: Optional[float] = None  # None = jamais peuplé

        # État du backoff.
        self._next_attempt_at: float = 0.0
        self._fail_count: int = 0

    # -- API publique -------------------------------------------------------

    def get_key(self, kid: str) -> dict:
        """
        Retourne le JWK (dict) correspondant au `kid`, en rafraîchissant le
        cache si nécessaire.

        Raises:
            JWKSUnavailable    : cache expiré + impossible de (re)fetch.
            MissionTokenInvalid: JWKS disponible mais `kid` inconnu/révoqué.
        """
        with self._lock:
            self._ensure_fresh_locked()
            key = self._keys_by_kid.get(kid)
            if key is None:
                # kid absent du JWKS courant : peut être une rotation très récente.
                # On tente un refresh forcé UNE fois (hors fenêtre de backoff)
                # avant de conclure à un kid révoqué/inconnu.
                if self._fetched_at is not None and self._can_attempt_locked():
                    self._refetch_locked(force=True)
                    key = self._keys_by_kid.get(kid)
            if key is None:
                # kid réellement inconnu → token non authentique (clé révoquée
                # ou jamais publiée). Refus, sans révéler la liste des kid connus.
                raise MissionTokenInvalid("unknown_kid")
            return key

    def force_reload(self) -> int:
        """
        Force un refresh JWKS immédiat (réinitialise le backoff).

        Utilisé par l'endpoint admin `POST /admin/auth/jwks/reload` pour
        propager une révocation urgente de `kid` sans attendre le TTL.

        Returns:
            Nombre de clés chargées après reload.

        Raises:
            JWKSUnavailable si le fetch échoue.
        """
        with self._lock:
            self._fail_count = 0
            self._next_attempt_at = 0.0
            self._refetch_locked(force=True)
            return len(self._keys_by_kid)

    # -- Interne (appelé sous _lock) ---------------------------------------

    def _is_fresh_locked(self) -> bool:
        if self._fetched_at is None:
            return False
        return self._now() < self._fetched_at + self._ttl

    def _can_attempt_locked(self) -> bool:
        """True si la fenêtre de backoff autorise une nouvelle tentative."""
        return self._now() >= self._next_attempt_at

    def _ensure_fresh_locked(self) -> None:
        """
        Garantit un cache exploitable, ou lève `JWKSUnavailable` (fail-close).

        - Cache frais → ne touche pas au réseau.
        - Cache expiré/absent → tente un fetch (si la fenêtre de backoff le
          permet). Sur succès le cache est rafraîchi.
        - Si après tentative le cache reste inexploitable (jamais peuplé, ou
          expiré) → fail-close.
        """
        if self._is_fresh_locked():
            return

        if self._can_attempt_locked():
            try:
                self._refetch_locked(force=False)
            except JWKSUnavailable:
                # Échec géré plus bas : on décide selon l'état du cache.
                pass

        # Décision fail-close : on n'accepte de servir QUE si le cache est frais.
        # Un cache expiré n'est JAMAIS servi (pas de fallback sur cache périmé).
        if not self._is_fresh_locked():
            raise JWKSUnavailable("jwks_unavailable")

    def _refetch_locked(self, *, force: bool) -> None:
        """
        Effectue le fetch HTTP (conditionnel ETag) et met à jour le cache.

        `force=True` ignore la fenêtre de backoff (reload admin, retry kid).
        Sur échec, programme le prochain essai (backoff exponentiel + jitter)
        et lève `JWKSUnavailable`.
        """
        if not force and not self._can_attempt_locked():
            raise JWKSUnavailable("jwks_backoff")

        try:
            status, etag, body = self._fetch(self._url, self._etag, self._timeout)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._schedule_backoff_locked()
            logger.warning(
                "JWKS fetch échec (%s) — backoff #%d ~%.1fs",
                type(exc).__name__, self._fail_count, self._backoff_delay_locked(),
            )
            raise JWKSUnavailable("jwks_fetch_error") from exc

        if status == 304:
            # Document inchangé : on prolonge la fraîcheur du cache existant.
            if self._fetched_at is None:
                # 304 sans cache initial : anomalie serveur → fail-close.
                self._schedule_backoff_locked()
                raise JWKSUnavailable("jwks_304_without_cache")
            self._fetched_at = self._now()
            self._reset_backoff_locked()
            logger.debug("JWKS 304 Not Modified — cache prolongé (TTL %ds)", self._ttl)
            return

        if status != 200 or not body:
            self._schedule_backoff_locked()
            raise JWKSUnavailable(f"jwks_http_{status}")

        try:
            keys = self._parse_jwks(body)
        except ValueError as exc:
            # JWKS malformé : on ne corrompt PAS le cache existant.
            self._schedule_backoff_locked()
            logger.warning("JWKS malformé (%s) — backoff", type(exc).__name__)
            raise JWKSUnavailable("jwks_malformed") from exc

        self._keys_by_kid = keys
        self._etag = etag
        self._fetched_at = self._now()
        self._reset_backoff_locked()
        logger.info("JWKS rafraîchi depuis %s — %d clé(s) ES256/P-256", self._url, len(keys))

    @staticmethod
    def _parse_jwks(body: bytes) -> dict[str, dict]:
        """
        Parse un document JWKS et ne retient QUE les clés ES256/P-256 valides.

        Refuse silencieusement (ignore) les clés d'un autre type/courbe/algo —
        on n'accepte jamais RSA, OKP ou une courbe non P-256 dans ce contexte.

        Raises:
            ValueError si le JSON est invalide ou ne contient aucune clé exploitable.
        """
        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("invalid_json") from exc

        raw_keys = data.get("keys")
        if not isinstance(raw_keys, list):
            raise ValueError("missing_keys")

        keys_by_kid: dict[str, dict] = {}
        for jwk in raw_keys:
            if not isinstance(jwk, dict):
                continue
            kid = jwk.get("kid")
            if not kid:
                continue
            # Filtre strict : seules les clés EC P-256 destinées à la signature
            # ES256 sont retenues (anti confusion d'algorithme).
            if jwk.get("kty") != _KTY or jwk.get("crv") != _CRV:
                continue
            if jwk.get("use") not in (None, "sig"):
                continue
            if jwk.get("alg") not in (None, _ALG):
                continue
            keys_by_kid[kid] = jwk

        if not keys_by_kid:
            raise ValueError("no_es256_keys")
        return keys_by_kid

    def _backoff_delay_locked(self) -> float:
        exp = min(
            _BACKOFF_MAX_SECONDS,
            _BACKOFF_BASE_SECONDS * (2 ** max(0, self._fail_count - 1)),
        )
        jitter = exp * _BACKOFF_JITTER
        return max(0.0, exp + random.uniform(-jitter, jitter))

    def _schedule_backoff_locked(self) -> None:
        self._fail_count += 1
        self._next_attempt_at = self._now() + self._backoff_delay_locked()

    def _reset_backoff_locked(self) -> None:
        self._fail_count = 0
        self._next_attempt_at = 0.0


# =============================================================================
# Validation des claims du mission_token
# =============================================================================

def validate_mission_token(
    token: str,
    jwks_cache: JWKSCache,
    *,
    instance_id: str,
    component_kind: str,
    iat_leeway: int,
    now: Optional[float] = None,
) -> dict:
    """
    Valide un mission_token et retourne ses claims (séquence §17.10, étapes 1-6).

    Étapes appliquées (fail-close à chaque étape) :
      1. Extraction du header → `kid`, `alg`. `alg` doit être ES256.
      2. Sélection de la clé publique via `kid` (JWKS, refus si inconnu).
      3. Vérification de la signature ES256 + `exp` (grâce 0).
      4. Vérification `iat <= now + leeway` (anti-skew d'horloge avancée).
      5. Vérification `mission_id`, `jti`, `scope`.
      6. `aud` contient `instance_id` (refus 403 sinon).
      7. `component_id[component_kind] == instance_id` (refus 403 sinon).

    Raises:
        MissionTokenInvalid   : token absent/malformé/signature/exp/iat/iss/kid.
        MissionTokenForbidden : token authentique mais aud/component_id non conforme.
        JWKSUnavailable       : JWKS indisponible (propagée → 503 par le middleware).
    """
    if not _PYJWT_OK:  # pragma: no cover
        raise MissionTokenInvalid("pyjwt_missing")

    if not token or token.count(".") != 2:
        raise MissionTokenInvalid("not_a_jwt")

    # 1. Header → kid + alg (sans vérification de signature).
    try:
        header = _pyjwt.get_unverified_header(token)
    except _pyjwt.PyJWTError:
        raise MissionTokenInvalid("bad_header")

    if header.get("alg") != _ALG:
        # Refus catégorique de tout algo ≠ ES256 (anti alg=none, anti RS/ES mix).
        raise MissionTokenInvalid("bad_alg")

    kid = header.get("kid")
    if not kid:
        raise MissionTokenInvalid("missing_kid")

    # 2. Clé publique correspondante (lève JWKSUnavailable ou MissionTokenInvalid).
    jwk = jwks_cache.get_key(kid)
    try:
        public_key = ECAlgorithm.from_jwk(json.dumps(jwk))
    except Exception as exc:  # noqa: BLE001 — JWK corrompu = token non vérifiable
        logger.warning("JWK ES256 illisible pour kid (%s)", type(exc).__name__)
        raise MissionTokenInvalid("bad_jwk")

    # 3. Décodage + signature + exp. `aud` est vérifié manuellement à l'étape 5
    #    (le contrat exige « contient instance_id », ce que fait déjà PyJWT, mais
    #    on garde la main pour renvoyer un 403 distinct du 401).
    try:
        claims = _pyjwt.decode(
            token,
            public_key,
            algorithms=[_ALG],
            issuer=_ISS,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": False,   # vérifié manuellement (anti-skew futur)
                "verify_aud": False,   # vérifié manuellement (403 distinct)
                "require": ["exp", "iat", "iss", "aud", "mission_id", "jti", "scope"],
            },
            leeway=0,  # grâce 0 côté PEP — le refresh est géré côté agent (§17.10)
        )
    except _pyjwt.ExpiredSignatureError:
        logger.info("mission_token expiré (exp dépassé)")
        raise MissionTokenInvalid("expired")
    except _pyjwt.InvalidIssuerError:
        raise MissionTokenInvalid("bad_iss")
    except _pyjwt.MissingRequiredClaimError as exc:
        logger.warning("mission_token claim requis manquant (%s)", exc.claim)
        raise MissionTokenInvalid("missing_claim")
    except _pyjwt.InvalidSignatureError:
        logger.warning("mission_token signature invalide")
        raise MissionTokenInvalid("bad_signature")
    except _pyjwt.PyJWTError as exc:
        logger.warning("mission_token rejeté (%s)", type(exc).__name__)
        raise MissionTokenInvalid("invalid")

    # 4. Anti-skew sur iat : un token daté dans le futur au-delà du leeway est suspect.
    ref = time.time() if now is None else now
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or iat > ref + iat_leeway:
        logger.warning("mission_token iat dans le futur au-delà du skew toléré")
        raise MissionTokenInvalid("iat_future")

    # 5. Les claims de traçabilité/enforcement mission sont obligatoires et typés.
    mission_id = claims.get("mission_id")
    if not isinstance(mission_id, str) or not mission_id:
        logger.warning("mission_token mission_id de type invalide")
        raise MissionTokenInvalid("bad_mission_id")

    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti:
        logger.warning("mission_token jti de type invalide")
        raise MissionTokenInvalid("bad_jti")

    scope = claims.get("scope")
    if not isinstance(scope, list) or not scope or not all(isinstance(s, str) and s for s in scope):
        logger.warning("mission_token scope de type invalide")
        raise MissionTokenInvalid("bad_scope")

    # 6. aud doit contenir l'identifiant d'instance de CE MCP (T03/T11).
    #    `aud` est soit une str, soit une list[str] (RFC 7519). Toute autre forme
    #    est un token malformé → 401 (et JAMAIS un crash 500 sur input forgé).
    aud = claims.get("aud")
    if isinstance(aud, str):
        aud_values = [aud]
    elif isinstance(aud, list) and all(isinstance(a, str) for a in aud):
        aud_values = aud
    else:
        logger.warning("mission_token aud de type invalide")
        raise MissionTokenInvalid("bad_aud")
    if instance_id not in aud_values:
        logger.warning("mission_token aud ne contient pas l'instance configurée")
        raise MissionTokenForbidden("wrong_audience")

    # 7. component_id[<kind>] doit correspondre à l'instance configurée.
    component_id = claims.get("component_id")
    if not isinstance(component_id, dict) or component_id.get(component_kind) != instance_id:
        logger.warning("mission_token component_id ne correspond pas à l'instance/kind configurés")
        raise MissionTokenForbidden("component_id_mismatch")

    return claims


def claims_to_mission_context(claims: dict) -> dict:
    """
    Projette les claims validés vers `request.scope["mission_context"]`.

    Expose UNIQUEMENT les claims utiles aux handlers MCP et aux contrôles OPA
    (cf. §17.10 « mapping des claims »), sans recopier l'intégralité du token.
    """
    return {
        "aud": claims.get("aud"),
        "component_id": claims.get("component_id"),
        "scope": claims.get("scope"),
        "mission_id": claims.get("mission_id"),
        "jti": claims.get("jti"),
        "tenant_id": claims.get("tenant_id"),
        "template_id": claims.get("template_id"),
        "provenance": claims.get("provenance"),
    }


# =============================================================================
# Middleware ASGI
# =============================================================================

class AuthMissionJWTMiddleware:
    """
    Middleware ASGI de validation du mission_token (PEP côté MCP).

    Ordre attendu dans la pile (cf. §17.12) : inséré juste en amont de
    l'`AuthMiddleware` Bearer legacy.

    Politique selon `STARTER_KIT_AUTH_MODE` :
      - ``jwt``        : SEUL le mission_token est accepté. Un Bearer opaque
                         (non-JWT) sur une route protégée → 401 (dual-stack
                         interdit, P1/P2). Un JWT invalide → 401/403/503.
      - ``dual-stack`` : si l'Authorization ressemble à un JWT (3 segments) on
                         le valide ; sinon on délègue au middleware suivant
                         (Bearer legacy, P3-P7).
      - ``bearer``     : ce middleware ne devrait pas être instancié (cf.
                         `create_app`) ; par sûreté il délègue tout.

    Endpoint admin : ``POST /admin/auth/jwks/reload`` force un refresh JWKS
    immédiat (200 / 503). Réservé aux requêtes déjà autorisées en amont par
    l'AdminMiddleware — ce middleware ne réimplémente pas l'auth admin.
    """

    ADMIN_RELOAD_PATH = "/admin/auth/jwks/reload"

    # Routes publiques : jamais d'enforcement mission_token (alignées sur
    # AuthMiddleware.PUBLIC_PATHS + le JWKS et le refresh côté mcp-mission).
    PUBLIC_PATHS = {
        "/health", "/healthz", "/ready", "/favicon.ico",
        "/.well-known/jwks.json",
    }

    def __init__(self, app, *, settings=None, jwks_cache: Optional[JWKSCache] = None):
        self.app = app
        self._settings = settings or get_settings()
        self._mode = self._settings.starter_kit_auth_mode
        self._instance_id = self._settings.mcp_instance_id
        self._component_kind = self._settings.mcp_component_kind
        self._iat_leeway = self._settings.mission_jwt_iat_leeway_seconds

        if jwks_cache is not None:
            self._jwks = jwks_cache
        elif self._mode != "bearer" and self._settings.mcp_mission_jwks_url:
            self._jwks = JWKSCache(
                url=self._settings.mcp_mission_jwks_url,
                ttl_seconds=self._settings.jwks_cache_ttl_seconds,
            )
        else:
            self._jwks = None

    async def __call__(self, scope, receive, send):
        scope_type = scope["type"]
        if scope_type not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        path = scope.get("path", "")

        # Endpoint admin de reload (HTTP uniquement ; l'auth admin est gérée
        # localement ici, cf. _handle_admin_reload).
        if scope_type == "http" and path == self.ADMIN_RELOAD_PATH and scope.get("method") == "POST":
            return await self._handle_admin_reload(scope, send)

        # Mode bearer pur OU cache non configuré → délégation totale.
        if self._mode == "bearer" or self._jwks is None:
            return await self.app(scope, receive, send)

        # Routes publiques → pas d'enforcement (HTTP seulement ; les chemins
        # publics ci-dessus sont des routes HTTP).
        if scope_type == "http" and path in self.PUBLIC_PATHS:
            return await self.app(scope, receive, send)

        token = self._extract_bearer(scope)

        # Discriminant JWT vs Bearer legacy : un Bearer opaque legacy ne contient
        # JAMAIS de point. Tout token contenant un '.' est donc une TENTATIVE de
        # mission_token et DOIT être validé strictement (un JWT tronqué/cassé,
        # ex. 2 segments, ne doit pas retomber sur le legacy → pas de fail-open).
        looks_like_jwt = bool(token) and "." in token

        if not looks_like_jwt:
            # Pas de tentative JWT (token absent ou opaque legacy).
            if self._mode == "dual-stack":
                # P3-P7 : fallback Bearer legacy (middleware suivant).
                return await self.app(scope, receive, send)
            # Mode jwt pur (P1/P2) : Bearer legacy interdit pour les appels mission.
            return await self._deny(scope_type, send, 401, "mission_token_required")

        # Une tentative de mission_token est présentée → validation stricte dans
        # TOUS les modes. En dual-stack, un JWT invalide ne retombe PAS sur le
        # Bearer legacy (pas de fail-open).
        try:
            claims = validate_mission_token(
                token,
                self._jwks,
                instance_id=self._instance_id,
                component_kind=self._component_kind,
                iat_leeway=self._iat_leeway,
            )
        except JWKSUnavailable:
            # Cache expiré + JWKS injoignable → fail-close.
            return await self._deny(scope_type, send, 503, "jwks_unavailable")
        except MissionTokenForbidden:
            return await self._deny(scope_type, send, 403, "forbidden")
        except MissionTokenInvalid:
            return await self._deny(scope_type, send, 401, "invalid_mission_token")

        # Injection du contexte mission validé dans le scope ASGI.
        scope["mission_context"] = claims_to_mission_context(claims)
        return await self.app(scope, receive, send)

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _extract_bearer(scope) -> Optional[str]:
        """
        Extrait le token `Authorization: Bearer <token>`.

        - Schéma insensible à la casse (RFC 7235).
        - Décodage `latin-1` : ne lève jamais sur des octets arbitraires (un
          Authorization forgé ne doit pas provoquer de 500).
        - Jamais via query string (CWE-598).
        """
        for key, value in scope.get("headers", []):
            if key == b"authorization":
                auth = value.decode("latin-1")
                scheme, _, rest = auth.partition(" ")
                if scheme.casefold() == "bearer":
                    return rest.strip() or None
                return None
        return None

    async def _handle_admin_reload(self, scope, send) -> None:
        """
        Force un refresh JWKS (POST /admin/auth/jwks/reload).

        Opération privilégiée : réservée à un token admin (bootstrap key ou
        token S3 avec permission `admin`). On réutilise le contrôle d'auth admin
        existant du starter-kit (comparaison constante anti-timing).
        """
        if not self._is_admin_request(scope):
            return await self._reject(send, 401, "admin_token_required")
        if self._jwks is None:
            return await self._reject(send, 503, "jwks_not_configured")
        try:
            count = self._jwks.force_reload()
        except JWKSUnavailable:
            return await self._reject(send, 503, "jwks_unavailable")
        await self._json(send, 200, {"status": "ok", "keys": count})

    def _is_admin_request(self, scope) -> bool:
        """Vrai si la requête porte un token admin valide (réutilise admin.api)."""
        token = self._extract_bearer(scope)
        if not token:
            return False
        # Import paresseux pour éviter un cycle d'import au chargement du module.
        from ..admin.api import _is_admin
        return _is_admin(token)

    async def _deny(self, scope_type: str, send, status: int, error: str) -> None:
        """
        Refuse une requête en respectant le protocole ASGI du scope.

        - HTTP      → réponse JSON d'erreur (fail-close).
        - WebSocket → fermeture propre (`websocket.close`, code 1008 Policy
          Violation) ; émettre des messages `http.response.*` sur un scope
          WebSocket serait une violation du protocole ASGI (risque de 500).
        """
        if scope_type == "websocket":
            # Codes : 1008 = Policy Violation (auth refusée), 1011 = erreur serveur (503).
            code = 1011 if status == 503 else 1008
            await send({"type": "websocket.close", "code": code})
            return
        await self._reject(send, status, error)

    async def _reject(self, send, status: int, error: str) -> None:
        """Réponse HTTP d'erreur fail-close (corps minimal, sans oracle d'attaque)."""
        await self._json(send, status, {"status": "error", "error": error})

    @staticmethod
    async def _json(send, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
