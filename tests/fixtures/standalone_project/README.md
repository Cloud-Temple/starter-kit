# Témoin de qualification #48 — FILES public, hors gabarit

Cette recette est de l'outillage de test, jamais un backend distribué ni une
correction Tools. **Aucun résultat natif n'est acquis par sa présence.**
Elle doit être relue avant toute fenêtre Docker. Le même témoin public est
réutilisé par #49 avec `compose.external-jobs.yml`, généré lors de la copie :
aucun postgres local ni pg-net ajouté par ce fragment, mêmes jobs/public Tools,
app et jobs joints au bridge externe dédié ainsi qu'au seul réseau S3 du cas.
Utiliser `external.py init --app-service mcp-tools` et la procédure distribuée
`ops/postgres/EXTERNAL.md` ; aucune qualification #49 n'est déduite de #48.

## Provenance et construction manuelle

Tools public SHA `6083e6b8c4a54a649583047a009eedc95238b373`, archive attendue
SHA256 `73b1d329ec90c17cd5de58bb06f37dba6a253369c72695bcae5819871422e056`.
`acquire.py` télécharge au SHA, contrôle avant extraction les chemins/types/
tailles, refuse symlinks et hash divergent. Aucun fallback main, vendoring,
contenu exécuté lors de l'acquisition ou résolution implicite des dépendances.

```sh
python tests/fixtures/standalone_project/acquire.py /new/tools-public
python tests/fixtures/standalone_project/copy_witness.py \
  --kit boilerplate --tools /new/tools-public --destination /new/witness-src
```

Le témoin part d'une copie du kit puis apporte les sources/scripts/locks/image
**publics Tools inchangés**, et adapte normalement noms/ports/Caddy8050 comme
un dérivé. Les montages client sont ceux de `docker-compose.pg-client.yml`
renommé, pas réimplémentés ici. Le SQL opérateur public reste seulement dans
`tools-source/docs/postgres/infrastructure.sql` du témoin, monté RO par job.
Ne pas importer les tests de la factory pour exécuter le produit.

Dans le futur environnement autorisé, builds **séquentiels** avec worker/daemon
eux-mêmes plafonnés : Tools2GiB/2CPU, jobTools+client1GiB/2CPU,
infraPG+openssl1GiB/1CPU, WAF2GiB/2CPU. Conserver les IDs et logs privés.

```sh
# Depuis la copie, seulement après fenêtre opérateur et préflight frais.
docker build -t starter48-tools:<run> -f Dockerfile.app .
docker image inspect starter48-tools:<run> --format '{{.Id}}'
# FROM tag local du même run, ImageID vérifié avant/après, jamais RepoDigest inventé.
docker build -t starter48-tools-job:<run> -f Dockerfile.operator \
  --build-arg TOOLS_LOCAL_IMAGE=starter48-tools:<run> .
docker build -t starter48-infra:<run> ops/postgres
docker build -t starter48-waf:<run> waf
```

Le job utilise USERroot pour apt puis USERmcp ; paquet Debian trixie
`postgresql-client-17=17.11-0+deb13u1`. La présence/disponibilité réelle du
paquet, OS et `pg_dump/pg_restore17.11` sont vérifiées au build, pas supposées
d'après libpq. Échec fermé si indisponible : aucun changement du Dockerfile app,
du lock Tools ou ajout de dépôt apt automatique. Pas de docker.sock nécessaire
à l'opération FILES, même si le DockerCLI fait partie du Dockerfile public.

## Décisions R2/P3 intégrées avant code

1. **S3TEST obligatoire et versionné**, en HTTPS, propre au scénario, préparé
   avant app. L'opérateur S3 crée un réseauTEST dédié explicite ; lui seul est
   partagé par source/restore/jobs dans `compose.jobs.yml`. Aucun service PG
   n'y entre. `external:true` est limité à ce réseau S3 du **témoin**, jamais
   aux volumes ou réseaux PG ; aucune connexion PG entre projets. Ne pas
   employer un endpoint/credential de production. Le kit n'installe pas S3.
2. Avant import : grants `pg_control_system()`/`pg_control_checkpoint()` aux
   identités opérateur et relevé **de cible** `identity.json`. Rôles et droits
   globaux rétablis avantimport. La probe de domaine arrive avec le schéma
   restauré ; ne pas rejouer `infrastructure.sql` si ses objets sont déjà dans
   le dump. Le check public valide ensuite la cible et ses droits.
3. UID/GID mesurés aussi pour jobTools, service/passfile0600, répertoire0700
   et parent d'archiveRW. Aucun postulat que UIDmcp=UIDhôte=UIDpostgres.
4. App construite localement : `.Id` réel, tag propre au run, pas RepoDigest.
   Job dérivé sous root pour apt, retour mcp, image app inchangée.
5. Renommage du service dans base **et fragment client distribué**, port8050
   et `/ready`200. Le guide de création mentionne aussi ce fragment.

## Préparer S3 et les identités du scénario

L'opérateur prépare un S3 de test et son HTTPS/CA, active le versioning avant
app et démontre les droits sous chaque identité (aucun simulateur auth utilisé
comme preuve IAM). Choisir images officielles épinglées et budget ci-dessous.
La recette reçoit ces **préconditions explicites**, sans accéder à une stack
de qualifications existante ni lancer un provider à sa place :

- `TEST_S3_NETWORK` : réseauTESTS3 de ce seul scénario ; son service doit être
  joignable sous le hostname TLS du endpoint par source ET cible restaurée.
- `TEST_S3_CA_FILE` : CA publique de ce S3, lecture seule dans
  `/run/test-s3/ca.crt` par app/jobs.
- `TOOLS_S3_APP_ENV` : fichier privé0600 pour l'identité app.
- `TOOLS_S3_JOB_ENV` : fichier privé0600 **différent pour chaque job** install,
  backup ou restore. Ne jamais y mettre le provisionneur SQL/root S3.
- `TOOLS_WAF_IMAGE=starter48-waf:<run>` : image du WAF copié, construite avec
  les plafonds annoncés avant runtime, sans modification de son Dockerfile.

Ces fichiers contiennent les variables existantes `S3_ENDPOINT_URL=https://…`,
`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, `S3_REGION_NAME`,
`AWS_CA_BUNDLE=/run/test-s3/ca.crt`. Le produit conserve ces providers.
L'endpoint et le bucket sont communs, les identités/scopes sont distincts :

| Identité | Droits nécessaires limités au scénario |
| --- | --- |
| app | GetBucketVersioning ; lecture/écriture/delete marker des générations FILES ; accès TokenStore S3 historique nécessaire aux tokens, avec préfixes séparés et sans privilèges IAM/effacement de versions. |
| install | Aucun effet S3 requis par le bootstrap SQL ; credential propre sans accès aux données, ne pas réutiliser l'identité app. |
| backup / restore | GetBucketVersioning et GetObjectVersion/lecture des pins et métadonnées du seul préfixe FILES ; aucun PUT/DELETE/purge/tokenstore/IAM. |

Préparer la CA lisible par les UID app/jobs sans exposer les credentials des
envfiles. Provisionner les bearers MCP writer/admin/refusé via les interfaces
publiques **existantes** de Tools avec TokenStore S3 ; ne pas sélectionner
ACCESSpostgres ni créer un mode directACCESS. Le writer porte les permissions
FILES nécessaires ; le bearer refusé n'a pas ces droits. Garder les tokens en
fichiers privés, retirer leurs accès après les épreuves et vérifier les refus.

L'image et les identités S3 réelles doivent être vérifiées au préflight du
scénario ; ne pas substituer une image non épinglée ou un ancien banc.

## Source locale et séquence réelle

Initialiser selon `ops/postgres/README.md`, noms **exactement** : database`tools`,
owner`tools_files_owner`, app`tools_files_app`, installer`tools_files_install`,
backup`tools_files_backup`, restore`tools_files_restore` ; projet
`starter48-<run>-src` et destination privée neuve. Copier `.env.example` vers
`.env`, configurer le serveur et les protections Host/Origin usuelles de Tools.
Le fichierS3app est ajouté explicitement par le Compose du témoin.

1. `prepare.py init`, puis `start-postgres`. Disponibilité via `identity` TLS
   bornée ; pas de retry du provisioning. `provision` crée rôles/base et pose
   les grants natifs AVANT le premier bootstrap, enregistre l'identité réelle.
2. `prepare.py install-product` appelle le hook du témoin : jobinstall seul
   `files_bootstrap --apply` → provisionneur `infrastructure.sql` public exact
   → grants étroits des jobs → `--check` → retrait membership/LOGINinstall.
   Pas de SQL produit dans le kit, aucun privilège de création dans l'app.
3. `python ops/postgres/tools_operator.py start /private/state.json`, avec
   variablesS3 ci-dessus. Ce lanceur transmet l'identitéPG observée, chemins
   libpq et ImageIDs du state. `/ready` doit atteindre200 avant contrat public.
4. Vérifier UID/modes et montageRO **depuis le conteneur**. `qualify.py seed`
   appelle le tool publicFILES, écrit UUID/intention avant dispatch puis lit
   le contenu et sa SHA256. `qualify.py read` n'écrit rien et ne rejoue jamais.
5. Arrêter puis démarrer les mêmes app/PG/volume ; relire avec un process et
   une connexion neufs. Le testE2E compare aussi le nom du volume réel.

```sh
# Depuis la factory pour l'oracle seulement ; le serveur utilise la copie autonome.
RUN_STANDALONE_48=1 STANDALONE_48_COPY=/new/witness-src \
STANDALONE_48_STATE=/private/src/state.json \
STANDALONE_48_URL=http://127.0.0.1:<owned-waf-port> \
STANDALONE_48_WRITER_TOKEN_FILE=/private/writer.token \
python -m pytest tests/e2e/test_standalone_postgres.py -q
```

Ce test est volontairement opt-in. Sans fenêtre, un skip n'est **pas** une
qualification. Les fauteshostname/CA/credential/cible/fichier absent touchent
une copie privée de la connexion, pas l'état source : mutationpublique refusée,
puis configuration saine rétablie et absence constatée par le contrat public.
Les répertoires de faute sont préservés ; aucune suppression du volume.

## Backup, restore et fermeture

Suspendre réellement les écrivains avant de déclarer `--quiesced`. Exécuter
`tools_operator.py backup <state-source>` avec fichierS3backup distinct.
Le parent `/private/src/archives` appartient à l'UID job ; `files_backup`
crée le sous-dossier `export` en0700, fichiers0600. Chiffrer et conserver
l'archive hors preuves publiques, retenir les pins/versions S3 et l'auth.

Créer une deuxième copie identique `witness-restore`, projet
`starter48-<run>-restore`, répertoire privé et volume neufs ; même imageapp/job,
jamais de volume/réseau PG externe. Initialiser puis provisionner cette cible,
**sans install-product/--apply**. `provision` y pose les grants natifs et relève
le nouvel identifiant système AVANT import. Copier l'archive vers le parentRW
de cible via un helper borné (sourceRO, cibleRW), conserver modes/UID job.
Ne jamais donner CONNECTsource au rôle SQLrestore de cible.

Avec le fichierS3restore RO et réseauTESTS3, exécuter
`tools_operator.py restore <state-cible>`. Il ajoute les grants globaux owner
nécessaires à la probe, appelle le modulepublic `files_backup import`, vérifie
le schéma restauré via `--check`, retire LOGIN/membershiprestore, puis appneuve
`start`, `/ready`200 et `qualify.py read` avec l'intention confirmée conservée.
La CA/identitéPG sont celles de cible ; le S3 versionné et les pins restent les
mêmes. Ne jamais réécrire les versions ni inventer un succès d'ACK perdu.

Après chaque phase vérifier ses révocations et fermer la fenêtre provisionneur
avant nettoyage final, en conservant un chemin de récupération autorisé hors
PG. Si unknown : préserver intent/credential de récupération/volume, fermer
les écrivains, diagnostiquer sans retry. Arrêter les seuls services de ces deux
projets, sans `down -v` ni prune. Révoquer les bearers et identitésS3 du scénario
avec preuves de refus fraîches avant arrêter leur émetteur ; conserver données
et état final jusqu'à lecture indépendante. Aucun rollback d'un voisin.

## Budgets et limites à mesurer

Runtime nominal2.75GiB/3.5CPU : PG512MiB/1CPU, app1GiB/1CPU,
WAF256MiB/0.5CPU, **S3 permanent512MiB/0.5CPU**, runner512MiB/0.5CPU.
Un job512MiB/1CPU et une ciblePG512MiB/1CPU supplémentaires au maximum,
enveloppe conservatrice4.25GiB avec marge512MiB /5.5CPU. Cas séquentiels,
timeout20min, builders arrêtés et autres campagnes préservées.
Ne pas lancer si préflight frais RAM/disque/inodes/routes/ports/UID/images
ou budgets ne permettent pas ces plafonds ; ne pas retirer les quotas.

La recette ne prouve pas HA/PITR, #49, Vault-first ou ACCESSdirect. Restent à
observer nativement : buildjobs, S3/IAM/TLS, montage et UID, séquenceinfra,
refus, persistance et reprise logique. Les cas de préparation incomplète et
COMMIT/opération incertaine exigent aussi une observation contrôlée avant
fermetureAC : ne pas simuler leur succès par les seuls tests purs.
