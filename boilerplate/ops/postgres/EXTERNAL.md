# Même MCP : Python hôte ou PostgreSQL externe

Ce raccord opt-in ne change ni backend, ni schéma, ni API, ni provider du produit.
Il accompagne la copie complète du gabarit. Le squelette `mon_service` n’a pas
de stockage SQL ; son health n’en constitue pas une qualification. Un dérivé
emploie les procédures publiques de son produit. Le témoin FILES public Tools
6083 utilise ses mêmes sources/lock, `files_bootstrap`, `files_backup` et CLI.
Les procédures ci-dessous restent à qualifier nativement pour #49.

## Python avec le PostgreSQL local lancé seul

Préparer les images et `.env` comme décrit dans [README](README.md), avec
versions client/serveur17 compatibles et rôles séparés. Ajouter à `init`
`--host-port 15432` (exemple : choisir un port réellement libre1024–65535).
Le fragment `docker-compose.postgres-host.yml` devient alors explicitement
actif : **bind127.0.0.1 uniquement**, aucun port dans le parcours #48 normal.
`start-postgres` ne lance ni MCP ni WAF ; installation via les mêmes jobs/hook.

```sh
# Depuis une copie neuve ; compléter les autres arguments init du README.
python3 ops/postgres/prepare.py init --host-port 15432 \
  --private /absolute/new/private --project starter49-example-src \
  --database tools --owner tools_files_owner --app tools_files_app \
  --installer tools_files_install --backup tools_files_backup \
  --restore tools_files_restore --app-image local-app:run \
  --job-image local-job:run --operator-image local-infra:run
python3 ops/postgres/prepare.py start-postgres --state /absolute/new/private/state.json
python3 ops/postgres/prepare.py identity --state /absolute/new/private/state.json
python3 ops/postgres/prepare.py provision --state /absolute/new/private/state.json
python3 ops/postgres/prepare.py install-product --state /absolute/new/private/state.json
```

Les fichiers `host-app` appartiennent à l’utilisateur hôte, en0700/0600,
distincts des montages conteneur aux UID/GID mesurés. Service : `host=postgres`,
`hostaddr=127.0.0.1`, port publié, SAN`postgres` et CA vérifiés ; passfile porte
**le port hôte**, pas5432. Les jobs et l’app Compose gardent leurs chemins
conteneur. Aucune modification hosts/DNS, aucun `sslmode=require/disable`.

Créer un venv privé et installer les dépendances **du même produit/révision**
avec son lock public, y compris libpq/psycopg si son backend le requiert.
Aucun installateur système par distribution n’est fourni. Configurer les
variables publiques métier, les bons `FILES_PG_DATABASE/ROLE/OWNER/SCHEMA`,
`FILES_PG_SYSTEM_IDENTIFIER/TIMELINE` observés et les seules identités S3/app
du cas ; aucun secret opérateur dans l’environnement app. Puis, pour Tools :

```sh
PYTHONPATH=src python ops/postgres/profiles.py \
  --state /absolute/new/private/state.json --run-module mcp_tools
```

Pour le squelette, le module reste `src.mon_service`. Le lanceur appelle
directement `python -m` du module public, fixe le serveur MCP à127.0.0.1 et
supprime les variables `PG*` héritées avant de transmettre **seulement
PGSERVICEFILE/PGPASSFILE**. `FILES_PG_CA_FILE` pointe vers la CA absolue hôte.
Il ne charge pas un nouveau fournisseur de secrets. Pour inspecter les seuls
chemins non secrets sans lancer : `profiles.py --state …`.
Vérifier `/ready` réel, les opérations publiques autorisées et refusées, puis
la persistance après arrêt contrôlé du processus Python et du seul PG possédé.
Reprendre PG via `compose start postgres`, jamais par un nouvel init/provision.

## Cible externe neuve dédiée ou partagée

PostgreSQL peut être une stack de qualification distincte ou fourni par
l’opérateur. Aucun serveur externe n’est installé, démarré, rechargé ou arrêté
par ce raccord. Le code livré couvre un profil de fixture PostgreSQL17 ; il
refuse une version incompatible et ne promet pas une qualification DBaaS.

Créer un bridge ordinaire non interne propre au cas, joint par la fixture et
les futurs jobs/app. Ce réseau n’est ni `pg-net`, ni `mcp-net`, ni le réseau hôte.
Le nom TLS du serveur doit être au SAN et résolu sur ce bridge. Pour Python,
fournir l’adresse IP réellement accessible et le port, en gardant le même `host`
pour vérifier le SAN. Une fixture locale peut publier un port loopback ; un
conteneur ne joint jamais ce port par sa propre127.0.0.1. Les contextes **hôte,
jobs, app** sont tous contrôlés sous leurs profils réels. La composition
externe inclut uniquement base + `docker-compose.pg-external.yml`, pas les
fragments PG local/client/opérateur local. Le service opérateur distinct évite
l’addition involontaire de `pg-net` lors d’une fusion Compose ; l’app rejoint
le bridge externe en plus de son `mcp-net`. Renommer `mon-mcp` dans les deux
fichiers si le dérivé change ce nom, et fournir `--app-service <nom>`.

### Précondition serveur partagé avant tout DDL

L’hébergeur propriétaire doit avoir préparé puis chargé une politique HBA
qui autorise les futurs rôles au seul domaine. Le kit **ne modifie pas** les
ACL/CONNECT/CREATE d’une base tierce ou système et ne recharge pas HBA.
PUBLIC CONNECT éventuel n’est pas une protection. Il refuse la cible si
la vue HBA, le fichier exact, les rôles/bases/ACL ou les privilèges nécessaires
ne sont pas lisibles. La fixture emploie un superutilisateur explicitement
autorisé, distinct de l’app ; ne pas assimiler cela à une capacité DBaaS.

Le contrôle préalable accepte uniquement des formes simples non citées : noms
littéraux, `all`, réseaux universels0.0.0.0/0 et::/0, méthodes `reject` ou
`scram-sha-256`. Exiger le premier match `reject` hors domaine, pour les quatre
futurs LOGIN app/install/backup/restore, socket local, TCP IPv4/IPv6, TLS et
non-TLS ; exiger SCRAM/TLS pour le domaine. Aucun interpréteur HBA général :
includes, groupes`+`, regex, jetons same*, hostname réseau, options ou citation
ambiguë entraînent un refus avantDDL. Les memberships owner futurs install/
restore n’autorisent pas à ignorer une règle groupe. Les collisions de rôle/base
refusent également ; aucune adoption de domaine existant.

Exemple **à préparer uniquement par le propriétaire d’une fixture neuve**,
avec ses vrais noms/adresses et son accès provisionneur conservé :

```text
local all postgres scram-sha-256
local all tools_files_app,tools_files_install,tools_files_backup,tools_files_restore reject
hostssl all postgres 0.0.0.0/0 scram-sha-256
hostssl tools tools_files_app,tools_files_install,tools_files_backup,tools_files_restore 0.0.0.0/0 scram-sha-256
host all tools_files_app,tools_files_install,tools_files_backup,tools_files_restore 0.0.0.0/0 reject
hostssl all postgres ::/0 scram-sha-256
hostssl tools tools_files_app,tools_files_install,tools_files_backup,tools_files_restore ::/0 scram-sha-256
host all tools_files_app,tools_files_install,tools_files_backup,tools_files_restore ::/0 reject
```

Aucun voisin non concerné n’est reconfiguré pour satisfaire la campagne. Le
provisionneur doit lire `pg_hba_file_rules` et le fichier source via SQL, créer
rôles/base, disposer du SET owner nécessaire à CREATE DATABASE OWNER et pouvoir
accorder les fonctions contrôle. Aucune augmentation de ses droits par le kit.

### Fichiers et commandes opérateur

Fournir un dossier provisionneur privé0700, trois fichiers0600 : `ca.crt`,
`pg_service.conf`, `pgpass`. Service unique`[operator]` : host/port réels,
dbname`postgres`, user provisionneur explicite, sslmode`verify-full`,
sslrootcert`/run/pg-client/ca.crt`, passfile`/run/pg-client/pgpass`,
connect_timeout`3`. Le passfile doit autoriser ce provisionneur sur postgres et
sur la future base (deux entrées, ou champ base`*` du seul profil privilégié).
Aucun certificat client implicite ou option TLS non projetable n’est accepté.
Cet accès privilégié est monté uniquement dans les jobs provisionneur ; jamais
dans app/backup/restore. Le raccord direct génère les seuls mots de passe
des rôles neufs du domaine, comme #48 ; il ne crée pas de provider alternatif.

Créer `target.json` privé0600, **sept clés exactement**, valeurs d’exemple :

```json
{"host":"pg-case","port":5432,"host_hostaddr":"127.0.0.1","host_port":15433,"network":"starter49-case-external","neighbor":"neighbor","provision_private":"/absolute/authorized/provisioner"}
```

Depuis la copie avec `.env` déjà configuré :

```sh
python3 ops/postgres/external.py init --target /absolute/private/target.json \
  --private /absolute/new/external-private --project starter49-external-src \
  --database tools --owner tools_files_owner --app tools_files_app \
  --installer tools_files_install --backup tools_files_backup \
  --restore tools_files_restore --app-image local-app:run \
  --job-image local-job:run --operator-image local-infra:run
python3 ops/postgres/external.py provision --state /absolute/new/external-private/state.json
python3 ops/postgres/prepare.py install-product --state /absolute/new/external-private/state.json
```

`init` prépare seulement les fichiers après validation RO du réseau/composition.
`provision` lit les préconditions avant le premier CREATE ; l’intention exclusive
précède tout DDL. La branche externe crée uniquement base/rôles possédés, owner
NOLOGIN et connexions séparées ; aucun REVOKE sur postgres/template1/voisin.
Après création, **avant droits/probes métier et serveur**, trois contextes
authentifient la vraie identité app vers le domaine en TLS. Les rôles auxiliaires
sont testés depuis hôte/job. Chacun doit observer les refus de connexion HBA
**SQLSTATE28000** vers voisin, postgres, template1. 28P01,42501, panne réseau,
CA/hostname erroné ou liste vide ne prouvent rien. Échec à cette étape : NOLOGIN
des seuls rôles du cas et arrêt de leurs sessions, aucun produit installé ;
échec ou ACK perdu de ce retrait reste UNKNOWN à inspecter.

La sonde infra n’est pas un driver applicatif. Après la connexion témoin psycopg
au domaine, elle émet uniquement SSLRequest, exige S et TLS vérifié même CA/SAN,
puis StartupMessagev3 user/database et **un ErrorResponse complet28000 suivi
de fermeture**. Tout AuthenticationRequest, autre code/type ou transport refuse ;
elle n’envoie jamais de password/SASL et ne publie aucun message serveur libre.
Cette observation complète le précontrôle HBA ; le code28000 seul ne diagnostique
pas une cause générale d’authentification. Sources : [flux PostgreSQL17](https://www.postgresql.org/docs/17/protocol-flow.html),
[formats des messages](https://www.postgresql.org/docs/17/protocol-message-formats.html).

Le hook reste celui du produit, apply/check et retrait installateur ; pour le
témoin, choisir `compose.external-jobs.yml` généré par `copy_witness.py` et
`--app-service mcp-tools`. Après retrait des rôles one-shot, recontrôler l’app
seule avant serveur par `external.py check-contexts --state … --role app`.
Lancement Python identique au cas local via `profiles.py --run-module …` ;
Compose via base+fragment externe, avec les chemins/identités du state et les
variables publiques de l’app. Le code, l’image quand utilisée et le lock ne
changent pas entre topologies. Le contrôle initial utilise Python/psycopg du
produit aussi dans l’image de job et dans l’image app, avant de lancer le MCP.

## Arrêt, incertitude et restauration

Ne pas relancer un CREATE, init/provision ou mutation publique non confirmée.
Préserver intention, fichiers, données et accès récupération. Les refus RO
peuvent être observés de nouveau ; ils ne confirment pas un ancien effet ambigu.
`prepare.py stop` n’arrête que les services du projet MCP ; aucun service PG
externe n’appartient à ce projet. Reprendre les mêmes app/volume cible et relire
les données publiques sans provisionner une seconde fois.

Changer les profils n’effectue aucun transfert. Suspendre écrivains puis suivre
les mêmes outils publics backup/export et restore/import vers **une autre cible
neuve**, identité cible relevée avant import ; retirer les droits temporaires,
contrôler les données/API et mettre l’ancienne cible hors écriture avant rollback.
Sur partagé, ne jamais stopper le serveur ou changer ALLOW_CONNECTIONS du voisin.
Ne pas désactiver le compte de l’hébergeur : retirer les seuls transports/
logins temporaires du cas après vérification de récupération indépendante.
Conserver PKI/archives/volumes ; aucun prune/down-v par défaut.

La recette de qualification conserve des domaines S3/PG propres à #49, inclut
lecture complète du voisin sous sa propre identité avant préparation, après
préparation et en dernier, et teste fautes/refus/restart depuis Python local et
externe. Les cas restent dans les tests standalone existants ; une simulation
de ces raccords ne prouve ni un run natif, ni HA/PITR, ni ACCESS/Vault-first.

### Limites de raccord à observer à la qualification

`app-probe-job` conserve une copie privée du credential app sous l'UID du job
jusqu'au nettoyage opérateur du domaine ; ne pas la traiter comme un nouveau
scope ni la monter ailleurs. La même révocation app ferme les deux transports.
Le copieur du témoin requiert PyYAML aussi sur le chemin #48 ; `fault.py` utilise
`sed` avec le hostname échappé, dont le cas avec tiret reste à observer nativement.
La sonde exige la fermeture TLS après l'ErrorResponse : un serveur ne donnant pas
une fermeture TLS propre reste non confirmé, jamais un succès déduit d'un EOF
anormal. Ces limites ne changent ni les droits ni les oracles du produit.
