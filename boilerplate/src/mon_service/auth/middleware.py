# -*- coding: utf-8 -*-
"""
Middlewares ASGI : authentification et logging.

Pile d'exécution (ordre) :
    LoggingMiddleware → AdminMiddleware → HealthCheckMiddleware → AuthMiddleware → MCPServer
"""

import sys
import time
import hmac
import hashlib
import collections
from datetime import datetime, timezone
from typing import Optional
from .context import current_mission_context, current_token_info, mission_context_to_token_info
from .token_store import get_token_store
from ..config import get_settings


# =============================================================================
# Ring buffer pour l'activité (utilisé par la console admin)
# =============================================================================

_activity_log = collections.deque(maxlen=200)


def get_activity_log() -> list:
    """Retourne les N dernières entrées du ring buffer d'activité."""
    return list(_activity_log)


# =============================================================================
# AuthMiddleware
# =============================================================================

class AuthMiddleware:
    """
    Middleware ASGI d'authentification par Bearer token.

    - Extrait le token du header Authorization (Bearer uniquement)
    - Valide via bootstrap key ou Token Store S3
    - Injecte les infos du token dans les contextvars
    - Les routes publiques passent sans token

    ⚠️ Les tokens via query string (?token=) sont INTERDITS (CWE-598 :
    les query strings apparaissent dans les logs, l'historique navigateur,
    les headers Referer, et les caches proxy).
    """

    PUBLIC_PATHS = {"/health", "/healthz", "/ready", "/favicon.ico"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        path = scope.get("path", "")

        # Routes publiques → pas d'auth
        if path in self.PUBLIC_PATHS:
            return await self.app(scope, receive, send)

        mission_context = scope.get("mission_context")
        token_info = None
        settings = get_settings()

        if isinstance(mission_context, dict):
            token_info = mission_context_to_token_info(mission_context)
        elif settings.starter_kit_auth_mode != "jwt":
            token = self._extract_token(scope)
            if token:
                token_info = self._validate_token(token)

        # Injecter dans le contextvar (même si None → les outils vérifieront)
        tok = current_token_info.set(token_info)
        mission_tok = current_mission_context.set(mission_context if isinstance(mission_context, dict) else None)
        try:
            await self.app(scope, receive, send)
        finally:
            current_mission_context.reset(mission_tok)
            current_token_info.reset(tok)

    def _extract_token(self, scope) -> Optional[str]:
        """Extrait le token depuis le header Authorization (Bearer uniquement)."""
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        if auth.startswith("Bearer "):
            return auth[7:]

        # ⚠️ PAS de fallback query string — les tokens ne doivent JAMAIS
        # transiter via l'URL (CWE-598: logs, Referer, cache proxy).
        return None

    def _validate_token(self, token: str) -> Optional[dict]:
        """
        Valide un token et retourne ses infos.

        Ordre de validation :
        1. Bootstrap key → admin total (comparaison constante, anti-timing)
        2. JWT / OIDC offline (si JWT_ISSUER, JWT_AUDIENCE, JWKS_FILE configurés)
           → fail-close : un token JWT malformé/expiré ne tombe PAS dans le store S3
        3. Token Store S3 (si configuré) → lookup par hash SHA-256
        """
        settings = get_settings()

        # 1. Bootstrap key → admin total (comparaison constante contre timing attacks)
        if hmac.compare_digest(token, settings.admin_bootstrap_key):
            return {
                "client_name": "admin",
                "permissions": ["admin", "read", "write"],
                "allowed_resources": [],
            }

        # 2. JWT / OIDC (si les 3 variables de config sont présentes)
        if settings.jwt_issuer and settings.jwt_audience and settings.jwks_file:
            from .jwt_validator import validate_jwt, claims_to_token_info

            claims = validate_jwt(
                token,
                issuer=settings.jwt_issuer,
                audience=settings.jwt_audience,
                jwks_file=settings.jwks_file,
            )
            if claims is not None:
                return claims_to_token_info(claims)

            # Fail-close : si le token ressemble à un JWT (header.payload.signature)
            # mais que la validation a échoué (expiré, mauvaise sig, etc.),
            # on refuse immédiatement sans laisser le hash tomber dans le store S3.
            if token.count(".") == 2:
                return None

        # 3. Token Store S3 (si configuré)
        store = get_token_store()
        if store:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            token_info = store.get_by_hash(token_hash)
            if token_info and not token_info.get("revoked", False):
                return token_info

        return None


# =============================================================================
# LoggingMiddleware (avec ring buffer pour la console admin)
# =============================================================================

class LoggingMiddleware:
    """
    Middleware ASGI de logging des requêtes HTTP.

    - Log sur stderr : méthode, path, status, durée
    - Stocke dans un ring buffer mémoire (200 entrées) pour la console admin
    """

    QUIET_PATHS = {"/health", "/healthz", "/ready"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "?")
        t0 = time.monotonic()
        status_code = 0

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = round((time.monotonic() - t0) * 1000, 1)

            # Stocker dans le ring buffer (toutes les requêtes).
            # Timestamp en ISO 8601 UTC (et non un epoch) : c'est le contrat
            # attendu par le front (parsing 'YYYY-MM-DDTHH:MM:SS'). Émettre un
            # float epoch cassait le rendu de la page Activité.
            _activity_log.append({
                "method": method,
                "path": path,
                "status": status_code,
                "duration_ms": elapsed,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # Log stderr (sauf health checks pour éviter le bruit)
            if path not in self.QUIET_PATHS:
                print(
                    f"📡 {method} {path} → {status_code} ({elapsed}ms)",
                    file=sys.stderr,
                )
