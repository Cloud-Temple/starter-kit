# -*- coding: utf-8 -*-
"""Comportement du magasin de tokens quand S3 est en panne.

Le défaut corrigé ici : `load()` et `_save()` avalaient toute erreur en
imprimant un avertissement. Conséquences observées, dans l'ordre de gravité,
une création répondait 201 avec un token que personne n'avait persisté, une
révocation répondait succès sans rien révoquer, et une panne de lecture se
présentait comme un magasin vide donc comme un token inconnu.

Une écriture qui échoue est ambiguë : le magasin a pu l'appliquer et perdre sa
réponse en route. La règle retenue est de garder localement l'état le plus
restrictif, et de marquer le cache à revalider.

Preuves par mutation, mesurées sur ces 30 tests et reproductibles. Chaque
défaut est réintroduit par une modification précise, et la suite tombe ainsi :

    mutation appliquée au code corrigé                      rouges
    -----------------------------------------------------   ------
    `load()` ravale sa levée, l'avalement d'origine           7/30
    `_persist_or_restore` n'annule rien et ne marque rien     7/30
    `_save()` ravale sa levée                                 6/30
    `handle_admin_api` n'attrape plus TokenStoreUnavailable   4/30
    `_guard_stale` ne lève jamais                             3/30
    une révocation ratée est annulée en mémoire               3/30
    une écriture douteuse ne marque plus le cache             3/30
    `CACHE_TTL` replie un `0` configuré sur 300               2/30
    l'API admin rend de nouveau `str(e)` dans le corps        2/30
    `_maybe_refresh` ignore le drapeau de revalidation        1/30
    les mutations ne sont plus sérialisées                    1/30
    `_is_missing_object` accepte de nouveau un statut 404 nu  1/30

Aucun de ces chiffres n'est une estimation, et aucune mutation ne passe
inaperçue. Reproduire : appliquer une mutation au fichier source, relancer
`pytest tests/test_token_store_fail_close.py`, compter, annuler.

Limite connue et non couverte ici : sans écriture conditionnelle côté magasin,
deux instances peuvent encore s'écraser mutuellement. Le verrou vérifié plus
bas ne sérialise que ce process.
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

from mon_service.auth import token_store as ts  # noqa: E402
from mon_service.auth.token_store import (  # noqa: E402
    S3TokenStore,
    TokenStoreUnavailable,
    VaultTokenStore,
    _load_at_startup,
    get_token_store_status,
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
    token_store_cache_ttl: int = 300


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


class ObjetAbsent(Exception):
    """Erreur boto3 réaliste : le code vit dans response, pas dans le message."""
    response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class PanneDeProxy(Exception):
    """Une passerelle en panne peut renvoyer un 404 sans que l'objet soit absent."""

    response = {"Error": {"Code": ""}, "ResponseMetadata": {"HTTPStatusCode": 404}}


def test_un_objet_absent_est_un_magasin_vide_pas_une_panne():
    store, _ = make_store(get_error=ObjetAbsent("the specified key does not exist"))

    store.load()

    assert store._tokens == {}
    assert store._cache_time > 0


def test_un_404_sans_code_s3_n_est_pas_un_objet_absent():
    """Ni le texte de l'erreur ni le statut HTTP seul ne prouvent l'absence.

    Une passerelle en panne renvoie un 404 structuré sans code S3 ; le lire
    comme un magasin vide transformerait la panne en « aucun token connu ».
    """
    store, _ = make_store(get_error=PanneDeProxy("gateway returned HTTP 404 during outage"))

    with pytest.raises(TokenStoreUnavailable):
        store.load()

    assert store._cache_time == 0


def test_le_ttl_du_cache_vient_de_la_configuration():
    store, _ = make_store(token_store_cache_ttl=60)

    assert store.CACHE_TTL == 60


def test_un_ttl_a_zero_est_respecte_et_non_replie_sur_le_defaut():
    """`TOKEN_STORE_CACHE_TTL=0` veut dire « jamais de cache », pas « 300s ».

    Le repli `int(...) or 300` rendait à l'exploitant la fenêtre de révocation
    de cinq minutes qu'il venait précisément de refuser.
    """
    store, _ = make_store(token_store_cache_ttl=0)

    assert store.CACHE_TTL == 0


def test_un_ttl_a_zero_refuse_l_acces_des_que_le_magasin_tombe():
    token = a_token()
    store, _ = make_store(get_error=RuntimeError("S3 Connection timeout"),
                          token_store_cache_ttl=0, token_store_stale_grace=0)
    store._tokens = {token["hash"]: token}
    store._cache_time = time.time() - 1

    with pytest.raises(TokenStoreUnavailable):
        store.get_by_hash(token["hash"])


def test_un_ttl_illisible_retombe_sur_le_defaut():
    store, _ = make_store(token_store_cache_ttl="pas-un-entier")

    assert store.CACHE_TTL == store.DEFAULT_CACHE_TTL


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


def test_une_elevation_de_permissions_refusee_n_est_pas_conservee_en_memoire():
    """Un 502 rendu à l'admin ne doit pas laisser les droits accordés ici."""
    token = a_token()
    token["permissions"] = ["read"]
    store, _ = make_store(put_error=RuntimeError("S3 503 SlowDown"),
                          payload={"tokens": [token]})

    with pytest.raises(TokenStoreUnavailable):
        store.update(token["hash"][:12], permissions=["read", "write", "admin"])

    assert store._tokens[token["hash"]]["permissions"] == ["read"]


def test_une_revocation_refusee_garde_le_refus_en_memoire():
    """Une écriture qui échoue est ambiguë : la requête a pu aboutir sans sa réponse.

    Annuler la révocation rendrait le token valide ici alors que le magasin le
    donne peut-être pour mort. On garde donc l'état le plus restrictif, et on
    marque le cache à revalider.
    """
    token = a_token()
    store, _ = make_store(put_error=RuntimeError("S3 503 SlowDown"),
                          payload={"tokens": [token]})

    with pytest.raises(TokenStoreUnavailable):
        store.revoke(token["hash"][:12])

    assert store._tokens[token["hash"]]["revoked"] is True
    assert store._needs_reload is True


def test_une_ecriture_appliquee_dont_la_reponse_se_perd_n_est_pas_annulee():
    """Le cas reproduit par la revue indépendante : le PUT aboutit, le client lève."""
    token = a_token()
    store, fake = make_store(payload={"tokens": [token]})

    ecrits = {}

    def put_object(**kwargs):
        ecrits["payload"] = json.loads(kwargs["Body"].decode())  # le magasin a écrit
        raise RuntimeError("RequestTimeout après commit")

    fake.put_object = put_object

    with pytest.raises(TokenStoreUnavailable):
        store.revoke(token["hash"][:12])

    assert ecrits["payload"]["tokens"][0]["revoked"] is True
    assert store._tokens[token["hash"]]["revoked"] is True
    assert store._needs_reload is True


def test_une_ecriture_douteuse_force_la_relecture_suivante():
    """Sans le drapeau, le cache resterait « frais » alors qu'il est incertain."""
    token = a_token()
    store, fake = make_store(put_error=RuntimeError("S3 503 SlowDown"),
                             payload={"tokens": [token]},
                             token_store_cache_ttl=10_000)
    store.load()
    with pytest.raises(TokenStoreUnavailable):
        store.revoke(token["hash"][:12])

    fake.put_error = None
    store._backoff_until = 0
    lectures = fake.get_calls
    store.get_by_hash(token["hash"])

    assert fake.get_calls == lectures + 1
    assert store._needs_reload is False


def test_deux_mutations_concurrentes_dans_un_process_ne_s_entrelacent_pas():
    """Une mutation est un lire-modifier-écrire : l'entrelacer en perd une.

    Ce verrou ne couvre que ce process : deux instances peuvent toujours
    s'écraser, faute d'écriture conditionnelle côté magasin.
    """
    import threading

    token = a_token()
    journal = []
    verrou_journal = threading.Lock()

    class S3Lent:
        def __init__(self):
            self.payload = {"tokens": [token]}

        def get_object(self, **kwargs):
            with verrou_journal:
                journal.append("get")
            time.sleep(0.02)
            return {"Body": io.BytesIO(json.dumps(self.payload).encode())}

        def put_object(self, **kwargs):
            with verrou_journal:
                journal.append("put")
            self.payload = json.loads(kwargs["Body"].decode())

    store = S3TokenStore(DummySettings())
    store._s3_client = S3Lent()

    resultats = {}
    fils = [
        threading.Thread(target=lambda: resultats.__setitem__(
            "cree", store.create("agent-b", ["read"]))),
        threading.Thread(target=lambda: resultats.__setitem__(
            "revoque", store.revoke(token["hash"][:12]))),
    ]
    for f in fils:
        f.start()
    for f in fils:
        f.join()

    assert journal == ["get", "put", "get", "put"], journal

    final = {t["hash"]: t for t in store._s3_client.payload["tokens"]}
    assert resultats["revoque"] is True
    assert final[token["hash"]]["revoked"] is True
    assert resultats["cree"]["hash"] in final


class VaultHorsService(VaultTokenStore):
    """Vault dont la lecture marche et dont l'écriture est refusée."""

    def load(self):
        self._cache_time = time.time()

    def _save(self):
        raise TokenStoreUnavailable("MCP Vault unavailable while saving token store")


def test_le_magasin_vault_restaure_aussi_son_etat_apres_un_refus():
    store = VaultHorsService(DummySettings())
    token = a_token()
    store._tokens = {token["hash"]: token}

    with pytest.raises(TokenStoreUnavailable):
        store.update(token["hash"][:12], permissions=["admin"])
    assert store._tokens[token["hash"]]["permissions"] == ["read"]

    with pytest.raises(TokenStoreUnavailable):
        store.revoke(token["hash"][:12])
    # Une révocation dont l'écriture échoue garde le refus : l'annuler rendrait
    # le token valide ici alors que Vault l'a peut-être déjà marqué mort.
    assert store._tokens[token["hash"]]["revoked"] is True

    with pytest.raises(TokenStoreUnavailable):
        store.create(client_name="agent", permissions=["read"])
    assert set(store._tokens) == {token["hash"]}


class SaveQuiEchoueApresUneAutreMutation(S3TokenStore):
    """Une écriture qui échoue après qu'un autre token a été touché."""

    AUTRE = "c" * 64

    def load(self):
        self._cache_time = time.time()

    def _save(self):
        self._tokens[self.AUTRE] = a_token(self.AUTRE)
        raise TokenStoreUnavailable("S3 Connection timeout")


def test_la_restauration_n_emporte_pas_une_mutation_portant_sur_un_autre_token():
    token = a_token()
    token["permissions"] = ["read"]
    store = SaveQuiEchoueApresUneAutreMutation(DummySettings())
    store._tokens = {token["hash"]: token}

    with pytest.raises(TokenStoreUnavailable):
        store.update(token["hash"][:12], permissions=["admin"])

    assert store._tokens[token["hash"]]["permissions"] == ["read"]
    assert SaveQuiEchoueApresUneAutreMutation.AUTRE in store._tokens


# --- Démarrage et diagnostic -------------------------------------------------

def test_une_panne_au_demarrage_ne_empeche_pas_le_service_de_demarrer():
    """Refuser de démarrer coûterait /health, la console et la clé bootstrap,
    c'est-à-dire les moyens de diagnostiquer la panne."""
    store, _ = make_store(get_error=RuntimeError("S3 Connection timeout"))

    _load_at_startup(store, "S3")  # ne doit pas lever

    # Démarré mais dégradé : l'authentification par token refuse.
    with pytest.raises(TokenStoreUnavailable):
        store.get_by_hash("a" * 64)


class ReponseVault:
    def __init__(self, status_code, payload=None, json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


@dataclass
class VaultSettings(DummySettings):
    mcp_vault_url: str = "https://vault.example.test"
    mcp_vault_id: str = "vault-test"
    mcp_vault_token: str = "jeton"
    mcp_vault_token_file: str = ""
    mcp_vault_token_store_path: str = "token-store/tokens.json"
    mcp_vault_timeout: float = 1.0


def test_un_payload_vault_illisible_est_une_indisponibilite_pas_une_ValueError(monkeypatch):
    """Une ValueError nue remonterait jusqu'au démarrage et l'empêcherait."""
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: ReponseVault(200, json_error=True))
    store = VaultTokenStore(VaultSettings())

    with pytest.raises(TokenStoreUnavailable):
        store.load()

    # Et le démarrage encaisse ce cas comme les autres.
    _load_at_startup(store, "Vault")


def test_le_statut_dit_aussi_la_verite_pour_le_magasin_vault(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: ReponseVault(503))
    store = VaultTokenStore(VaultSettings())
    monkeypatch.setattr(ts, "get_token_store", lambda: store)

    with pytest.raises(TokenStoreUnavailable):
        store.load()

    assert get_token_store_status()["reachable"] is False


def test_le_statut_ne_pretend_pas_que_le_magasin_est_joignable(monkeypatch):
    store, _ = make_store(get_error=RuntimeError("S3 Connection timeout"))
    monkeypatch.setattr(ts, "get_token_store", lambda: store)

    avant = get_token_store_status()
    assert avant["reachable"] is True
    assert avant["never_loaded"] is True

    with pytest.raises(TokenStoreUnavailable):
        store.load()

    apres = get_token_store_status()
    assert apres["reachable"] is False
    # Le message d'erreur du magasin n'a pas à sortir par une réponse HTTP.
    assert "Connection timeout" not in json.dumps(apres)


# --- Cache périmé ------------------------------------------------------------

def _store_en_panne_avec_cache(age_offset):
    token = a_token()
    store, fake = make_store(get_error=RuntimeError("S3 Connection timeout"))
    store._tokens = {token["hash"]: token}
    store._cache_time = time.time() - (store.CACHE_TTL + age_offset)
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
    store._cache_time = time.time() - (store.CACHE_TTL + 86400)

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


async def call_admin(method, path, body=None, bearer="test-bootstrap-key"):
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
        "headers": [(b"authorization", b"Bearer " + bearer.encode())],
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


class StoreInjoignableDesLAuth:
    """Le garde admin lui-même tombe : l'appelant n'est donc pas authentifié."""

    def get_by_hash(self, token_hash):
        raise TokenStoreUnavailable(
            "chargement du magasin de tokens impossible : EndpointConnectionError "
            "Could not connect to https://s3-interne.example/bucket-prive-42"
        )


async def test_un_appelant_non_authentifie_ne_recoit_pas_le_message_du_magasin(monkeypatch):
    """Le garde admin vit dans le dispatcher, ce chemin est donc atteignable sans auth.

    Le porteur présenté ici n'est pas la clé bootstrap : `_is_admin` doit donc
    interroger le magasin, qui tombe. Rendre `str(e)` publiait l'endpoint S3
    interne et le nom du bucket à quiconque présente un bearer quelconque.
    """
    monkeypatch.setattr(admin_api, "get_token_store", lambda: StoreInjoignableDesLAuth())

    status, data = await call_admin("GET", "/admin/api/tokens", bearer="porteur-quelconque")

    assert status == 503
    assert data["error"] == "token_store_unavailable"
    rendu = json.dumps(data)
    assert "s3-interne.example" not in rendu
    assert "bucket-prive-42" not in rendu
    assert "EndpointConnectionError" not in rendu


async def test_une_ecriture_non_authentifiee_pendant_une_panne_repond_502_sans_detail(monkeypatch):
    monkeypatch.setattr(admin_api, "get_token_store", lambda: StoreInjoignableDesLAuth())

    status, data = await call_admin("POST", "/admin/api/tokens",
                                    {"client_name": "agent", "permissions": ["read"]},
                                    bearer="porteur-quelconque")

    assert status == 502
    assert "bucket-prive-42" not in json.dumps(data)
