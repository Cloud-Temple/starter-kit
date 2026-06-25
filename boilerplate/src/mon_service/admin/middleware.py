# -*- coding: utf-8 -*-
"""
AdminMiddleware ASGI — Console d'administration web (/admin).

Intercepte les routes /admin, /admin/static/*, /admin/api/*
AVANT l'auth MCP (l'admin gère sa propre auth Bearer admin).

Pattern identique à MCP Tools / MCP Vault.
"""

import os
import json
import mimetypes
from pathlib import Path


class AdminMiddleware:
    """
    Middleware ASGI outermost — sert la console admin et l'API REST.

    Routes interceptées :
        GET  /admin             → SPA HTML (admin.html)
        GET  /admin/static/*    → Fichiers statiques (CSS, JS, images)
        *    /admin/api/*       → API REST admin (délègue à api.py)
        Tout le reste           → Passe au middleware suivant (MCP)
    """

    # En-têtes de sécurité de la console admin.
    # CSP : `script-src 'self'` interdit tout script/handler INLINE (onerror=,
    # onclick=…) → neutralise le XSS stocké même si une donnée non échappée
    # passait (defense-in-depth). Possible car la console n'utilise plus de
    # handlers inline (délégation data-action). `style-src` garde 'unsafe-inline'
    # (styles inline résiduels ; risque CSS très inférieur au script).
    _SECURITY_HEADERS = [
        (b"content-security-policy",
         b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
         b"img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"),
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
    ]

    def __init__(self, app, mcp_instance=None):
        self.app = app
        self.mcp = mcp_instance
        self.static_dir = Path(__file__).parent.parent / "static"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        # --- CORS preflight ---
        if method == "OPTIONS" and path.startswith("/admin/api/"):
            return await self._cors_response(send)

        # --- PUT tokens ---
        if method == "PUT" and path.startswith("/admin/api/tokens/"):
            from .api import handle_admin_api
            return await handle_admin_api(scope, receive, send, self.mcp)

        # --- SPA HTML ---
        if path in ("/admin", "/admin/"):
            return await self._serve_file(send, "admin.html", "text/html")

        # --- Fichiers statiques ---
        if path.startswith("/admin/static/"):
            rel = path[len("/admin/static/"):]
            # Protection path traversal rapide (double-slash, séquences ..)
            if ".." in rel or "//" in rel:
                return await self._error(send, 403, "Forbidden")
            return await self._serve_file(send, rel)

        # --- API REST admin ---
        if path.startswith("/admin/api/"):
            from .api import handle_admin_api
            return await handle_admin_api(scope, receive, send, self.mcp)

        # --- Tout le reste → middleware suivant ---
        return await self.app(scope, receive, send)

    async def _serve_file(self, send, filename, content_type=None):
        """Sert un fichier statique depuis le répertoire static/.

        ⚠️ Protection path traversal : on vérifie que le chemin résolu
        est bien sous static_dir (pas de `../../etc/passwd`).
        """
        filepath = (self.static_dir / filename).resolve()
        static_root = self.static_dir.resolve()

        # Le fichier doit être sous static_dir
        if not str(filepath).startswith(str(static_root)):
            return await self._error(send, 403, "Forbidden")

        if not filepath.exists():
            return await self._error(send, 404, f"Not found: {filename}")

        if content_type is None:
            content_type = mimetypes.guess_type(str(filepath))[0] or "application/octet-stream"

        body = filepath.read_bytes()
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", content_type.encode()),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-cache"),
                *self._SECURITY_HEADERS,
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def _cors_response(self, send):
        """Répond aux preflight CORS OPTIONS (same-origin uniquement)."""
        # ⚠️ L'admin est servi par le même domaine que l'API.
        # Pas besoin de wildcard * — ça exposerait l'API admin
        # à des requêtes cross-origin malveillantes.
        await send({
            "type": "http.response.start",
            "status": 204,
            "headers": [
                (b"access-control-allow-methods", b"GET, POST, PUT, DELETE, OPTIONS"),
                (b"access-control-allow-headers", b"Authorization, Content-Type"),
                (b"access-control-max-age", b"3600"),
            ],
        })
        await send({"type": "http.response.body", "body": b""})

    async def _error(self, send, status, message):
        """Retourne une erreur JSON."""
        body = json.dumps({"status": "error", "message": message}).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
