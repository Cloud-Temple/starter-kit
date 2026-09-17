# -*- coding: utf-8 -*-
"""Les tests d'écriture conditionnelle savent-ils échouer ?

`tests/integration/test_ecriture_conditionnelle_ecs.py` sert à répondre « Dell
ECS honore-t-il If-Match ». Un tel test n'a de valeur que si son verdict
positif est autre chose que le silence d'un compteur mort. On le prouve en
retirant l'en-tête conditionnel de l'appel, ce qui reproduit exactement ce que
fait un serveur qui l'ignore : le test visé doit tomber.

Contre MinIO, trois mutations sur trois sont détectées.

Deux tests n'ont volontairement aucune mutation, et c'est le point important.
`test_if_match_accepte_un_etag_juste` et `test_if_none_match_cree_un_objet_absent`
sont des témoins : leur sujet n'est pas le serveur qui IGNORE la condition mais
celui qui la REFUSE en bloc. Sans eux, un stockage rejetant tout en-tête
conditionnel ressemblerait à un stockage qui protège. Retirer l'en-tête de leur
appel ne les fait pas tomber, puisque la condition y est vraie de toute façon.
Le mode de panne qu'ils détectent ne se produit pas en mutant le client, il se
produit en changeant de serveur : on ne le simule donc pas.

La barrière du test de concurrence n'est pas mutée non plus. La retirer ne fait
pas tomber le test contre un serveur conforme : mesuré, 277 écritures réussies
sur 400 sans aucune perte, parce que l'écrivain lent lit après le PUT d'un
autre et ne se dispute jamais l'objet. Elle n'est pas là pour détecter, elle est
là pour porter la probabilité de collision à un niveau utile. Sa valeur se lit
sur le compteur de conflits du test, pas sur une mutation.

Ce harnais a besoin d'un stockage S3 conforme, pas du vrai ECS : il éprouve les
tests, pas le stockage. MinIO fait l'affaire.

    docker network create mut-net
    docker run -d --name mut-minio --network mut-net \
      -e MINIO_ROOT_USER=cle -e MINIO_ROOT_PASSWORD=secret123 \
      quay.io/minio/minio server /data

puis, depuis un environnement où les dépendances de
`boilerplate/requirements.lock` et pytest sont installées, et avec le seau créé :

    RUN_REAL_S3=1 S3_ENDPOINT_URL=http://mut-minio:9000 \
    S3_ACCESS_KEY_ID=cle S3_SECRET_ACCESS_KEY=secret123 \
    S3_BUCKET_NAME=sonde S3_REGION_NAME=us-east-1 \
    SONDE_RONDES=15 python -B scripts/mutations_ecriture_conditionnelle.py

Le fichier de test est restauré après chaque mutation, y compris en cas
d'échec. Une ancre qui ne correspond plus fait échouer bruyamment : une
mutation qui ne s'applique pas ne doit jamais se lire comme une mutation
détectée.
"""

import ast
import os
import shutil
import subprocess
import sys

CHEMIN = "tests/integration/test_ecriture_conditionnelle_ecs.py"

MUTATIONS = [
    ("M01 If-Match faux ignoré",
     'Body=b"ecrasement", IfMatch=ETAG_FAUX)',
     'Body=b"ecrasement")',
     "test_if_match_refuse_un_etag_faux"),
    ("M02 If-None-Match ignoré",
     'Body=b"ecrasement", IfNoneMatch="*")',
     'Body=b"ecrasement")',
     "test_if_none_match_refuse_un_objet_present"),
    ("M03 condition absente sous concurrence",
     "s3.put_object(Bucket=seau, Key=cle, Body=charge, IfMatch=etag)",
     "s3.put_object(Bucket=seau, Key=cle, Body=charge)",
     "test_if_match_est_atomique_sous_concurrence"),
]


def _purger_bytecode():
    for racine, dossiers, _ in os.walk("."):
        for dossier in list(dossiers):
            if dossier == "__pycache__":
                shutil.rmtree(os.path.join(racine, dossier), ignore_errors=True)
                dossiers.remove(dossier)


def main():
    if not os.path.exists(CHEMIN):
        print(f"lancer depuis la racine du dépôt : {CHEMIN} introuvable")
        return 2
    if os.environ.get("RUN_REAL_S3") != "1":
        print("poser RUN_REAL_S3=1 et les variables S3_* vers un stockage de test")
        return 2

    origine = open(CHEMIN, encoding="utf-8").read()
    echecs = []
    try:
        for nom, ancien, nouveau, cible in MUTATIONS:
            occurrences = origine.count(ancien)
            if occurrences != 1:
                print(f"  ANCRE       {nom} : {occurrences} occurrence(s), attendu 1")
                echecs.append(nom)
                continue
            mute = origine.replace(ancien, nouveau)
            try:
                ast.parse(mute)
            except SyntaxError as erreur:
                print(f"  SYNTAXE     {nom} : {erreur}")
                echecs.append(nom)
                continue

            open(CHEMIN, "w", encoding="utf-8").write(mute)
            _purger_bytecode()
            resultat = subprocess.run(
                [sys.executable, "-B", "-m", "pytest", f"{CHEMIN}::{cible}",
                 "-q", "-p", "no:cacheprovider"],
                capture_output=True, text=True,
            )
            open(CHEMIN, "w", encoding="utf-8").write(origine)

            if resultat.returncode != 0:
                print(f"  DÉTECTÉE    {nom}  ->  {cible}")
            else:
                print(f"  SURVIVANTE  {nom}  ->  {cible}")
                derniere = resultat.stdout.strip().splitlines()
                if derniere:
                    print(f"              {derniere[-1]}")
                echecs.append(nom)
    finally:
        open(CHEMIN, "w", encoding="utf-8").write(origine)
        _purger_bytecode()

    print(f"\n{len(MUTATIONS) - len(echecs)}/{len(MUTATIONS)} mutations détectées")
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
