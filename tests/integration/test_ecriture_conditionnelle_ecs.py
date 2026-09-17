# -*- coding: utf-8 -*-
"""Le vrai Dell ECS honore-t-il l'écriture conditionnelle S3 ?

La question bloque depuis juin le correctif du magasin de tokens sur trois
dépôts (`starter-kit#28`, `mcp-office#4`, `mcp-teleport#146`). Tous trois
réécrivent `_system/tokens.json` en entier sans condition : deux instances qui
écrivent en même temps s'écrasent, et une révocation peut disparaître sans que
la moindre erreur remonte. La correction envisagée est un compare-and-swap par
`If-Match`, et elle suppose que le stockage l'implémente.

Ce qui rend la question piégeuse : Cloud Temple impose SigV2 sur les opérations
data, et `HmacV1Auth` ne signe que `content-md5`, `content-type`, `date` et les
en-têtes `x-amz-*`. `If-Match` n'entre pas dans la signature. Un serveur qui ne
l'implémente pas ne renvoie donc aucune erreur : il ignore l'en-tête, le PUT
réussit, et l'appelant croit tenir un verrou qu'il n'a pas. Le mode de panne
que le correctif est censé fermer serait réintroduit par le correctif.

D'où la forme de ces tests. Ils n'observent pas, ils exigent. Si ECS n'honore
pas la condition, ils échouent, et cet échec est la réponse.

Deux niveaux, parce que le premier ne suffit pas.

Séquentiel. Un `If-Match` correct doit passer, sinon un serveur qui refuse tout
ressemblerait à un serveur qui protège. Un `If-Match` volontairement faux doit
rendre 412, et c'est le cas qui tranche : un serveur qui ignore l'en-tête
accepte aussi bien le faux que le vrai. `If-None-Match: *` est une primitive
distincte, pas forcément implémentée avec la précédente, et c'est celle dont
aurait besoin l'autre route, un objet S3 par token.

Concurrent. Un 412 en séquentiel ne prouve pas l'atomicité : un serveur peut
comparer puis écrire en deux temps et perdre la course entre les deux. Des
écrivains lisent le même objet, attendent à une barrière, puis écrivent tous
avec le MÊME ETag. Un seul doit gagner. Sans la barrière, mesuré contre MinIO,
277 écritures sur 400 réussissaient parce que l'écrivain lent lisait après le
PUT d'un autre : la collision n'avait jamais lieu et un CAS cassé une fois sur
mille serait passé inaperçu.

L'instrument a été validé avant d'être utilisé : la même épreuve, privée de son
en-tête conditionnel, rapporte 350 marques perdues sur 400 écritures. Un « zéro
perte » n'est donc pas le silence d'un compteur mort.

Le client S3 est celui du magasin, obtenu par `TokenStore._get_s3()`, pour
mesurer la configuration que la production utilise et pas une configuration
voisine. La signature est surchargée explicitement dans le seul cas où l'on
veut savoir si la réponse dépend d'elle.
"""

import json
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mon_service.auth.token_store import TokenStore  # noqa: E402

pytestmark = pytest.mark.real_s3

RUN_REAL_S3 = os.environ.get("RUN_REAL_S3") == "1"
VARIABLES_REQUISES = [
    "S3_ENDPOINT_URL",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_BUCKET_NAME",
]

ETAG_FAUX = '"00000000000000000000000000000000"'


@dataclass
class ReglagesS3:
    """Réglages minimaux attendus par `TokenStore._get_s3()`.

    Dupliqué depuis `test_real_s3_tokenstore.py` au lieu d'être partagé : ce
    fichier-ci ajoute `s3_signature_version`, que l'autre n'a pas, et l'autre
    n'a encore jamais tourné contre le vrai stockage. On ne remanie pas un test
    dans le mouvement où on essaie de l'exécuter pour la première fois.
    """

    s3_endpoint_url: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_bucket_name: str
    s3_region_name: str = "fr1"
    s3_signature_version: str = "s3"
    s3_addressing_style: str = "path"


def _reglages(signature: str) -> ReglagesS3:
    return ReglagesS3(
        s3_endpoint_url=os.environ["S3_ENDPOINT_URL"],
        s3_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        s3_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        s3_bucket_name=os.environ["S3_BUCKET_NAME"],
        s3_region_name=os.environ.get("S3_REGION_NAME", "fr1"),
        s3_signature_version=signature,
    )


@pytest.fixture(params=["s3", "s3v4"], ids=["sigv2", "sigv4"])
def bac(request):
    """Client S3 et préfixe jetable, nettoyé quoi qu'il arrive.

    Le préfixe porte un identifiant unique : deux exécutions simultanées du
    workflow ne doivent pas se mesurer l'une l'autre.
    """
    if not RUN_REAL_S3:
        pytest.skip("poser RUN_REAL_S3=1 pour lancer les tests contre le vrai stockage")
    manquantes = [n for n in VARIABLES_REQUISES if not os.environ.get(n)]
    if manquantes:
        pytest.skip(f"variables d'environnement absentes : {', '.join(manquantes)}")

    reglages = _reglages(request.param)
    s3 = TokenStore(reglages)._get_s3()
    prefixe = f"_sonde/{uuid.uuid4().hex}/"
    try:
        yield s3, reglages.s3_bucket_name, prefixe, request.param
    finally:
        jeton = None
        while True:
            kw = {"Bucket": reglages.s3_bucket_name, "Prefix": prefixe}
            if jeton:
                kw["ContinuationToken"] = jeton
            try:
                page = s3.list_objects_v2(**kw)
            except ClientError:
                break
            for objet in page.get("Contents", []):
                try:
                    s3.delete_object(Bucket=reglages.s3_bucket_name, Key=objet["Key"])
                except ClientError:
                    pass
            if not page.get("IsTruncated"):
                break
            jeton = page.get("NextContinuationToken")


def _statut(erreur):
    return erreur.response.get("ResponseMetadata", {}).get("HTTPStatusCode")


def _code(erreur):
    return erreur.response.get("Error", {}).get("Code")


def test_if_match_accepte_un_etag_juste(bac):
    """Contrôle indispensable : sans lui, un serveur qui refuse tout passerait
    pour un serveur qui protège."""
    s3, seau, prefixe, signature = bac
    cle = prefixe + "sequentiel"

    etag = s3.put_object(Bucket=seau, Key=cle, Body=b"v0")["ETag"]
    s3.put_object(Bucket=seau, Key=cle, Body=b"v1", IfMatch=etag)

    assert s3.get_object(Bucket=seau, Key=cle)["Body"].read() == b"v1", (
        f"sous {signature}, un If-Match portant l'ETag courant n'a pas écrit"
    )


def test_if_match_refuse_un_etag_faux(bac):
    """L'épreuve qui tranche. Un serveur qui ignore l'en-tête accepte."""
    s3, seau, prefixe, signature = bac
    cle = prefixe + "sequentiel"
    s3.put_object(Bucket=seau, Key=cle, Body=b"v0")

    with pytest.raises(ClientError) as capture:
        s3.put_object(Bucket=seau, Key=cle, Body=b"ecrasement", IfMatch=ETAG_FAUX)

    erreur = capture.value
    assert _statut(erreur) == 412 or _code(erreur) == "PreconditionFailed", (
        f"sous {signature}, le refus n'est pas une condition non remplie "
        f"mais {_code(erreur)} HTTP {_statut(erreur)}"
    )
    assert s3.get_object(Bucket=seau, Key=cle)["Body"].read() == b"v0", (
        f"sous {signature}, le corps a changé alors que la condition était fausse"
    )


def test_if_none_match_cree_un_objet_absent(bac):
    s3, seau, prefixe, signature = bac
    cle = prefixe + "creation"

    s3.put_object(Bucket=seau, Key=cle, Body=b"v0", IfNoneMatch="*")

    assert s3.get_object(Bucket=seau, Key=cle)["Body"].read() == b"v0", (
        f"sous {signature}, la création conditionnelle d'un objet absent n'a pas écrit"
    )


def test_if_none_match_refuse_un_objet_present(bac):
    """Primitive distincte de `If-Match`, et celle dont aurait besoin la route
    un objet S3 par token."""
    s3, seau, prefixe, signature = bac
    cle = prefixe + "creation"
    s3.put_object(Bucket=seau, Key=cle, Body=b"v0")

    with pytest.raises(ClientError) as capture:
        s3.put_object(Bucket=seau, Key=cle, Body=b"ecrasement", IfNoneMatch="*")

    erreur = capture.value
    assert _statut(erreur) == 412 or _code(erreur) == "PreconditionFailed", (
        f"sous {signature}, le refus n'est pas une condition non remplie "
        f"mais {_code(erreur)} HTTP {_statut(erreur)}"
    )
    assert s3.get_object(Bucket=seau, Key=cle)["Body"].read() == b"v0", (
        f"sous {signature}, le corps a changé alors que l'objet existait déjà"
    )


@pytest.mark.timeout(600)
def test_if_match_est_atomique_sous_concurrence(bac):
    """Un 412 en séquentiel ne prouve pas l'atomicité.

    Deux indicateurs indépendants. Les marques perdues répondent « une écriture
    annoncée comme réussie a-t-elle survécu », les rondes à plusieurs gagnants
    répondent « deux écrivains partis du même ETag ont-ils pu réussir ». Le
    second attrape le cas où les marques survivent par chance.
    """
    s3, seau, prefixe, signature = bac
    ecrivains = int(os.environ.get("SONDE_ECRIVAINS", "8"))
    rondes = int(os.environ.get("SONDE_RONDES", "25"))
    cle = prefixe + "concurrent"

    s3.put_object(Bucket=seau, Key=cle, Body=json.dumps([]).encode())

    succes = 0
    conflits = 0
    autres = {}
    rondes_multi = []

    for ronde in range(rondes):
        barriere = threading.Barrier(ecrivains, timeout=60)

        def ecrire(indice, ronde=ronde, barriere=barriere):
            reponse = s3.get_object(Bucket=seau, Key=cle)
            corps = json.loads(reponse["Body"].read())
            etag = reponse["ETag"]
            corps.append(f"{ronde}-{indice}")
            charge = json.dumps(corps).encode()
            try:
                barriere.wait()
            except threading.BrokenBarrierError:
                return "autre", "barrière rompue"
            try:
                s3.put_object(Bucket=seau, Key=cle, Body=charge, IfMatch=etag)
                return "succes", None
            except ClientError as erreur:
                if _statut(erreur) == 412 or _code(erreur) == "PreconditionFailed":
                    return "conflit", None
                return "autre", f"{_code(erreur)} HTTP {_statut(erreur)}"

        gagnants = 0
        with ThreadPoolExecutor(max_workers=ecrivains) as bassin:
            for issue, detail in bassin.map(ecrire, range(ecrivains)):
                if issue == "succes":
                    succes += 1
                    gagnants += 1
                elif issue == "conflit":
                    conflits += 1
                else:
                    autres[detail] = autres.get(detail, 0) + 1
        if gagnants > 1:
            rondes_multi.append((ronde, gagnants))

    presentes = len(json.loads(s3.get_object(Bucket=seau, Key=cle)["Body"].read()))
    perdues = succes - presentes

    rapport = (
        f"signature {signature}, {ecrivains} écrivains x {rondes} rondes : "
        f"{succes} succès annoncés, {presentes} marques présentes, "
        f"{conflits} conflits 412, autres erreurs {autres or 'aucune'}"
    )

    assert not autres, f"erreurs inattendues pendant l'épreuve. {rapport}"
    assert not rondes_multi, (
        f"{len(rondes_multi)} ronde(s) à plusieurs gagnants, donc deux écrivains "
        f"partis du même ETag ont réussi : la condition est ignorée ou le "
        f"compare-and-swap n'est pas atomique. Détail {rondes_multi[:5]}. {rapport}"
    )
    assert perdues == 0, (
        f"{perdues} écriture(s) annoncées comme réussies ont disparu. {rapport}"
    )
    # En dernier, parce que ce n'est pas un verdict sur le serveur mais sur
    # l'épreuve : si les écrivains ne se sont jamais disputé l'objet, les trois
    # assertions ci-dessus ont passé sans rien avoir à trancher.
    assert conflits > 0, (
        "aucun conflit sur toute l'épreuve : les écrivains ne se sont jamais "
        f"disputé l'objet, l'épreuve n'a donc rien mesuré. {rapport}"
    )
