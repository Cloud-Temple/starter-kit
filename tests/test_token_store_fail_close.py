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

Preuves par mutation, mesurées sur ces 43 tests. Chaque défaut est réintroduit
par une modification précise, et la suite tombe ainsi :

    mutation appliquée au code corrigé                          rouges
    ---------------------------------------------------------   ------
    `_persist_or_restore` n'annule rien et ne marque rien      7/43
    `load()` ravale sa levée, l'avalement d'origine            7/43
    `_save()` ravale sa levée                                  6/43
    le middleware n'attrape plus TokenStoreUnavailable         6/43
    `handle_admin_api` n'attrape plus TokenStoreUnavailable    4/43
    `_guard_stale` ne lève jamais                              4/43
    le refus du middleware laisse tourner l'app en aval        3/43
    une révocation ratée est annulée en mémoire                3/43
    une écriture douteuse ne marque plus le cache              3/43
    `CACHE_TTL` replie un `0` configuré sur 300                3/43
    un `TTL=0` laisse la grâce rouvrir la fenêtre              2/43
    le compteur de génération est ignoré (S3 et Vault)         2/43
    `_serialise` n'incrémente plus le compteur                 2/43
    l'API admin rend de nouveau `str(e)` dans le corps         2/43
    `_is_missing_object` accepte de nouveau un statut 404 nu   2/43
    le wrapper Vault rebaisse `_needs_reload`                  1/43
    le 503 du middleware republie le message du magasin        1/43
    le refus du middleware redevient un 401                    1/43
    le 503 du middleware ne dit plus quand réessayer           1/43
    un websocket invérifiable est accepté                      1/43
    les routes publiques ne court-circuitent plus l'auth       1/43
    `_maybe_refresh` ignore le drapeau de revalidation         1/43
    les mutations ne sont plus sérialisées                     1/43

Aucun de ces chiffres n'est une estimation, et aucune mutation ne passe
inaperçue. Reproduire : appliquer une mutation au fichier source, relancer
`pytest tests/test_token_store_fail_close.py`, compter, annuler.

Limite connue et non couverte ici : sans écriture conditionnelle côté magasin,
deux instances peuvent encore s'écraser mutuellement. Le verrou vérifié plus
bas ne sérialise que ce process, et seulement ses mutations entre elles. Une
lecture concurrente n'est pas sérialisée avec une mutation ; c'est le compteur
de génération, pas le verrou, qui l'empêche d'effacer une révocation.
"""

import asyncio
import copy
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
    """La grâce par défaut est laissée en place, et c'est tout l'intérêt.

    La version précédente de ce test fixait aussi `token_store_stale_grace=0`.
    Elle testait donc une configuration que personne n'écrit, et masquait le
    défaut : avec la grâce par défaut, un `TTL=0` servait encore le cache
    pendant cinq minutes, c'est-à-dire exactement la fenêtre de révocation que
    l'exploitant refusait en mettant zéro.
    """
    token = a_token()
    store, _ = make_store(get_error=RuntimeError("S3 Connection timeout"),
                          token_store_cache_ttl=0)
    assert store.stale_grace == 300, "la grâce par défaut doit rester en place"
    store._tokens = {token["hash"]: token}
    store._cache_time = time.time() - 1

    with pytest.raises(TokenStoreUnavailable):
        store.get_by_hash(token["hash"])


def test_une_grace_par_defaut_protege_toujours_un_ttl_normal():
    """Contrepartie du test ci-dessus : court-circuiter la grâce ne doit valoir
    que pour un TTL nul. Avec un TTL normal, une panne brève reste absorbée."""
    token = a_token()
    store, _ = make_store(get_error=RuntimeError("S3 Connection timeout"),
                          token_store_cache_ttl=60)
    store._tokens = {token["hash"]: token}
    store._cache_time = time.time() - 90  # périmé, mais dans les 60 + 300

    assert store.get_by_hash(token["hash"]) is not None


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


# =============================================================================
# Le 503 du middleware d'authentification
#
# Ce bloc comblait un trou : la suite affirmait couvrir le refus en 503 alors
# qu'aucun test ne construisait `AuthMiddleware`. Retirer tout le
# `except TokenStoreUnavailable` du middleware laissait les 128 tests du dépôt
# verts, y compris ceux de ce fichier.
# =============================================================================

from mon_service.auth.middleware import AuthMiddleware  # noqa: E402
from mon_service.auth.context import current_token_info  # noqa: E402


class _AppTemoin:
    """Application en aval. Doit rester intouchée quand l'accès est refusé."""

    def __init__(self):
        self.appels = 0
        self.token_info_vu = "jamais appelée"

    async def __call__(self, scope, receive, send):
        self.appels += 1
        self.token_info_vu = current_token_info.get()
        corps = b'{"ok": true}'
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": corps})


async def _appeler_middleware(middleware, path="/mcp", bearer=None, type_scope="http"):
    envoyes = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        envoyes.append(message)

    headers = [(b"authorization", b"Bearer " + bearer.encode())] if bearer else []
    scope = {"type": type_scope, "method": "POST", "path": path, "headers": headers}
    await middleware(scope, receive, send)

    debut = next((m for m in envoyes if m["type"] == "http.response.start"), None)
    brut = b"".join(m.get("body", b"") for m in envoyes if m["type"] == "http.response.body")
    return debut, brut, envoyes


def _middleware_dont_le_magasin_est_injoignable(monkeypatch, message=None):
    """AuthMiddleware dont la validation lève, comme lors d'une panne réelle."""
    app = _AppTemoin()
    middleware = AuthMiddleware(app)
    detail = message or (
        "chargement du magasin de tokens impossible : EndpointConnectionError "
        "Could not connect to https://s3-interne.example/bucket-prive-42"
    )

    def _tombe(self, token):
        raise TokenStoreUnavailable(detail)

    monkeypatch.setattr(AuthMiddleware, "_validate_token", _tombe)
    return middleware, app


def test_un_acces_invérifiable_repond_503_et_non_401():
    """Un 401 dirait « ton token est invalide », et le client le remplacerait.

    C'est la promesse centrale du correctif. Sans ce test, supprimer le
    `except TokenStoreUnavailable` du middleware ne faisait rien tomber.
    """
    monkeypatch = pytest.MonkeyPatch()
    try:
        middleware, app = _middleware_dont_le_magasin_est_injoignable(monkeypatch)
        debut, _, _ = asyncio.run(_appeler_middleware(middleware, bearer="un-token-quelconque"))
    finally:
        monkeypatch.undo()

    assert debut is not None, "le middleware n'a rien répondu"
    assert debut["status"] == 503
    assert debut["status"] != 401


def test_un_acces_invérifiable_n_atteint_jamais_l_application():
    """Refuser, c'est ne pas exécuter la requête. Un 503 rendu après coup ne
    protégerait rien si l'outil en aval a déjà tourné."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        middleware, app = _middleware_dont_le_magasin_est_injoignable(monkeypatch)
        asyncio.run(_appeler_middleware(middleware, bearer="un-token-quelconque"))
    finally:
        monkeypatch.undo()

    assert app.appels == 0
    assert app.token_info_vu == "jamais appelée"


def test_le_503_du_middleware_ne_publie_pas_le_detail_du_magasin():
    """Le message cite l'endpoint S3 et le bucket. Il reste sur stderr."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        middleware, _ = _middleware_dont_le_magasin_est_injoignable(monkeypatch)
        _, brut, _ = asyncio.run(_appeler_middleware(middleware, bearer="un-token-quelconque"))
    finally:
        monkeypatch.undo()

    rendu = brut.decode()
    assert "s3-interne.example" not in rendu
    assert "bucket-prive-42" not in rendu
    assert "EndpointConnectionError" not in rendu
    assert json.loads(rendu)["error"] == "token_store_unavailable"


def test_le_503_du_middleware_dit_au_client_de_reessayer():
    """Sans `Retry-After`, un client bien élevé n'a aucune borne à respecter."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        middleware, _ = _middleware_dont_le_magasin_est_injoignable(monkeypatch)
        debut, _, _ = asyncio.run(_appeler_middleware(middleware, bearer="un-token-quelconque"))
    finally:
        monkeypatch.undo()

    entetes = {k.lower(): v for k, v in debut["headers"]}
    assert entetes.get(b"retry-after") == b"30"


def test_un_websocket_invérifiable_est_ferme_et_non_accepte():
    """Un websocket n'a pas de code HTTP. 1013 « try again later » est l'équivalent."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        middleware, app = _middleware_dont_le_magasin_est_injoignable(monkeypatch)
        _, _, envoyes = asyncio.run(
            _appeler_middleware(middleware, bearer="un-token", type_scope="websocket"))
    finally:
        monkeypatch.undo()

    assert envoyes == [{"type": "websocket.close", "code": 1013}]
    assert app.appels == 0


def test_une_route_publique_ne_consulte_jamais_le_magasin():
    """Pourquoi ce test ne prouve PAS que le 503 fonctionne.

    Il a d'abord été écrit sous le nom « le health check reste vert pendant une
    panne », et la contre-revue a eu raison de le rejeter sous ce nom : `/health`
    est dans `PUBLIC_PATHS`, donc le middleware sort avant d'appeler
    `_validate_token`. Le faux magasin en panne ne peut pas s'y exprimer, et le
    test restait vert avec ou sans le `except TokenStoreUnavailable`. En
    production, `HealthCheckMiddleware` intercepte encore plus haut.

    Ce qu'il prouve réellement, et qui mérite d'être protégé : une route
    publique ne consulte pas le magasin du tout. C'est la condition pour qu'une
    panne S3 n'entraîne pas un cycle de redémarrages généralisé par l'orchestrateur,
    ce qui la transformerait en panne totale. Le test tombe si quelqu'un retire
    le court-circuit des routes publiques.
    """
    monkeypatch = pytest.MonkeyPatch()
    consultations = []
    try:
        app = _AppTemoin()
        middleware = AuthMiddleware(app)

        def _compte_et_tombe(self, token):
            consultations.append(token)
            raise TokenStoreUnavailable("magasin injoignable")

        monkeypatch.setattr(AuthMiddleware, "_validate_token", _compte_et_tombe)
        debut, _, _ = asyncio.run(
            _appeler_middleware(middleware, path="/health", bearer="un-token"))
    finally:
        monkeypatch.undo()

    assert consultations == [], "une route publique a interrogé le magasin"
    assert debut["status"] == 200
    assert app.appels == 1


def test_une_requete_sans_token_traverse_toujours_pendant_une_panne():
    """Le magasin n'est pas interrogé sans Bearer : rien à refuser ici.

    Répondre 503 à du trafic anonyme ferait tomber les routes publiques par
    ricochet, et gonflerait la panne au-delà de ce qu'elle touche vraiment.
    """
    monkeypatch = pytest.MonkeyPatch()
    try:
        middleware, app = _middleware_dont_le_magasin_est_injoignable(monkeypatch)
        debut, _, _ = asyncio.run(_appeler_middleware(middleware, bearer=None))
    finally:
        monkeypatch.undo()

    assert debut["status"] == 200
    assert app.appels == 1
    assert app.token_info_vu is None


def test_un_token_valide_passe_toujours_quand_le_magasin_repond():
    """Garde-fou de non-régression : le refus ne doit pas mordre sur le cas sain."""
    app = _AppTemoin()
    middleware = AuthMiddleware(app)
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(AuthMiddleware, "_validate_token",
                            lambda self, token: {"client_name": "agent", "permissions": ["read"]})
        debut, _, _ = asyncio.run(_appeler_middleware(middleware, bearer="un-token-valide"))
    finally:
        monkeypatch.undo()

    assert debut["status"] == 200
    assert app.appels == 1
    assert app.token_info_vu["client_name"] == "agent"


def test_le_contextvar_ne_fuit_pas_apres_un_refus():
    """Un refus sort avant de poser le contextvar. S'il fuyait, la requête
    suivante du même worker hériterait d'un état d'authentification."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        middleware, _ = _middleware_dont_le_magasin_est_injoignable(monkeypatch)
        asyncio.run(_appeler_middleware(middleware, bearer="un-token"))
    finally:
        monkeypatch.undo()

    assert current_token_info.get() is None


def _executer_borne(action, message, secondes=10):
    """Exécute `action` dans un thread et échoue si elle ne rend pas la main.

    Les tests de course appellent une mutation pendant qu'un lecteur tient une
    I/O bloquée. Si une régression rend le verrou non réentrant, cet appel
    s'auto-interbloque et, sans borne, pend la suite entière : `pytest-timeout`
    n'est pas installé et les jobs de la CI n'ont pas de `timeout-minutes`, donc
    le défaut GitHub de six heures s'appliquerait. On veut un rouge en dix
    secondes, pas un budget de CI consommé.
    """
    import threading

    resultat = {}

    def _cible():
        try:
            resultat["valeur"] = action()
        except BaseException as e:  # noqa: BLE001 — le test doit voir la cause
            resultat["erreur"] = e

    fil = threading.Thread(target=_cible, daemon=True)
    fil.start()
    fil.join(timeout=secondes)
    if fil.is_alive():
        raise AssertionError(f"{message} : rien rendu en {secondes}s, interblocage probable")
    if "erreur" in resultat:
        raise resultat["erreur"]
    return resultat.get("valeur")


def test_une_lecture_en_vol_n_efface_pas_une_revocation_aboutie():
    """Le verrou des mutations ne protège pas d'une lecture concurrente.

    `load()` fait son GET hors verrou et ne le prend qu'au moment d'écraser
    `_tokens`. Une lecture partie avant une révocation peut donc revenir après
    elle, réinjecter la version non révoquée et lui offrir un TTL tout neuf :
    le token révoqué redevient valide sur cette instance pendant cinq minutes.

    La phrase « le verrou ne sérialise que ce process » laissait croire
    l'inverse, qu'à l'intérieur d'un process tout était sérialisé.

    L'entrelacement est ici imposé, pas espéré : le GET du lecteur est bloqué
    jusqu'à ce que la révocation soit écrite.
    """
    import threading

    token = a_token()
    gete_commence = threading.Event()
    liberer_le_lecteur = threading.Event()

    class S3QuiRetientLaPremiereLecture:
        def __init__(self):
            self.etat_serveur = {"tokens": [dict(token)]}
            self.gets = 0

        def get_object(self, **kwargs):
            self.gets += 1
            corps = json.dumps(self.etat_serveur).encode()
            if self.gets == 1:
                # Le lecteur tient déjà l'état d'avant révocation.
                gete_commence.set()
                liberer_le_lecteur.wait(timeout=5)
            return {"Body": io.BytesIO(corps)}

        def put_object(self, **kwargs):
            self.etat_serveur = json.loads(kwargs["Body"].decode())

    store = S3TokenStore(DummySettings())
    faux = S3QuiRetientLaPremiereLecture()
    store._s3_client = faux

    lecteur = threading.Thread(target=store.load)
    lecteur.start()
    assert gete_commence.wait(timeout=5), "le GET du lecteur n'a jamais démarré"

    revocation = _executer_borne(lambda: store.revoke(token["hash"][:16]),
                                 "store.revoke a bloqué")
    assert revocation is True
    liberer_le_lecteur.set()
    lecteur.join(timeout=5)
    assert not lecteur.is_alive(), "le lecteur est resté bloqué"

    assert store._tokens[token["hash"]]["revoked"] is True, (
        "la lecture en vol a réinjecté la version non révoquée"
    )
    # Le corps jeté n'est pas une vérité de remplacement : la prochaine lecture
    # doit aller la chercher au lieu de faire confiance à ce cache.
    assert store._needs_reload is True


def test_vault_aussi_une_lecture_en_vol_n_efface_pas_une_revocation(monkeypatch):
    """Le compteur de génération doit valoir pour les deux backends.

    La première version de ce correctif déclarait `_mutation_seq` dans
    `VaultTokenStore.__init__` sans jamais s'en servir dans `_load`. L'attribut
    n'était là que pour empêcher `_serialise` de lever une `AttributeError`.
    Résultat : Vault, un backend de production supporté, restait exposé au
    défaut exact que ce travail prétend corriger, avec en prime l'apparence
    d'une protection. Ce test rend cet oubli impossible à refaire en silence.
    """
    import threading
    import httpx

    token = a_token()
    gete_commence = threading.Event()
    liberer_le_lecteur = threading.Event()
    etat_serveur = {"tokens": [dict(token)]}
    gets = {"n": 0}

    def faux_get(*a, **k):
        gets["n"] += 1
        corps = copy.deepcopy(etat_serveur)
        if gets["n"] == 1:
            gete_commence.set()
            liberer_le_lecteur.wait(timeout=5)
        return ReponseVault(200, payload={"data": corps})

    def faux_post(*a, **k):
        envoye = k.get("json") or {}
        etat_serveur["tokens"] = copy.deepcopy(envoye.get("data", {}).get("tokens", []))
        return ReponseVault(200, payload={})

    monkeypatch.setattr(httpx, "get", faux_get)
    monkeypatch.setattr(httpx, "post", faux_post)
    monkeypatch.setattr(ts, "get_vault_application_token", lambda settings: "jeton")

    store = VaultTokenStore(VaultSettings())

    lecteur = threading.Thread(target=store.load)
    lecteur.start()
    assert gete_commence.wait(timeout=5), "le GET du lecteur n'a jamais démarré"

    revocation = _executer_borne(lambda: store.revoke(token["hash"][:16]),
                                 "store.revoke a bloqué")
    assert revocation is True

    liberer_le_lecteur.set()
    lecteur.join(timeout=5)
    assert not lecteur.is_alive(), "le lecteur est resté bloqué"

    assert store._tokens[token["hash"]]["revoked"] is True, (
        "la lecture en vol a réinjecté la version non révoquée côté Vault"
    )
    assert store._needs_reload is True


def test_un_ttl_a_zero_ferme_aussi_la_console_admin_pendant_une_panne():
    """Effet de bord assumé du court-circuit de la grâce, pas un accident.

    `_guard_stale` sert `get_by_hash` comme `list_all`, donc un `TTL=0` refuse
    aussi l'authentification admin par token pendant une panne. L'exploitant
    qui met zéro pour borner la fenêtre de révocation côté MCP perd du même
    coup sa console, au moment où il en a le plus besoin. Seule la clé
    bootstrap passe encore, elle ne consulte pas le magasin.

    Le comportement est cohérent avec le fail-close et c'est pourquoi il est
    conservé. Ce test existe pour qu'il ne se perde pas en silence, et pour que
    la ligne correspondante du README et du `.env.example` reste vraie.
    """
    token = a_token()
    store, _ = make_store(get_error=RuntimeError("S3 Connection timeout"),
                          token_store_cache_ttl=0)
    store._tokens = {token["hash"]: token}
    store._cache_time = time.time() - 1

    with pytest.raises(TokenStoreUnavailable):
        store.list_all()
