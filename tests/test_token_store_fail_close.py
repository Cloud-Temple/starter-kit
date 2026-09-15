# -*- coding: utf-8 -*-
"""Comportement du magasin de tokens quand S3 est en panne.

Le défaut corrigé ici : `load()` et `_save()` avalaient toute erreur en
imprimant un avertissement. Conséquences observées, dans l'ordre de gravité,
une création répondait 201 avec un token que personne n'avait persisté, une
révocation répondait succès sans rien révoquer, et une panne de lecture se
présentait comme un magasin vide donc comme un token inconnu.

Chaque test ci-dessous échoue sur le code d'avant le correctif.
"""

import io
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("ADMIN_BOOTSTRAP_KEY", "test-bootstrap-key")
os.environ.setdefault("MCP_SERVER_NAME", "starter-kit-test")

from mon_service.auth.token_store import (  # noqa: E402
    S3TokenStore,
    TokenStoreUnavailable,
)
from mon_service.admin import api as admin_api  # noqa: E402
from mon_service.config import get_settings  # noqa: E402


@dataclass
class DummySettings:
    s3_region_name: str = "fr1"
    s3_signature_version: str = "s3"
    s3_addressing_style: str = "path"
    s3_endpoint_url: str = "http://s3.example.test"
    s3_access_key_id: str = "key"
    s3_secret_access_key: str = "secret"
    s3_bucket_name: str = "bucket"
    token_store_fail_mode: str = "fail_close"
    token_store_stale_grace: int = 300


class FakeS3:
    """Client S3 minimal, capable de tomber en panne à la demande."""

    def __init__(self, get_error=None, put_error=None, payload=None):
        self.get_error = get_error
        self.put_error = put_error
        self.payload = payload if payload is not None else {"tokens": []}
        self.get_calls = 0
        self.put_calls = 0

    def get_object(self, Bucket, Key):  # noqa: N803 (signature boto3)
        self.get_calls += 1
        if self.get_error:
            raise self.get_error
        return {"Body": io.BytesIO(json.dumps(self.payload).encode())}

    def put_object(self, **kwargs):
        self.put_calls += 1
        if self.put_error:
            raise self.put_error


def make_store(**kwargs):
    settings = DummySettings(**{k: v for k, v in kwargs.items() if k.startswith("token_store")})
    store = S3TokenStore(settings)
    fake = FakeS3(**{k: v for k, v in kwargs.items() if not k.startswith("token_store")})
    store._s3_client = fake
    return store, fake


def a_token(hash_value="a" * 64, revoked=False):
    return {
        "hash": hash_value,
        "client_name": "agent",
        "permissions": ["read"],
        "allowed_resources": [],
        "policy_id": "",
        "email": "",
        "created_at": "2026-01-01T00:00:00+00:00",
        "expires_at": None,
        "revoked": revoked,
    }


# --- Lecture -----------------------------------------------------------------

def test_une_panne_de_lecture_est_levee_et_ne_vieillit_pas_le_cache():
    store, fake = make_store(get_error=RuntimeError("S3 Connection timeout"))

    with pytest.raises(TokenStoreUnavailable):
        store.load()

    assert fake.get_calls == 1
    # L'horodatage du cache ne doit pas bouger : sinon la panne se présente
    # comme un chargement réussi d'un magasin vide.
    assert store._cache_time == 0
    assert store._tokens == {}


def test_un_objet_absent_est_un_magasin_vide_pas_une_panne():
    store, _ = make_store(get_error=RuntimeError("NoSuchKey: the key does not exist"))

    store.load()

    assert store._tokens == {}
    assert store._cache_time > 0


# --- Écriture ----------------------------------------------------------------

def test_une_creation_pendant_une_panne_de_lecture_n_ecrit_rien():
    store, fake = make_store(get_error=RuntimeError("S3 Connection timeout"))

    with pytest.raises(TokenStoreUnavailable):
        store.create(client_name="agent", permissions=["read"])

    assert fake.put_calls == 0
    assert store._tokens == {}


def test_une_creation_dont_l_ecriture_echoue_n_expose_aucun_token():
    store, fake = make_store(put_error=RuntimeError("S3 503 SlowDown"))

    with pytest.raises(TokenStoreUnavailable):
        store.create(client_name="agent", permissions=["read"])

    assert fake.put_calls == 1
    # Le token ne doit pas non plus survivre en mémoire : il serait valide sur
    # cette instance et inconnu de toutes les autres.
    assert store._tokens == {}


def test_une_revocation_dont_l_ecriture_echoue_n_annonce_pas_un_succes():
    token = a_token()
    store, fake = make_store(put_error=RuntimeError("S3 503 SlowDown"),
                             payload={"tokens": [token]})

    with pytest.raises(TokenStoreUnavailable):
        store.revoke(token["hash"][:12])

    assert fake.put_calls == 1


def test_une_mutation_recharge_avant_d_ecrire():
    """Écrire depuis un cache périmé perdrait les tokens créés ailleurs."""
    existant = a_token(hash_value="b" * 64)
    store, fake = make_store(payload={"tokens": [existant]})
    store._tokens = {}
    store._cache_time = time.time()  # cache frais mais faux

    store.create(client_name="agent", permissions=["read"])

    persistes = {t["hash"] for t in store._tokens.values()}
    assert existant["hash"] in persistes
    assert fake.get_calls == 1


# --- Cache périmé ------------------------------------------------------------

def _store_en_panne_avec_cache(age_offset):
    token = a_token()
    store, fake = make_store(get_error=RuntimeError("S3 Connection timeout"))
    store._tokens = {token["hash"]: token}
    store._cache_time = time.time() - (S3TokenStore.CACHE_TTL + age_offset)
    return store, fake, token


def test_un_cache_perime_reste_servi_dans_la_fenetre_bornee():
    store, _, token = _store_en_panne_avec_cache(10)

    assert store.get_by_hash(token["hash"]) == token


def test_un_cache_perime_au_dela_de_la_fenetre_refuse_l_acces():
    store, _, token = _store_en_panne_avec_cache(DummySettings.token_store_stale_grace + 10)

    with pytest.raises(TokenStoreUnavailable):
        store.get_by_hash(token["hash"])


def test_fail_open_sert_le_cache_perime_quand_il_est_demande_explicitement():
    token = a_token()
    store, _ = make_store(get_error=RuntimeError("S3 Connection timeout"),
                          token_store_fail_mode="fail_open")
    store._tokens = {token["hash"]: token}
    store._cache_time = time.time() - (S3TokenStore.CACHE_TTL + 86400)

    assert store.get_by_hash(token["hash"]) == token


def test_le_backoff_evite_de_marteler_s3_pendant_la_panne():
    store, fake, _ = _store_en_panne_avec_cache(10)

    for _ in range(5):
        store.get_by_hash("inconnu")

    # Un seul appel réel : les suivants tombent dans la fenêtre de backoff.
    assert fake.get_calls == 1


# --- Traduction HTTP ---------------------------------------------------------

class StoreEnPanne:
    def list_all(self):
        raise TokenStoreUnavailable("magasin injoignable")

    def create(self, *args, **kwargs):
        raise TokenStoreUnavailable("magasin injoignable")

    def get_by_hash(self, token_hash):
        return None


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def call_admin(method, path, body=None):
    payload = json.dumps(body or {}).encode() if body is not None else b""
    sent = []
    consomme = False

    async def receive():
        nonlocal consomme
        if consomme:
            return {"type": "http.request", "body": b"", "more_body": False}
        consomme = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"authorization", b"Bearer test-bootstrap-key")],
    }
    await admin_api.handle_admin_api(scope, receive, send, None)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(raw.decode())


@pytest.mark.asyncio
async def test_une_creation_pendant_une_panne_ne_repond_plus_201(monkeypatch):
    monkeypatch.setattr(admin_api, "get_token_store", lambda: StoreEnPanne())

    status, data = await call_admin("POST", "/admin/api/tokens",
                                    {"client_name": "agent", "permissions": ["read"]})

    assert status == 502
    assert data["error"] == "token_store_unavailable"
    assert "raw_token" not in json.dumps(data)


@pytest.mark.asyncio
async def test_une_lecture_pendant_une_panne_repond_503(monkeypatch):
    monkeypatch.setattr(admin_api, "get_token_store", lambda: StoreEnPanne())

    status, data = await call_admin("GET", "/admin/api/tokens")

    assert status == 503
    assert data["error"] == "token_store_unavailable"
