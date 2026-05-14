# -*- coding: utf-8 -*-
"""
Validation JWT offline via JWKS local.

Design :
- JWKS chargé une seule fois depuis le fichier configuré (JWKS_FILE).
- Validation : signature RS256/RS512/ES256, exp, nbf, iss, aud.
- Compatible Keycloak, Entra ID (Azure AD), Okta, et tout OIDC standard.
- Fail-close : toute erreur de validation → None (accès refusé), jamais d'exception
  qui remonterait vers le client.
- Aucun secret ni claim sensible ne transite dans les logs.

⚠️  Rotation de clés : si le JWKS est mis à jour, redémarrer le service
    (le cache lru_cache est en mémoire, lié au processus).
    Pour les environnements à haute dispo, intégrer un signal SIGHUP
    qui appelle _reload_jwks().
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Vérifie la disponibilité de PyJWT au chargement du module (pas d'import dur).
try:
    import jwt as _pyjwt
    from jwt.algorithms import RSAAlgorithm, ECAlgorithm

    _PYJWT_OK = True
except ImportError:  # pragma: no cover
    _PYJWT_OK = False
    logger.warning(
        "PyJWT non installé — validation JWT désactivée. "
        "Ajouter PyJWT[cryptography]>=2.8.0 dans requirements.txt."
    )


# =============================================================================
# Chargement des clés (lru_cache = une seule lecture par process)
# =============================================================================

@lru_cache(maxsize=1)
def _load_keys(jwks_file: str) -> list[dict]:
    """
    Charge et met en cache les clés publiques depuis le fichier JWKS.

    Args:
        jwks_file: Chemin vers le fichier JWKS local.

    Returns:
        Liste de dicts JWK (champs key_type, kid, etc.)

    Raises:
        RuntimeError si le fichier est absent, illisible ou malformé.
    """
    path = Path(jwks_file)
    if not path.exists():
        raise RuntimeError(
            f"Fichier JWKS introuvable : {jwks_file}. "
            "Vérifiez que le volume est correctement monté dans le container."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Fichier JWKS invalide (JSON malformé) : {jwks_file}") from exc

    keys: list[dict] = data.get("keys", [])
    if not keys:
        raise RuntimeError(
            f"Fichier JWKS ne contient aucune clé (champ 'keys' vide) : {jwks_file}"
        )
    logger.info("JWKS chargé depuis %s — %d clé(s)", jwks_file, len(keys))
    return keys


def _reload_jwks() -> None:
    """Invalide le cache JWKS (utile après rotation de clés)."""
    _load_keys.cache_clear()
    logger.info("Cache JWKS invalidé (reload demandé)")


def _find_public_key(keys: list[dict], kid: Optional[str], alg: str):
    """
    Sélectionne et convertit la clé publique correspondant au kid/alg.

    Returns:
        Clé publique (objet RSA ou EC) ou None si non trouvée.
    """
    for key_data in keys:
        # kid explicite → correspondance stricte
        if kid is not None and key_data.get("kid") != kid:
            continue

        kty = key_data.get("kty", "")
        try:
            if kty == "RSA" and alg.startswith(("RS", "PS")):
                return RSAAlgorithm.from_jwk(json.dumps(key_data))
            if kty == "EC" and alg.startswith("ES"):
                return ECAlgorithm.from_jwk(json.dumps(key_data))
        except Exception as exc:
            logger.debug("Impossible de charger la clé JWK (kid=%s): %s", kid, type(exc).__name__)
            continue

    if kid:
        logger.warning("JWT validation: aucune clé trouvée pour kid=%r alg=%s", kid, alg)
    else:
        logger.warning("JWT validation: aucune clé RSA/EC trouvée pour alg=%s", alg)
    return None


# =============================================================================
# Validation publique
# =============================================================================

def validate_jwt(
    token: str,
    issuer: str,
    audience: str,
    jwks_file: str,
) -> Optional[dict]:
    """
    Valide un JWT et retourne ses claims si valides, None sinon.

    Vérifie :
    - Signature (RS256 / RS512 / ES256 / ES512, selon le header alg)
    - exp (token expiré → refus)
    - nbf (not before, si présent)
    - iss (issuer attendu)
    - aud (audience attendue)

    Politique de log :
    - On log le TYPE d'erreur (Expired, InvalidAudience…), jamais le token.
    - On ne révèle jamais si c'est la signature ou l'audience qui a échoué
      dans les messages renvoyés au client (seul le log interne le sait).

    Args:
        token:      Bearer token extrait du header Authorization.
        issuer:     Valeur attendue du claim `iss`.
        audience:   Valeur attendue du claim `aud`.
        jwks_file:  Chemin vers le fichier JWKS local.

    Returns:
        Dict des claims si le token est valide, None sinon.
    """
    if not _PYJWT_OK:
        return None

    # Chargement des clés (depuis le cache lru_cache)
    try:
        keys = _load_keys(jwks_file)
    except RuntimeError as exc:
        # Erreur fatale de config — on log clairement pour l'ops/SRE
        logger.error("JWT config error: %s", exc)
        return None

    # Lecture du header sans vérification (pour extraire kid/alg)
    try:
        header = _pyjwt.get_unverified_header(token)
    except _pyjwt.exceptions.DecodeError:
        logger.warning("JWT validation: header illisible (token malformé)")
        return None

    kid = header.get("kid")
    alg = header.get("alg", "RS256")

    # Trouver la clé publique correspondante
    public_key = _find_public_key(keys, kid, alg)
    if public_key is None:
        return None

    # Décodage + validation complète
    try:
        claims = _pyjwt.decode(
            token,
            public_key,
            algorithms=[alg],
            audience=audience,
            issuer=issuer,
            options={
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
        logger.debug(
            "JWT valid — sub=%s, iss=%s",
            claims.get("sub", "?"),
            claims.get("iss", "?"),
        )
        return claims

    except _pyjwt.exceptions.ExpiredSignatureError:
        logger.info("JWT validation: token expiré (exp dépassé)")
        return None
    except _pyjwt.exceptions.InvalidAudienceError:
        logger.warning("JWT validation: audience invalide")
        return None
    except _pyjwt.exceptions.InvalidIssuerError:
        logger.warning("JWT validation: issuer invalide")
        return None
    except _pyjwt.exceptions.InvalidSignatureError:
        logger.warning("JWT validation: signature invalide")
        return None
    except _pyjwt.exceptions.DecodeError as exc:
        logger.warning("JWT validation: decode error (%s)", type(exc).__name__)
        return None
    except Exception as exc:  # noqa: BLE001
        # Filet de sécurité — ne jamais laisser une exception non gérée remonter
        logger.error("JWT validation: erreur inattendue (%s)", type(exc).__name__)
        return None


# =============================================================================
# Conversion claims → token_info (format interne du middleware)
# =============================================================================

def claims_to_token_info(claims: dict) -> dict:
    """
    Convertit les claims JWT en token_info compatible avec le middleware auth.

    Format retourné :
        {
            "client_name": str,         # preferred_username ou sub
            "permissions": list[str],   # ["read"] | ["read","write"] | ["admin","read","write"]
            "allowed_resources": [],    # [] = accès à toutes les ressources du rôle
            "jwt_sub": str,             # claim sub (pour traçabilité)
        }

    Mapping de rôles (Keycloak) :
        realm_access.roles  +  resource_access.<client>.roles → permissions MCP

    TODO: Adapter le mapping ci-dessous selon votre politique RBAC et votre IdP.
          Les noms de rôles (mcp-admin, mcp-write, mcp-read) sont des exemples.
    """
    # Identifiant lisible (pour les logs et le outil system_whoami)
    client_name: str = (
        claims.get("preferred_username")
        or claims.get("email")
        or claims.get("sub", "jwt-unknown")
    )

    # Agréger tous les rôles disponibles dans les claims
    roles: set[str] = set()

    # Keycloak : rôles du realm
    realm_access = claims.get("realm_access") or {}
    roles.update(realm_access.get("roles", []))

    # Keycloak : rôles des ressources (tous les clients configurés)
    resource_access = claims.get("resource_access") or {}
    for _client, resource in resource_access.items():
        roles.update(resource.get("roles", []))

    # Microsoft Entra ID : rôles dans le claim "roles"
    roles.update(claims.get("roles", []))

    # Mapping rôles → permissions MCP
    # Ordre d'évaluation : le plus permissif d'abord
    permissions: list[str]
    if roles & {"mcp-admin", "admin", "service_account"}:
        permissions = ["admin", "read", "write"]
    elif roles & {"mcp-write", "write", "editor"}:
        permissions = ["read", "write"]
    elif roles & {"mcp-read", "read", "viewer"}:
        permissions = ["read"]
    else:
        # Aucun rôle reconnu → read-only (fail-safe conservateur)
        logger.info(
            "JWT token_info: aucun rôle MCP reconnu pour sub=%s (roles=%s) → read-only",
            claims.get("sub", "?"),
            sorted(roles)[:5],   # au plus 5 rôles dans le log (éviter un log trop verbeux)
        )
        permissions = ["read"]

    return {
        "client_name": client_name,
        "permissions": permissions,
        "allowed_resources": [],
        "jwt_sub": claims.get("sub"),
    }
