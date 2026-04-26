# -*- coding: utf-8 -*-
"""
API REST admin — Endpoints pour la console d'administration.

Tous les endpoints requièrent un Bearer token admin ou token avec permission admin.
Routage depuis AdminMiddleware pour /admin/api/*.

Routes disponibles :
    GET  /admin/api/health            — État du service (version, outils, S3)
    GET  /admin/api/whoami            — Identité du token courant
    GET  /admin/api/tokens            — Liste des tokens (admin)
    POST /admin/api/tokens            — Créer un token (admin)
    PUT  /admin/api/tokens/{prefix}   — Modifier un token (admin)
    DELETE /admin/api/tokens/{prefix} — Révoquer un token (admin)
    GET  /admin/api/logs              — Activité récente (ring buffer)
"""

import json
import hmac
import hashlib
import platform
import traceback
from pathlib import Path

from ..config import get_settings
from ..auth.token_store import get_token_store
from ..auth.middleware import get_activity_log

# Limite de taille du body HTTP (anti-OOM)
_MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB


async def handle_admin_api(scope, receive, send, mcp):
    """Routeur principal de l'API admin."""
    path = scope.get("path", "")
    method = scope.get("method", "GET")

    # --- Auth admin requise ---
    token = _extract_admin_token(scope)
    if not _is_admin(token):
        return await _json_response(send, 401, {"status": "error", "message": "Admin token required"})

    # --- Routes système (accessible à tout token admin) ---
    if path == "/admin/api/health" and method == "GET":
        return await _api_health(send, mcp)

    if path == "/admin/api/whoami" and method == "GET":
        return await _api_whoami(send, token)

    # --- Routes tokens ---
    if path == "/admin/api/tokens" and method == "GET":
        return await _api_list_tokens(send)

    if path == "/admin/api/tokens" and method == "POST":
        body = await _read_body(receive)
        return await _api_create_token(send, body)

    if path.startswith("/admin/api/tokens/") and method == "PUT":
        hash_prefix = path[len("/admin/api/tokens/"):]
        if hash_prefix and "/" not in hash_prefix:
            body = await _read_body(receive)
            return await _api_update_token(send, hash_prefix, body)

    if path.startswith("/admin/api/tokens/") and method == "DELETE":
        hash_prefix = path[len("/admin/api/tokens/"):]
        if hash_prefix and "/" not in hash_prefix:
            return await _api_revoke_token(send, hash_prefix)

    # --- Route logs / activité ---
    if path == "/admin/api/logs" and method == "GET":
        return await _api_logs(send)

    return await _json_response(send, 404, {"status": "error", "message": f"Unknown admin route: {path}"})


# =============================================================================
# Endpoints
# =============================================================================

async def _api_health(send, mcp):
    """GET /admin/api/health — État du serveur."""
    settings = get_settings()
    version = "dev"
    vf = Path(__file__).parent.parent.parent.parent / "VERSION"
    if vf.exists():
        version = vf.read_text().strip()

    tools = [t.name for t in mcp._tool_manager.list_tools()] if mcp else []

    await _json_response(send, 200, {
        "status": "ok",
        "service_name": settings.mcp_server_name,
        "version": version,
        "python_version": platform.python_version(),
        "tools_count": len(tools),
        "tools": tools,
        "s3_configured": bool(settings.s3_endpoint_url),
    })


async def _api_list_tokens(send):
    """GET /admin/api/tokens — Liste des tokens."""
    store = get_token_store()
    if not store:
        return await _json_response(send, 200, {
            "status": "ok", "tokens": [],
            "message": "S3 non configuré — seul le bootstrap key est actif"
        })
    await _json_response(send, 200, {"status": "ok", "tokens": store.list_all()})


async def _api_create_token(send, body):
    """POST /admin/api/tokens — Créer un token."""
    store = get_token_store()
    if not store:
        return await _json_response(send, 400, {
            "status": "error",
            "message": "S3 non configuré — impossible de créer des tokens"
        })

    try:
        data = json.loads(body) if body else {}
    except (json.JSONDecodeError, ValueError):
        return await _json_response(send, 400, {"status": "error", "message": "JSON invalide"})

    client_name = data.get("client_name", "").strip()
    if not client_name:
        return await _json_response(send, 400, {"status": "error", "message": "client_name requis"})

    # Validation whitelist des permissions
    permissions = data.get("permissions", ["read"])
    valid_perms = {"read", "write", "admin"}
    if not isinstance(permissions, list) or not all(p in valid_perms for p in permissions):
        return await _json_response(send, 400, {
            "status": "error",
            "message": f"Permissions invalides. Valeurs autorisées : {', '.join(sorted(valid_perms))}"
        })

    allowed_resources = data.get("allowed_resources", [])
    email = data.get("email", "")
    expires_in_days = data.get("expires_in_days", 90)

    result = store.create(
        client_name, permissions, allowed_resources,
        expires_in_days=expires_in_days, email=email,
    )
    await _json_response(send, 201, {"status": "created", **result})


async def _api_update_token(send, hash_prefix, body):
    """PUT /admin/api/tokens/{hash_prefix} — Modifier un token (permissions, ressources)."""
    store = get_token_store()
    if not store:
        return await _json_response(send, 400, {
            "status": "error", "message": "S3 non configuré"
        })

    if len(hash_prefix) < 8:
        return await _json_response(send, 400, {
            "status": "error",
            "message": "Hash prefix trop court (min 8 caractères)"
        })

    try:
        data = json.loads(body) if body else {}
    except (json.JSONDecodeError, ValueError):
        return await _json_response(send, 400, {"status": "error", "message": "JSON invalide"})

    # Champs modifiables
    permissions = data.get("permissions")
    allowed_resources = data.get("allowed_resources")

    # Validation des permissions si fournies
    if permissions is not None:
        valid_perms = {"read", "write", "admin"}
        if not isinstance(permissions, list) or not all(p in valid_perms for p in permissions):
            return await _json_response(send, 400, {
                "status": "error",
                "message": f"Permissions invalides. Valeurs autorisées : {', '.join(sorted(valid_perms))}"
            })

    if not hasattr(store, 'update'):
        # Fallback si le token store ne supporte pas update
        return await _json_response(send, 501, {
            "status": "error", "message": "update() non supporté par ce TokenStore"
        })

    result = store.update(
        hash_prefix=hash_prefix,
        permissions=permissions,
        allowed_resources=allowed_resources,
    )
    status_code = 200 if result.get("status") == "updated" else 400
    await _json_response(send, status_code, result)


async def _api_revoke_token(send, hash_prefix):
    """DELETE /admin/api/tokens/{hash_prefix} — Révoquer un token."""
    store = get_token_store()
    if not store:
        return await _json_response(send, 400, {
            "status": "error", "message": "S3 non configuré"
        })

    # ⚠️ Min 8 chars pour éviter les collisions de hash prefix
    if len(hash_prefix) < 8:
        return await _json_response(send, 400, {
            "status": "error",
            "message": "Hash prefix trop court (min 8 caractères)"
        })

    if store.revoke(hash_prefix):
        await _json_response(send, 200, {
            "status": "ok",
            "message": f"Token {hash_prefix[:12]}… révoqué"
        })
    else:
        await _json_response(send, 404, {
            "status": "error",
            "message": f"Token {hash_prefix[:12]}… non trouvé"
        })


async def _api_logs(send):
    """GET /admin/api/logs — Activité récente (ring buffer)."""
    logs = get_activity_log()
    await _json_response(send, 200, {
        "status": "ok",
        "count": len(logs),
        "logs": logs[-50:],
    })


async def _api_whoami(send, token: str):
    """GET /admin/api/whoami — Identité du token courant."""
    settings = get_settings()

    # Bootstrap key
    if hmac.compare_digest(token, settings.admin_bootstrap_key):
        return await _json_response(send, 200, {
            "status": "ok",
            "auth_type": "bootstrap",
            "client_name": "admin",
            "permissions": ["read", "write", "admin"],
            "allowed_resources": [],
        })

    # Token S3
    store = get_token_store()
    if store:
        h = hashlib.sha256(token.encode()).hexdigest()
        info = store.get_by_hash(h)
        if info and not info.get("revoked"):
            result = {
                "status": "ok",
                "auth_type": "token",
                "client_name": info.get("client_name", "?"),
                "permissions": info.get("permissions", []),
                "allowed_resources": info.get("allowed_resources", []),
                "hash_prefix": h[:12],
            }
            if info.get("email"):
                result["email"] = info["email"]
            if info.get("created_at"):
                result["created_at"] = info["created_at"]
            if info.get("expires_at"):
                result["expires_at"] = info["expires_at"]
            return await _json_response(send, 200, result)

    await _json_response(send, 401, {"status": "error", "message": "Token invalide"})


# =============================================================================
# Helpers
# =============================================================================

def _extract_admin_token(scope) -> str:
    """Extrait le Bearer token depuis les headers Authorization."""
    headers = dict(scope.get("headers", []))
    auth = headers.get(b"authorization", b"").decode()
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return ""


def _is_admin(token: str) -> bool:
    """
    Vérifie si le token est admin (bootstrap key ou token S3 avec permission admin).

    ⚠️ Utilise hmac.compare_digest() (comparaison en temps constant)
    pour éviter les timing attacks sur le bootstrap key.
    """
    if not token:
        return False
    settings = get_settings()
    # Bootstrap key — comparaison constante anti timing attack
    if hmac.compare_digest(token, settings.admin_bootstrap_key):
        return True
    # Token S3
    store = get_token_store()
    if store:
        h = hashlib.sha256(token.encode()).hexdigest()
        info = store.get_by_hash(h)
        if info and "admin" in info.get("permissions", []) and not info.get("revoked"):
            return True
    return False


async def _read_body(receive) -> bytes:
    """
    Lit le body complet d'une requête ASGI.

    ⚠️ Limite la taille à _MAX_BODY_SIZE (10 MB) pour prévenir les attaques OOM.
    """
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if len(body) > _MAX_BODY_SIZE:
            raise ValueError(f"Body trop volumineux (limite : {_MAX_BODY_SIZE // (1024*1024)} MB)")
        if not message.get("more_body", False):
            break
    return body


async def _json_response(send, status: int, data: dict):
    """
    Envoie une réponse JSON.

    ⚠️ CORS restreint — pas de Access-Control-Allow-Origin: * (same-origin uniquement).
    L'admin est servi par le même domaine que l'API, aucun CORS nécessaire.
    """
    body = json.dumps(data, default=str).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})
