# -*- coding: utf-8 -*-
"""
Tests du middleware mission_token : `AuthMissionJWTMiddleware`.

Ces tests frappent le VRAI middleware et la VRAIE validation ES256 :

- de vraies clés EC P-256 sont générées à la volée (aucun secret en dur) ;
- de vrais JWT mission_token sont signés/altérés et soumis au middleware via
  des objets ASGI scope/receive/send factices ;
- le fetch JWKS est injecté par un faux HTTP (pas de réseau), ce qui permet de
  tester ETag/304, TTL, backoff exponentiel et fail-close de façon déterministe.

Contrat source : Cloud-Temple/mcp-mission ARCHITECTURE §17.10/§17.12/§21.8.7
et l'issue Cloud-Temple/starter-kit#14.
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Doit être posé AVANT le premier get_settings() : l'endpoint admin de reload
# réutilise le contrôle d'auth admin du starter-kit (admin.api._is_admin), qui
# lit le singleton get_settings(). On aligne donc le bootstrap key global.
os.environ["ADMIN_BOOTSTRAP_KEY"] = "test-bootstrap-key"
os.environ.setdefault("MCP_SERVER_NAME", "starter-kit-test")

import jwt as pyjwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

from mon_service.config import Settings  # noqa: E402
from mon_service.infra.auth_mission_jwt_middleware import (  # noqa: E402
    AuthMissionJWTMiddleware,
    JWKSCache,
    JWKSUnavailable,
    MissionTokenForbidden,
    MissionTokenInvalid,
    validate_mission_token,
)


# =============================================================================
# Fixtures cryptographiques — clés EC P-256 réelles, signature ES256 réelle
# =============================================================================

INSTANCE_ID = "vault-prod-eu-tenant-acme"
COMPONENT_KIND = "vault"
ISS = "mcp-mission"
JWKS_URL = "https://mcp-mission.internal/.well-known/jwks.json"


def _new_ec_keypair():
    """Génère une paire de clés EC P-256 (privée PEM, JWK public)."""
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_jwk = json.loads(
        pyjwt.algorithms.ECAlgorithm.to_jwk(priv.public_key())
    )
    return priv_pem, pub_jwk


def _jwk_with(kid: str, pub_jwk: dict) -> dict:
    jwk = dict(pub_jwk)
    jwk.update({"kid": kid, "use": "sig", "alg": "ES256"})
    return jwk


def _jwks_body(*jwks: dict) -> bytes:
    return json.dumps({"keys": list(jwks)}).encode()


def _make_token(priv_pem: bytes, kid: str, *, claims: dict, alg: str = "ES256") -> str:
    """Signe un mission_token avec la clé privée fournie."""
    return pyjwt.encode(claims, priv_pem, algorithm=alg, headers={"kid": kid})


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": ISS,
        "sub": "mission:mis_a3f8b2c1",
        "aud": [INSTANCE_ID, "teleport-prod-eu-tenant-acme"],
        "iat": now,
        "exp": now + 3600,
        "jti": "01HMC1234567890ABCDEFGHIJK",
        "mission_id": "mis_a3f8b2c1",
        "template_id": "server-update",
        "tenant_id": "tenant_acme",
        "component_id": {
            "vault": INSTANCE_ID,
            "teleport": "teleport-prod-eu-tenant-acme",
        },
        "scope": [f"{INSTANCE_ID}:mission/mis_a3f8b2c1:*"],
        "provenance": [{"actor": "mcp-mission", "via": "broker"}],
    }
    claims.update(overrides)
    return claims


class FakeClock:
    """Horloge monotone contrôlée (pour TTL/backoff déterministes)."""

    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeHTTP:
    """
    Faux transport HTTP pour le JWKS, programmable.

    `responses` est une liste de callables `(url, etag, timeout) -> (status, etag, body)`
    ou d'exceptions à lever. Chaque appel consomme l'élément suivant ; le dernier
    est répété si la liste est épuisée.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, etag, timeout):
        self.calls.append({"url": url, "if_none_match": etag})
        item = self.responses[0] if len(self.responses) == 1 else self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(url, etag, timeout)


def _ok_200(body: bytes, etag: str = '"v1"'):
    return lambda url, inm, timeout: (200, etag, body)


def _not_modified_304():
    return lambda url, inm, timeout: (304, inm, None)


# =============================================================================
# Helpers ASGI
# =============================================================================

def _settings(**overrides) -> Settings:
    base = {
        "admin_bootstrap_key": "test-bootstrap-key",
        "starter_kit_auth_mode": "jwt",
        "mcp_instance_id": INSTANCE_ID,
        "mcp_component_kind": COMPONENT_KIND,
        "mcp_mission_jwks_url": JWKS_URL,
    }
    base.update(overrides)
    return Settings(**base)


def _scope(token: str | None = None, *, path: str = "/mcp", method: str = "POST") -> dict:
    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode("latin-1")))
    return {"type": "http", "path": path, "method": method, "headers": headers}


class Captured:
    """Capture la réponse ASGI émise (ou l'absence = passage au middleware suivant)."""

    def __init__(self):
        self.status = None
        self.body = b""
        self.passed_through = False
        self.scope_seen = None


async def _run(middleware, scope) -> Captured:
    cap = Captured()

    async def downstream(s, receive, send):
        cap.passed_through = True
        cap.scope_seen = s

    middleware.app = downstream

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            cap.status = message["status"]
        elif message["type"] == "http.response.body":
            cap.body += message.get("body", b"")

    await middleware(scope, receive, send)
    return cap


def _body_json(cap: Captured) -> dict:
    return json.loads(cap.body.decode()) if cap.body else {}


# =============================================================================
# Tests d'acceptation — issue #14 / §21.8.7
# =============================================================================

@pytest.mark.asyncio
async def test_es256_p256_jwks_token_is_accepted():
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    token = _make_token(priv, "k1", claims=_base_claims())
    cap = await _run(mw, _scope(token))

    assert cap.passed_through is True
    assert cap.status is None  # aucune réponse d'erreur émise
    assert cap.scope_seen["mission_context"]["mission_id"] == "mis_a3f8b2c1"


@pytest.mark.asyncio
async def test_malformed_jwt_returns_401():
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    # 3 segments séparés par des points mais base64/contenu invalides.
    cap = await _run(mw, _scope("aaa.bbb.ccc"))

    assert cap.passed_through is False
    assert cap.status == 401


@pytest.mark.asyncio
async def test_invalid_signature_returns_401():
    priv_signer, _ = _new_ec_keypair()
    _, pub_other = _new_ec_keypair()  # JWKS publie une AUTRE clé
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub_other)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    # Token signé avec une clé dont la publique n'est PAS dans le JWKS (kid=k1).
    token = _make_token(priv_signer, "k1", claims=_base_claims())
    cap = await _run(mw, _scope(token))

    assert cap.passed_through is False
    assert cap.status == 401


@pytest.mark.asyncio
async def test_unknown_kid_returns_401():
    priv, pub = _new_ec_keypair()
    fetch = FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))])
    cache = JWKSCache(JWKS_URL, 300, fetch=fetch)
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    # Token avec kid inconnu du JWKS → refus (après tentative de refresh).
    token = _make_token(priv, "kid-revoked", claims=_base_claims())
    cap = await _run(mw, _scope(token))

    assert cap.passed_through is False
    assert cap.status == 401


@pytest.mark.asyncio
async def test_alg_none_is_rejected():
    """Un token alg=none (non signé) ne doit JAMAIS être accepté."""
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    unsigned = pyjwt.encode(_base_claims(), key=None, algorithm="none", headers={"kid": "k1"})
    cap = await _run(mw, _scope(unsigned))

    assert cap.passed_through is False
    assert cap.status == 401


@pytest.mark.asyncio
async def test_rs256_token_is_rejected():
    """Anti confusion d'algorithme : un RS256 ne doit pas passer pour un ES256."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    _, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    token = pyjwt.encode(_base_claims(), rsa_pem, algorithm="RS256", headers={"kid": "k1"})
    cap = await _run(mw, _scope(token))

    assert cap.passed_through is False
    assert cap.status == 401


@pytest.mark.asyncio
async def test_wrong_audience_returns_403():
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    # aud ne contient PAS l'instance configurée.
    claims = _base_claims(aud=["teleport-prod-eu-tenant-acme", "live-mem-acme"])
    token = _make_token(priv, "k1", claims=claims)
    cap = await _run(mw, _scope(token))

    assert cap.passed_through is False
    assert cap.status == 403


@pytest.mark.asyncio
async def test_component_id_mismatch_returns_403():
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    # aud OK mais component_id[vault] pointe vers une AUTRE instance.
    claims = _base_claims(component_id={"vault": "vault-prod-eu-tenant-OTHER"})
    token = _make_token(priv, "k1", claims=claims)
    cap = await _run(mw, _scope(token))

    assert cap.passed_through is False
    assert cap.status == 403


@pytest.mark.asyncio
async def test_expired_token_returns_401():
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    now = int(time.time())
    claims = _base_claims(iat=now - 7200, exp=now - 3600)  # expiré (hors grâce, grâce=0 côté PEP)
    token = _make_token(priv, "k1", claims=claims)
    cap = await _run(mw, _scope(token))

    assert cap.passed_through is False
    assert cap.status == 401


@pytest.mark.asyncio
async def test_iat_future_beyond_skew_returns_401():
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    now = int(time.time())
    # iat dans le futur bien au-delà du leeway (60s par défaut).
    claims = _base_claims(iat=now + 600, exp=now + 4200)
    token = _make_token(priv, "k1", claims=claims)
    cap = await _run(mw, _scope(token))

    assert cap.passed_through is False
    assert cap.status == 401


# =============================================================================
# JWKS cache : TTL, ETag/304, backoff, fail-close
# =============================================================================

def test_jwks_cache_ttl_respected():
    priv, pub = _new_ec_keypair()
    clock = FakeClock()
    fetch = FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))])
    cache = JWKSCache(JWKS_URL, ttl_seconds=300, fetch=fetch, time_func=clock)

    # 1er accès → 1 fetch.
    cache.get_key("k1")
    assert len(fetch.calls) == 1

    # Toujours dans le TTL → pas de nouveau fetch.
    clock.advance(299)
    cache.get_key("k1")
    assert len(fetch.calls) == 1

    # TTL dépassé → nouveau fetch.
    clock.advance(2)
    cache.get_key("k1")
    assert len(fetch.calls) == 2


def test_jwks_cache_uses_etag_if_available():
    priv, pub = _new_ec_keypair()
    clock = FakeClock()
    body = _jwks_body(_jwk_with("k1", pub))
    fetch = FakeHTTP([_ok_200(body, etag='"abc"'), _not_modified_304()])
    cache = JWKSCache(JWKS_URL, ttl_seconds=100, fetch=fetch, time_func=clock)

    cache.get_key("k1")
    assert fetch.calls[0]["if_none_match"] is None  # 1er fetch sans ETag

    # TTL expiré → revalidation conditionnelle avec l'ETag mémorisé → 304.
    clock.advance(101)
    cache.get_key("k1")
    assert fetch.calls[1]["if_none_match"] == '"abc"'
    # Le 304 doit prolonger la fraîcheur : pas de nouveau fetch immédiat.
    cache.get_key("k1")
    assert len(fetch.calls) == 2


def test_jwks_fetch_backoff_exponential_on_failure(monkeypatch):
    import mon_service.infra.auth_mission_jwt_middleware as mod

    # Neutralise le jitter pour un test déterministe.
    monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0.0)

    clock = FakeClock()
    import urllib.error
    err = urllib.error.URLError("boom")
    # Le 1er fetch peuple le cache ; ensuite tous les fetchs échouent.
    priv, pub = _new_ec_keypair()
    fetch = FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub))), err])
    cache = JWKSCache(JWKS_URL, ttl_seconds=10, fetch=fetch, time_func=clock)

    cache.get_key("k1")  # peuple le cache
    base_calls = len(fetch.calls)

    # Force l'expiration puis observe les délais de backoff : 1, 2, 4, 8, 16, 30 (cap).
    expected = [1, 2, 4, 8, 16, 30, 30]
    for delay in expected:
        clock.advance(11)  # cache expiré (TTL 10) ET fenêtre de backoff ouverte
        with pytest.raises(JWKSUnavailable):
            cache.get_key("k1")
        # Juste avant la fin du backoff : pas de nouvelle tentative réseau.
        calls_after_attempt = len(fetch.calls)
        clock.advance(delay - 0.5)
        with pytest.raises(JWKSUnavailable):
            cache.get_key("k1")
        assert len(fetch.calls) == calls_after_attempt, f"fetch tenté pendant backoff {delay}s"


@pytest.mark.asyncio
async def test_fail_close_when_cache_expired_and_fetch_fails():
    import urllib.error

    priv, pub = _new_ec_keypair()
    clock = FakeClock()
    fetch = FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub))), urllib.error.URLError("down")])
    cache = JWKSCache(JWKS_URL, ttl_seconds=10, fetch=fetch, time_func=clock)
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    # Cache peuplé au 1er token (accepté).
    token = _make_token(priv, "k1", claims=_base_claims())
    cap = await _run(mw, _scope(token))
    assert cap.passed_through is True

    # Cache expiré + fetch KO → fail-close 503 (PAS de cache périmé servi).
    clock.advance(50)
    token2 = _make_token(priv, "k1", claims=_base_claims())
    cap2 = await _run(mw, _scope(token2))
    assert cap2.passed_through is False
    assert cap2.status == 503


@pytest.mark.asyncio
async def test_claims_mapped_to_request_scope_mission_context():
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    token = _make_token(priv, "k1", claims=_base_claims())
    cap = await _run(mw, _scope(token))

    ctx = cap.scope_seen["mission_context"]
    for field in ("aud", "component_id", "scope", "mission_id", "jti", "tenant_id", "template_id", "provenance"):
        assert field in ctx, f"mission_context doit exposer {field}"
    assert ctx["mission_id"] == "mis_a3f8b2c1"
    assert ctx["tenant_id"] == "tenant_acme"
    assert ctx["jti"] == "01HMC1234567890ABCDEFGHIJK"


# =============================================================================
# Dual-stack / migration P1-P7
# =============================================================================

@pytest.mark.asyncio
async def test_dual_stack_mode_respects_per_mcp_config():
    """En dual-stack (P3-P7), un Bearer opaque est délégué au middleware suivant."""
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(
        None, settings=_settings(starter_kit_auth_mode="dual-stack"), jwks_cache=cache
    )

    # Bearer opaque (non-JWT) → fallback legacy (passe au middleware suivant).
    cap = await _run(mw, _scope("opaque-legacy-token-xyz"))
    assert cap.passed_through is True
    assert "mission_context" not in cap.scope_seen

    # Mais un JWT présenté en dual-stack reste validé (et ici accepté).
    token = _make_token(priv, "k1", claims=_base_claims())
    cap2 = await _run(mw, _scope(token))
    assert cap2.passed_through is True
    assert cap2.scope_seen["mission_context"]["mission_id"] == "mis_a3f8b2c1"


@pytest.mark.asyncio
async def test_dual_stack_invalid_jwt_does_not_fall_back():
    """Sécurité : un JWT invalide en dual-stack ne doit PAS retomber sur le Bearer legacy."""
    _, pub_other = _new_ec_keypair()
    priv_signer, _ = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub_other)))]))
    mw = AuthMissionJWTMiddleware(
        None, settings=_settings(starter_kit_auth_mode="dual-stack"), jwks_cache=cache
    )

    token = _make_token(priv_signer, "k1", claims=_base_claims())  # signature KO
    cap = await _run(mw, _scope(token))
    assert cap.passed_through is False
    assert cap.status == 401


@pytest.mark.asyncio
async def test_p1_p2_refuse_legacy_bearer_for_mission_calls():
    """P1/P2 (mode jwt) : un Bearer opaque legacy est REFUSÉ (dual-stack interdit)."""
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(starter_kit_auth_mode="jwt"), jwks_cache=cache)

    cap = await _run(mw, _scope("opaque-legacy-token-xyz"))
    assert cap.passed_through is False
    assert cap.status == 401


@pytest.mark.asyncio
async def test_dual_stack_truncated_jwt_does_not_fall_back():
    """
    Sécurité (revue Codex) : un JWT tronqué (2 segments 'header.payload') NE
    doit PAS être traité comme un Bearer opaque et délégué au legacy. Tout token
    contenant un '.' est une tentative mission_token → validation stricte → refus.
    """
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(
        None, settings=_settings(starter_kit_auth_mode="dual-stack"), jwks_cache=cache
    )

    full = _make_token(priv, "k1", claims=_base_claims())
    truncated = ".".join(full.split(".")[:2])  # 2 segments seulement
    cap = await _run(mw, _scope(truncated))
    assert cap.passed_through is False  # PAS de fallback legacy
    assert cap.status == 401


@pytest.mark.asyncio
async def test_websocket_denied_with_close_not_http_response():
    """
    Un refus sur scope WebSocket doit émettre un `websocket.close` (code 1008),
    jamais un `http.response.*` (violation du protocole ASGI → revue Codex).
    """
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(starter_kit_auth_mode="jwt"), jwks_cache=cache)

    sent = []

    async def downstream(s, receive, send):
        sent.append({"type": "passed_through"})

    mw.app = downstream

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    ws_scope = {"type": "websocket", "path": "/mcp", "headers": []}
    await mw(ws_scope, receive, send)

    assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.asyncio
async def test_token_with_non_string_aud_returns_401_not_500():
    """Un token signé mais `aud` de type invalide → 401 (jamais un crash 500)."""
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    # aud = entier (forgé côté émetteur compromis / bug).
    token = _make_token(priv, "k1", claims=_base_claims(aud=12345))
    cap = await _run(mw, _scope(token))
    assert cap.passed_through is False
    assert cap.status == 401


# =============================================================================
# Endpoint admin de reload JWKS
# =============================================================================

@pytest.mark.asyncio
async def test_admin_jwks_reload_invalidates_cache():
    priv, pub = _new_ec_keypair()
    priv2, pub2 = _new_ec_keypair()
    clock = FakeClock()
    # 1er JWKS publie k1 ; après reload, le serveur publie k2 (rotation).
    fetch = FakeHTTP([
        _ok_200(_jwks_body(_jwk_with("k1", pub)), etag='"v1"'),
        _ok_200(_jwks_body(_jwk_with("k2", pub2)), etag='"v2"'),
    ])
    cache = JWKSCache(JWKS_URL, ttl_seconds=99999, fetch=fetch, time_func=clock)
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    # Token k1 accepté tant que le cache contient k1.
    t1 = _make_token(priv, "k1", claims=_base_claims())
    cap = await _run(mw, _scope(t1))
    assert cap.passed_through is True

    # Reload admin (avec bootstrap key) → force refresh → JWKS = {k2}.
    reload_scope = _scope("test-bootstrap-key", path="/admin/auth/jwks/reload", method="POST")
    cap_reload = await _run(mw, reload_scope)
    assert cap_reload.status == 200
    assert _body_json(cap_reload)["keys"] == 1

    # k1 n'est plus publié → token k1 refusé (kid inconnu) malgré le grand TTL.
    cap2 = await _run(mw, _scope(t1))
    assert cap2.passed_through is False
    assert cap2.status == 401

    # Un token k2 est désormais accepté.
    t2 = _make_token(priv2, "k2", claims=_base_claims())
    cap3 = await _run(mw, _scope(t2))
    assert cap3.passed_through is True


@pytest.mark.asyncio
async def test_admin_jwks_reload_requires_admin_token():
    """Le reload est privilégié : refus sans token admin valide."""
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    mw = AuthMissionJWTMiddleware(None, settings=_settings(), jwks_cache=cache)

    # Sans Authorization.
    cap = await _run(mw, _scope(None, path="/admin/auth/jwks/reload", method="POST"))
    assert cap.status == 401

    # Avec un Bearer non-admin.
    cap2 = await _run(mw, _scope("not-the-admin-key", path="/admin/auth/jwks/reload", method="POST"))
    assert cap2.status == 401


# =============================================================================
# Validation unitaire directe (sans ASGI)
# =============================================================================

def test_validate_mission_token_happy_path_returns_claims():
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    claims = validate_mission_token(
        _make_token(priv, "k1", claims=_base_claims()),
        cache,
        instance_id=INSTANCE_ID,
        component_kind=COMPONENT_KIND,
        iat_leeway=60,
    )
    assert claims["mission_id"] == "mis_a3f8b2c1"


def test_validate_rejects_wrong_issuer():
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    with pytest.raises(MissionTokenInvalid):
        validate_mission_token(
            _make_token(priv, "k1", claims=_base_claims(iss="evil-idp")),
            cache,
            instance_id=INSTANCE_ID,
            component_kind=COMPONENT_KIND,
            iat_leeway=60,
        )


def test_validate_forbidden_on_audience():
    priv, pub = _new_ec_keypair()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(_jwks_body(_jwk_with("k1", pub)))]))
    with pytest.raises(MissionTokenForbidden):
        validate_mission_token(
            _make_token(priv, "k1", claims=_base_claims(aud=["someone-else"])),
            cache,
            instance_id=INSTANCE_ID,
            component_kind=COMPONENT_KIND,
            iat_leeway=60,
        )


def test_jwks_parse_ignores_non_es256_keys():
    """Un JWKS contenant des clés RSA/OKP ne retient que les EC P-256."""
    _, pub = _new_ec_keypair()
    body = json.dumps({"keys": [
        {"kty": "RSA", "kid": "rsa1", "n": "x", "e": "AQAB"},
        {"kty": "OKP", "kid": "okp1", "crv": "Ed25519", "x": "y"},
        _jwk_with("ec1", pub),
    ]}).encode()
    cache = JWKSCache(JWKS_URL, 300, fetch=FakeHTTP([_ok_200(body)]))
    # ec1 trouvable, rsa1 absent.
    assert cache.get_key("ec1")["kid"] == "ec1"
    with pytest.raises(MissionTokenInvalid):
        cache.get_key("rsa1")


# =============================================================================
# Validation de configuration (fail-close au boot — revue Codex)
# =============================================================================

def test_config_requires_mission_vars_in_jwt_mode():
    with pytest.raises(ValueError):
        Settings(starter_kit_auth_mode="jwt")  # variables mission manquantes


def test_config_rejects_non_https_jwks_url_for_routable_host():
    with pytest.raises(ValueError):
        _settings(mcp_mission_jwks_url="http://mcp-mission.example.com/.well-known/jwks.json")


def test_config_rejects_file_scheme_jwks_url():
    with pytest.raises(ValueError):
        _settings(mcp_mission_jwks_url="file:///etc/passwd")


def test_config_rejects_http_ipv6_routable_host():
    # IPv6 routable en http : pas de point mais des deux-points → doit être refusé
    # (sinon bypass de l'heuristique 'nom de service court'). Revue Codex passe 2.
    with pytest.raises(ValueError):
        _settings(mcp_mission_jwks_url="http://[2001:db8::1]/.well-known/jwks.json")


def test_config_rejects_http_ipv4_routable_host():
    with pytest.raises(ValueError):
        _settings(mcp_mission_jwks_url="http://203.0.113.10/.well-known/jwks.json")


def test_config_allows_http_for_internal_docker_host():
    # Nom de service Docker court (sans point) → http toléré pour le dev/interne.
    s = _settings(mcp_mission_jwks_url="http://mcp-mission/.well-known/jwks.json")
    assert s.mcp_mission_jwks_url.startswith("http://mcp-mission")


def test_config_allows_https():
    s = _settings(mcp_mission_jwks_url="https://mcp-mission.internal/.well-known/jwks.json")
    assert s.mcp_mission_jwks_url.startswith("https://")
