# PostgreSQL local optionnel — parcours opérateur copié

Ce répertoire accompagne `boilerplate/.`, fichiers cachés compris. Il prépare
l'infrastructure **neuve** d'un produit qui possède déjà son backend PostgreSQL.
Le squelette `mon_service` n'a pas de backend SQL. Son `/health` ne prouve ni une
connexion ni une écriture. Compléter `DESIGN/DATA_DESIGN.md` et raccorder les
procédures publiques du produit avant de qualifier une opération métier.

Le mode standalone ne change que l'emplacement de PostgreSQL : mêmes sources,
image applicative, schéma, API, droits et fournisseurs de secrets. Le Compose de
base ne lance pas PostgreSQL. Aucun override n'est autochargé. Le lancement
Python, le PG accessible depuis l'hôte et l'externe partagé seront qualifiés
séparément (#49), après livraison du local (#48). Changer de cible ne transfère
pas les données.

## Préconditions

Opérateur humain : Docker Compose récent, Python3.11+, images locales vérifiées,
répertoire de travail neuf, capacité disponible et fenêtre d'exploitation
autorisée. Sur un hôte partagé, ne pas arrêter une autre campagne pour trouver
des ressources. Le lanceur ne fait aucune réservation ni diagnostic global.
Ses commandes Docker n'ont pas de mode implicite d'autorisation.

Une seule instance PG17.11 avec volume persistant ; aucune promesse de HA,
quorum, PITR, RPO ou RTO de cluster. Pas d'installateur hôte par distribution.
L'image infrastructure provient de l'image officielle PG17.11, épinglée par
index SHA256 ; les clients `psql/pg_dump/pg_restore` sont de ce même majeur.
Le build vérifie ce majeur. Un patch/version indisponible échoue, sans fallback.

Conserver le fournisseur de credentials du produit. Le kit ne crée pas de
provider applicatif et ne convertit pas son TokenStore. Pour un profil
**Vault-first**, restaurer coffre/clés/unseal/auth **avant toute sonde SQL**, puis
provision/installation/backup/restore sous les scopes existants, avec
réautorisation par job. Aucun fichier ancien/direct ne contourne un refus.
Vault conserve son OpenBao interne comme autorité ; aucun auto-appel MCP.
Les secrets SQL des produits voisins restent hors du mount technique Vault.

Le témoin FILES public peut sélectionner le provider **direct déjà présent**,
explicitement hors profil Vault-first ; cela ne prouve pas Vault-first et
n'étend pas le mode direct à ACCESS. La couverture AC2 doit porter cette limite.

## Composition et montage client distribué

Ordre **explicite**, depuis la copie autonome :

```text
docker-compose.yml
docker-compose.pg-client.yml
docker-compose.postgres.yml
docker-compose.pg-operator.yml    # jobs opt-in uniquement
```

Renommer `mon-mcp` dans la base **et** dans `docker-compose.pg-client.yml` lors
de la personnalisation normale du projet. Le fragment client distribué monte
un seul répertoire privé paramétré vers `/run/pg-client`, RO et sans création
automatique d'un chemin absent. Il contient seulement `ca.crt`,
`pg_service.conf` et `pgpass` applicatifs. Les variables du produit pointent
dans ce répertoire ; le runtime n'interprète aucune configuration SQL du kit.

Répertoire0700/fichiers0600 possédés par l'UID/GID **mesurés dans l'image**.
Ils peuvent différer pour app, jobs et PostgreSQL. `prepare.py init` mesure
ces identités sous réseau `none`, puis utilise un helper UID0 explicite,
avec seuls CHOWN/FOWNER/DAC_OVERRIDE, pour chaque nouveau répertoire borné.
Aucun `sudo` implicite, docker.sock, chmod permissif ou volume hôte système.
Si les mappings du daemon ne permettent pas ces modes, arrêter sans desserrer
les permissions. Le parent RW des archives appartient aussi à l'UID du job.

PG a son réseau privé `pg-net`, sans port publié, et son volume nommé propre au
projet. Les opérateurs utilisent TCP `postgres:5432`, TLS `verify-full`, SAN
`postgres`. Aucun psql hôte/socket d'administration comme contournement.
L'alias `postgres-wrong` est ajouté seulement par le témoin de qualification.
L'entrypoint initialise sa base système neuve ;
aucun DDL produit n'est placé dans `docker-entrypoint-initdb.d`.

## Préparer, installer et démarrer

Les commandes suivantes sont à exécuter seulement dans une fenêtre autorisée,
après build borné des images. L'app garde son image ; l'image opérateur est
distincte. Ne pas inventer un RepoDigest pour une image construite localement :
conserver le tag unique du run et vérifier le véritable `.Id` par
`docker image inspect`. Pour le témoin Tools, utiliser la recette hors gabarit
du dépôt source ; un autre produit fournit son propre raccord.

Copier `.env.example` vers `.env` et configurer le produit **avant même
`start-postgres`** : le Compose de base exige cet env_file pour toutes ses
commandes. PostgreSQL conserve `restart: "no"` par choix opérateur : après
reboot/daemon restart, démarrer explicitement la base puis vérifier TLS et
readiness avant la reprise applicative ; l'app peut déjà être relancée par sa
politique propre. Aucun service/port supplémentaire n'est exposé.

```sh
# Depuis la copie ; builder plafonné par l'opérateur, jamais simultané au runtime.
docker build -t local-pg-operator:<run> ops/postgres
python3 ops/postgres/prepare.py init \
  --project starter48-<run>-src --private /absolute/new/private \
  --database mydomain --owner mydomain_owner --app mydomain_app \
  --installer mydomain_install --backup mydomain_backup --restore mydomain_restore \
  --app-image local-app:<run> --job-image local-product-job:<run> \
  --operator-image local-pg-operator:<run>
python3 ops/postgres/prepare.py start-postgres --state /absolute/new/private/state.json
# Attendre la disponibilité TLS, sans retry de provisionnement.
python3 ops/postgres/prepare.py identity --state /absolute/new/private/state.json
python3 ops/postgres/prepare.py provision --state /absolute/new/private/state.json
```

`init` ne démarre pas de service. Il refuse le répertoire existant et prépare
certificats/credentials/files selon le profil direct choisi ; pour un produit
Vault-first, remplacer ce raccord de provisioning avec ses procédures
autorisées existantes, ne jamais invoquer cette création de credentials directs
comme secours. La CA privée reste hors app/PG ; serveur et app ne reçoivent
que leurs fichiers respectifs. Le serveur ne peut pas monter la clé CA.

`start-postgres` refuse les ressources/volumes existants portant ce projet.
`provision` ne s'exécute qu'une fois : rôle owner NOLOGIN, app sans membership,
installateur/backup/restore séparés. Les bases système de **cette nouvelle
instance locale possédée** perdent CONNECT/TEMP PUBLIC ; ce script n'est pas
une procédure de provisionnement d'un cluster partagé existant.

L'image opérateur transporte les seuls outils infra. Les procédures du produit
tournent dans son image de job, jamais dans une app privilégiée. Copier
`product-install.example.sh` en `product-install.sh`, le remplacer par les
appels canoniques du produit, le relire, puis :

```sh
python3 ops/postgres/prepare.py install-product --state /absolute/new/private/state.json
```

L'exemple reste inactif et ferme la préparation tant qu'il n'est pas remplacé.
Le hook reçoit seulement le chemin du state privé et orchestre des jobs aux
identités séparées. L'installateur est seul dans son job, le provisionneur dans
les phases infra avant/après ; app ne reçoit jamais ces credentials. Installer
les grants/probes natifs requis **avant** les commandes du produit, puis son
schéma et ses probes de domaine, son check en lecture, retirer les accès
temporaires, vérifier `/ready` réel à200 et le contrat public.

Pour lancer l'app, exporter les paramètres **non secrets** `env` du state dans
un shell opérateur ou les transmettre explicitement à Compose. Ne pas exporter
les mots de passe. Utiliser base+client+local, les montages du produit et ses
procédures usuelles. Vérifier dans le conteneur les UID/modes/RO et la lecture
des trois fichiers sans en imprimer le contenu. Seules l'écriture publique
confirmée puis sa lecture attestent le résultat métier.

## Durées TLS et renouvellement opérateur

Ce parcours reste utilisable pour un produit persistant. `init --ca-days 3650
--server-days 365` fixe les défauts (CA dix ans, serveur un an) ; fournir
explicitement d'autres durées selon la politique d'exploitation. Serveur1–825jours,
CA au plus36500jours et strictement plus longue que le serveur. CN de la CA :
`standalone-ca`. SAN serveur : `postgres`. Surveiller les expirations et agir
avant l'échéance ; il n'y a ni renouvellement automatique ni rotation CA.

```sh
python3 ops/postgres/prepare.py renew-server-cert \
  --state /absolute/private/state.json --generation ops-2027-01 --server-days 365
```

Action explicite, sous fenêtre opérateur : mesurer l'UID/GID de l'image du
conteneurPG réellement en cours ; vérifier la CA existante et au moins la durée
serveur demandée **plus un jour** de validité restante. CA expirée/trop courte :
refus sans nouvelle intention ni redémarrage, pas de CA de secours. Préparer
une nouvelle clé/CSR/feuille dans un répertoire privé unique, vérifier chaîne,
SAN et correspondance clé/certificat avant publication. Le helper borné stage
ces seuls fichiers sous l'UIDPG mesuré. Les générations initiale et précédentes
restent privées et intactes ; une bascule atomique de `server/current` sélectionne
le couple clé/certificat, sans remplacer le répertoire déjà monté par Docker.
Redémarrer **seulement postgres**, sans port ni exposition nouveaux, puis faire
une connexionTLS neuve vérifiée et comparer le certificat servi au candidat.
Un fichierconfirmed n'est écrit qu'après cette observation ; readinessproduit
et lecture publique restent à contrôler séparément.

Échec/timeout après intention : étatUNKNOWN, intention et candidats conservés,
aucun replay automatique, même avec un autre nom de génération. Inspecter le
liencurrent, le fichier `server/previous-generation-<nom>` et l'état/certificat
réellement servi sous identité opérateur autorisée. L'absence de confirmation
ne prouve pas l'absence de bascule. Retour arrière manuel : vérifier l'ancien
couple et sa validité, rétablir atomiquement current vers cette génération
retenue, redémarrerpostgres seul, puis TLS/readiness/lecture publique. Si l'ancien
certificat est expiré, ce retour n'est pas valide : conserver les données et
diagnostiquer avec la CA existante. Réconcilier/documenter l'intention avant une
nouvelle opération ; ne jamais la supprimer pour forcer un retry. La rotation
de CA nécessite une procédure distincte et n'est pas fournie ici.

Fin de provisioning : fermer l'accèsLOGIN bootstrap/provisionneur par
l'autorité opérateur existante **après avoir vérifié un accès de récupération
indépendant**, et contrôler son refus frais. Ne pas laisser `provision*` et
`issue-*` utilisables comme accès courants ; les conserver dans le stockage
opérateur privé uniquement le temps nécessaire à résoudre unUNKNOWN, puis
retirer leurs transports selon le fournisseur applicable. Ne jamais monter ces
fichiers dans l'app ou supprimer le seul accès permettant la récupération.
Le rôlebackup du témoin est **one-shot** : sa révocation ferme cette campagne.
Un backup suivant exige une nouvelle fenêtre et une nouvelle identité/scopes
via le provisionneur autorisé ; aucune réactivation automatique n'est offerte.

## Incertitude, arrêt, reprise et restauration

Avant dispatch, le lanceur écrit une intention exclusive. Un timeout ou un
processus interrompu conserve l'intention, tous les fichiers et le volume.
Une deuxième invocation refuse : inspecter sous identité autorisée, ne pas
supprimer l'intention pour forcer un retry. Une préparation partielle n'est
jamais annoncée comme installation complète ; aucun DROP/reset/réparation
runtime. Ne jamais prendre un refus ou un résultat absent/tronqué comme preuve
d'absence d'effet.

```sh
python3 ops/postgres/prepare.py stop --state /absolute/new/private/state.json
```

Cette commande fait `stop` sur le seul projet ; **pas de `down -v`, prune ou
suppression implicite**. Reprendre avec `compose start` des mêmes services,
image et volume conservés, puis nouvelle connexion et lecture du contenu
confirmé. Aucun nouvel `init` ou `provision` pour reprendre une installation.

Sauvegarde/restauration : utiliser les outils **du produit** avec écrivains
réellement suspendus, auth restaurée avant SQL, identité backup distincte et
archive privée chiffrée. Les dumps ne créent pas les rôles globaux. Restaurer
sur un autre projet/volume neufs, sans `external:true` pour volumes/réseaux PG,
avec rôles/defaults/probes natifs et identité système **de cible** avant import.
Après le schéma restauré, vérifier les probes de domaine et droits actuels,
retirer les memberships temporaires, démarrer une app neuve et relire par l'API.
Ne pas relancer le CREATE des probes déjà présentes dans le dump. Une cible
partielle reste hors service ; source et archives sont conservées.

Le rollback suspend les écrivains, préserve données/intents et rétablit l'auth
puis la seule cible vérifiée. Un retour d'image ne rétrograde pas un schéma.
Aucun raccord automatique vers une autre cible ou S3 metadata pendant panne.
