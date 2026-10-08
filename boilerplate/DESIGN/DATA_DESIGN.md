# Design des données — à compléter par le projet dérivé

Statut : canevas, aucune décision produit ni capacité de stockage activée.
Le propriétaire du MCP remplace les questions ci-dessous par ses choix, leurs
raisons et leurs preuves. Les sections non applicables peuvent être marquées
comme telles avec justification. Aucun backend ou schéma métier n'est imposé.
Ce document complète [ARCHITECTURE.md](ARCHITECTURE.md) sans redéfinir le socle.

## 1. Périmètre et parcours public

- Quelle capacité persistante est demandée ? Qui en est propriétaire ?
- Quel parcours utilisateur permet de la vérifier par les routes/outils/CLI
  existants ? Comment une personne et un agent inspectent-ils le même résultat ?
- Quelles opérations sont hors périmètre ? Quelles limites sont visibles ?

## 2. Inventaire, autorité et invariants

Pour chaque catégorie effectivement utilisée, compléter cette table. Ne pas
prendre ses lignes comme un modèle métier universel.

| Catégorie | Propriétaire/autorité | Identifiants et invariants | Surfaces/volume/limites | Rétention |
| --- | --- | --- | --- | --- |
| À renseigner selon le produit | À décider | À décider | À mesurer/borner | À décider |

- Quels états, relations, validations et événements sont atomiques ?
- Quels caches/projections sont reconstruisibles ? Que voit-on si la source tombe ?
- Quelles données externes sont seulement consultées/référencées ?
- Quels blobs sont référencés ? Que se passe-t-il si leur stockage échoue ou
  si leur restauration n'est pas cohérente avec les métadonnées ?

## 3. Droits et isolation

- Quelle auth précède lecture/restitution/mutation/suppression ?
- Quel scope, tenant, allowlist ou ownership s'applique réellement ?
- Quels droits ont application, propriétaire des objets, provisionneur et backup ?
- Quelle identité technique est utilisée pour chaque acteur ? Où vivent les
  secrets, leurs scopes, TLS et leur révocation ? Que peut faire un bootstrap ?
- Comment prouver le refus des accès à un autre domaine/MCP et des droits admin
  inutiles ? L'isolation des tenants métier est-elle différente de celle des services ?

## 4. Stockage et choix de déploiement

- Quelles autorités sont choisies, et pourquoi ? Quels modes publics sont conservés ?
- Le produit propose-t-il standalone, stockage externe partagé ou les deux ?
  Décrire chaque choix supporté, les composants publics nécessaires et les rôles
  opérateur. Aucun checkout privé implicite dans une recette publique.
- Les contrats et droits métier sont-ils identiques entre topologies ?
- Quelle configuration explicite sélectionne la topologie/base/domaine ?
  Quelles valeurs, défauts, timeouts et tailles de page l'opérateur règle-t-il ?
- Comment distinguer mauvaise configuration, stockage absent, accès refusé et
  état vide sans fallback vers une autre autorité ?
- Qui initialise le schéma ? Que fait l'app devant un schéma incompatible ?
- Que vérifie la readiness réelle, distincte de la liveness ?

## 5. Concurrence et erreurs

- Quelle révision/CAS ou précondition protège chaque mutation ? Quels conflits ?
- Quelles suppressions sont autorisées, à quel état, avec quel audit conservé ?
- Quel résultat est confirmé avant de répondre succès ?
- Quel statut public représente un COMMIT/résultat incertain ? Quelle corrélation
  est conservée avant l'appel et quelle lecture indépendante résout l'état final ?
- Comment empêcher un replay automatique d'une mutation incertaine ?
- Comment borner lectures, écritures, transactions et tentatives ?

## 6. Backup, restauration et rollback

- Qui possède le stockage et sa sauvegarde ? Quelle portée : domaine ou cluster ?
- Quel type est effectivement livré : logique, physique, chaîne WAL/PITR ou autre ?
  Quelles versions, empreintes, rétention, chiffrement et limites sont documentées ?
- Dans quel ordre restaurer coffre/clés/auth et données ? Quels accès opérateur
  permettent de reprendre les dépendances d'autorisation sans boucle de secrets ?
- Comment restituer identités, ownership, grants, révocations et blobs ?
- Comment restaurer sur une cible/volume neuf sans affecter les domaines voisins ?
- Quel rollback suspend les écrivains, préserve sources/logs et vérifie l'état
  avant raccord et cleanup ? Qui effectue la lecture indépendante ?
- Quel RTO/RPO a été mesuré, sous quelles conditions ? Aucune HA ou PITR garantie
  seulement parce que le moteur les permet.
- Le lot implique-t-il une migration ? Si oui, où est son design distinct ?

## 7. Qualification et état de livraison

Choisir les contrôles pertinents au seam public existant : parcours autorisé,
droits refusés, redémarrage/relecture, concurrence, résultat incertain sans
replay, restore neuf et droits restitués, rollback, voisins préservés.

| Épreuve retenue | Source/configuration exécutées | État final/preuve | Limites |
| --- | --- | --- | --- |
| À décider avant implémentation | À relever pendant qualification | À vérifier indépendamment | À publier |

Consigner décisions ouvertes, plan étroit, dépendances et séquençage dans le
projet. Distinguer design proposé, code livré, recette exécutée et capacité
qualifiée. Un mock ou un document ne prouve pas un fonctionnement réel.
