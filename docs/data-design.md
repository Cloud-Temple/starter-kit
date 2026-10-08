# Concevoir les données d'un projet dérivé

Ce guide aide à écrire le design des données avant de choisir l'implémentation.
Il fournit des questions et des recommandations facultatives, sans imposer un
backend ou un modèle métier. Chaque MCP consigne ses décisions dans son dépôt.
Le [canevas DATA_DESIGN](../boilerplate/DESIGN/DATA_DESIGN.md) accompagne la copie
du gabarit et complète son [architecture](../boilerplate/DESIGN/ARCHITECTURE.md).

## Qui décide de quoi ?

| Acteur | Responsabilité |
| --- | --- |
| Factory starter-kit | Fournir le socle, ses guides et un canevas à compléter ; ne pas décider des données métier d'un MCP. |
| Projet dérivé | Décrire les données, contrats publics, droits métier, concurrence, rétention et stockage de son produit. |
| Opérateur/infrastructure | Provisionner les services retenus, leurs identités techniques et la sauvegarde/restauration selon le design du produit. |

Le [guide de création](create-new-mcp.md) place déjà les outils et règles métier
dans le projet concret. L'architecture du socle décrit ASGI, auth, WAF et
interfaces : ce guide ne la redéfinit pas. Les designs
[PolicyStore](policy-store.md) et [isolation propriétaire](owner-based-isolation.md)
sont distincts ; leur statut de spécification ne prouve pas leur implémentation.

Le kit fournit actuellement des TokenStores S3 et MCP Vault. Ce guide n'ajoute
pas de PostgreSQLTokenStore, schéma SQL, service, variable obligatoire ou nouveau
produit de sauvegarde. Choisir PostgreSQL pour les objets métier d'un MCP ne
résout pas automatiquement les problèmes de ses autres magasins.

## Commencer par les données et les opérations

Inventorier les catégories utilisées : objets métier, métadonnées, plans,
événements, fichiers, secrets, caches et projections. Pour chacune, noter :

- propriétaire et autorité persistante, ou absence de persistance voulue ;
- identifiants, états valides, relations et invariants ;
- surfaces qui la créent, lisent, modifient ou suppriment ;
- volume attendu, limites de réponse, pagination et requêtes bornées ;
- rétention, historique, effacement et dépendances externes.

Une projection ou un cache ne constitue pas une seconde autorité. Décrire son
rafraîchissement et ce qu'un client voit si la source est indisponible. Une
référence à une donnée externe n'autorise pas à en conserver une copie durable
dans un autre MCP. Les règles de conservation appartiennent au produit.

Décrire d'abord le parcours à vérifier par les interfaces existantes, puis les
transactions nécessaires. Préciser l'auth avant restitution ou mutation, les
droits par ressource/tenant, les conflits et les préconditions de suppression.
L'isolation technique de deux services ne remplace pas l'isolation métier entre
tenants d'un même service. Ne pas inventer des outils uniquement pour témoigner
du banc : le socle sépare déjà admin REST/CLI et outils MCP métier.

Pour la concurrence, choisir et documenter la révision/CAS ou une autre
précondition adaptée. Distinguer écriture confirmée, refus avant effet et
résultat incertain. Une réponse perdue après COMMIT ne signifie pas que la
transaction a échoué. Prévoir une corrélation avant l'appel et une lecture
indépendante de l'état final ; éviter le replay automatique d'une mutation
incertaine. Le produit décide du contrat exposé aux utilisateurs.

## Décrire les topologies utiles au produit

Un produit peut proposer les deux choix ci-dessous avec le même contrat public
et les mêmes droits métier. Le design indique explicitement ceux qu'il supporte.
Ces choix ne rendent aucune base obligatoire pour tous les MCP.

| Sujet | Standalone | Base externe partagée |
| --- | --- | --- |
| Installation | Projet public et composants nécessaires, avec son stockage propre. | Même application raccordée à un stockage exploité ailleurs. |
| Configuration | Topologie et endpoints explicitement sélectionnés. | Endpoints, domaine/base et identité autorisée explicitement sélectionnés. |
| Isolation | Droits app limités au domaine du produit. | Domaines et rôles séparés, accès croisés refusés et éprouvés. |
| Provisionnement | Opérateur de la stack autonome. | Opérateur/provisionneur de l'infrastructure partagée. |
| Sauvegarde | Opérateur du stockage propre. | Opérateur du stockage partagé, périmètre de reprise annoncé. |

Standalone signifie déployable depuis les sources publiques et composants
nécessaires explicitement documentés. Cela ne signifie pas « sans secrets » ou
« sans dépendances ». La recette ne doit pas exiger un checkout privé caché.
Une configuration erronée ne devrait pas être masquée par une bascule implicite
vers une autre autorité. Le produit décrit ses modes historiques et les effets
de ses nouveaux choix sans modifier silencieusement le contrat des utilisateurs.

Documenter séparément rôle application, propriétaire des objets, provisionneur
et backup. L'application n'a normalement pas besoin de DDL, de droits cluster
ou d'accès aux autres MCP. Le design précise le fournisseur de secrets, les
identités de chaque acteur, le scope des secrets, TLS, timeouts, durée des
sessions et comportement à la révocation. Les noms et défauts de configuration
restent des décisions du MCP, pas du canevas.

Séparer liveness et readiness dans le design : un process vivant ne prouve pas
que ses accès et son stockage sont utilisables. Décrire la vérification réelle
de l'identité/schema attendu et des dépendances requises, ses limites et les
diagnostics sans secrets. Le `/ready` actuel du gabarit répond comme `/health`
sans sonde de stockage : il ne certifie pas la readiness d'une future base.

## Sauvegarde et restauration : décrire le résultat attendu

L'opérateur du stockage possède la chaîne de sauvegarde ; chaque application
ne reçoit pas les droits de sauvegarder ou restaurer l'infrastructure entière.
Décrire le périmètre, la rétention, le chiffrement, les empreintes, les versions,
les credentials et les droits restitués. Un fichier exporté n'est pas une
restauration démontrée. Une archive reste un artefact de reprise, pas une seconde
autorité métier consultée pendant une panne.

La recette indique l'ordre de reprise des dépendances d'autorisation : coffre,
clés, auth et droits nécessaires avant les opérations qu'ils autorisent. Elle
identifie les accès opérateur pour restaurer ces dépendances et évite les boucles
où un secret indispensable est uniquement dans le stockage détruit. Aucun token
client ne reçoit les credentials infrastructure. Si des objets référencent des
blobs, décrire la cohérence de leur reprise et les objets manquants ; une
transaction SQL ne rend pas automatiquement une écriture S3 atomique.

Une sauvegarde logique et une chaîne physique/WAL/PITR sont des capacités
différentes. Déclarer celle qui est livrée et testée. Sur stockage partagé,
préciser si la reprise touche un domaine ou tout le cluster, et comment préserver
les voisins. Restaurer dans une cible/volume neuf, vérifier données et droits
sous les identités applicatives, puis le parcours public avec un process neuf.
Mesurer le RTO/RPO de l'épreuve réelle ; ne pas présenter HA, RPO zéro ou PITR
comme garanties acquises par le simple choix d'un moteur.

Définir le rollback avant le banc : suspension des écrivains, préservation des
sources/logs, dépendances d'auth restaurées, état restauré et droits vérifiés,
puis raccord explicite. Préserver le banc jusqu'à une lecture indépendante de
l'état final. Nettoyer seulement ses propres ressources et comparer la baseline
étrangère. Une migration de données existantes demande son propre design ; un
banc jetable n'en démontre pas la sûreté.

## Exemple de profil : chantier PostgreSQL avec Vault-first

Cet exemple illustre des choix du chantier, pas une règle universelle du kit.
Chaque MCP concerné en précise la réalisation dans son design :

- PostgreSQL porte son état structuré ; S3 porte ses fichiers/gros blobs et
  archives. Mission conserve ses propres données et références, sans miroir
  durable des données métier des autres MCP.
- Le même MCP propose une PostgreSQL propre en standalone ou un cluster externe
  partagé. Une base par domaine/MCP et des rôles séparés constituent un découpage
  à qualifier ; ce n'est pas un schéma métier défini par la factory.
- Vault est restauré et ses auth établies avant SQL, y compris les probes,
  provisionnements, backups et restores. Un refus Vault ne se contourne pas par
  des credentials directs ou périmés. La recette standalone prévoit Vault local
  ou endpoint explicitement configuré, sans dépendance privée obligatoire.
- Le fournisseur Vault exact et sa propre reprise sont documentés par le lot
  propriétaire. Le protocole MCP Vault du kit n'est pas l'API d'un autre produit
  Vault. Le bootstrap actuellement accepté avant le magasin par le socle n'est
  pas une preuve de ce contrôle Vault-first : le MCP doit traiter ce chemin.
- L'application SQL utilise les DML nécessaires et ne peut ni devenir owner,
  ni créer son schéma, ni accéder aux autres domaines. Le schema-init reste
  opérateur. Examiner aussi les permissions `PUBLIC`, autres bases, schémas et
  privilèges par défaut. [Privilèges PostgreSQL](https://www.postgresql.org/docs/18/ddl-priv.html).
- Une première recette peut être logique (`pg_dump`/`pg_restore`). Le dump d'une
  base ne contient pas les rôles globaux : couvrir leur restitution séparément.
  Les snapshots de dumps de bases distinctes ne sont pas synchronisés.
  [Sauvegarde SQL PostgreSQL](https://www.postgresql.org/docs/18/backup-dump.html).
- Le restore logique d'un domaine partagé vise une base neuve. Une reprise
  physique porte sur le cluster : récupérer un domaine peut nécessiter une
  restauration isolée puis son extraction. La chaîne WAL/PITR exige sauvegarde
  de base, archives continues et timelines compatibles, et une cible vérifiée ;
  elle se qualifie séparément. L'app n'archive pas les WAL.
  [Archivage continu et PITR](https://www.postgresql.org/docs/18/continuous-archiving.html).

## Transformer le design du MCP en preuves

Adapter cette liste à la capacité réellement choisie, au plus haut seam utile :

| Épreuve | Preuve utile |
| --- | --- |
| Projet copié puis installé depuis sources publiques | Aucun chemin runtime vers le checkout du kit/privé ; source, configuration expurgée et composants exécutés identifiés. |
| Création/lecture puis redémarrage | État final identique via surface publique et autorité persistante ; process neuf. |
| Mutation/suppression ou révocation | Préconditions métier, audit/rétention et absence/état final vérifiés. |
| Deux domaines sur stockage partagé | Contrat identique au standalone, accès croisés app et secrets refusés, voisins inchangés. |
| Mauvais scope, tenant ou allowlist | Refus avant restitution/mutation ; aucune donnée affectée. |
| Concurrence et réponse COMMIT perdue | Gagnants/conflits attendus, corrélations avant appel, incertitude déclarée sans replay. |
| Dépendance d'auth indisponible, mauvaise base/schema | Refus/readiness conformes au design, aucun fallback ou DDL implicite. |
| Backup et restore neuf | Dépendances d'auth reprises dans l'ordre choisi, données/droits/blobs vérifiés et lecture indépendante. |
| Rollback/cleanup | Sources/logs conservés, ressources propres seules nettoyées et baseline étrangère préservée. |

Réutiliser les tests ASGI, CLI, MCP et recettes existants quand ils correspondent
au produit. Les mocks vérifient un contrat ; ils ne prouvent pas le moteur réel,
le coffre ou le fournisseur S3 de production. Une recette facultative ne devient
une capacité livrée que lorsque ses preuves et ses limites sont consignées.

Ce guide et le canevas sont documentaires. Les adaptations runtime appartiennent
aux lots des MCP après leur design. Les travaux distincts
[TokenStore concurrent #28](https://github.com/Cloud-Temple/starter-kit/issues/28)
et [corpus du gabarit #33](https://github.com/Cloud-Temple/starter-kit/issues/33)
ne sont pas résolus par ce guide. Refs
[#44](https://github.com/Cloud-Temple/starter-kit/issues/44).
