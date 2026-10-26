# Configuration de ImmoAlert

[Retour au README](../README.md) · [Déploiement](DEPLOYMENT.md)

La configuration est lue depuis l’environnement et le fichier `.env`. Le gabarit complet est [`.env.example`](../.env.example). Les valeurs `votre_…` sont des espaces réservés à remplacer.

## Instance et accès

| Variable | Rôle |
| :--- | :--- |
| `BOT_TOKEN` | Jeton Telegram obtenu auprès de BotFather. Obligatoire. |
| `ADMIN_TELEGRAM_IDS` | Identifiants numériques autorisés à utiliser les commandes admin, séparés par des virgules. |
| `DATABASE_URL` | URL SQLAlchemy. En local : `sqlite:///data.db`. Compose impose `sqlite:////app/storage/data.db`. |
| `PREMIUM_FREE_DELAY_SECONDS` | Délai des alertes Free après collecte ; 300 secondes par défaut. Accepte aussi une durée comme `5m`. |

Les comptes Premium sont attribués depuis Telegram : `/grant_premium <id>`, éventuellement suivi d’une durée comme `7d`, `12h` ou `90m`. `/revoke_premium <id>` retire cet accès.

## Clés et quotas depuis le site

Dans **Sources & activité → Clés et quotas**, un administrateur peut ajouter, remplacer, désactiver et supprimer les clés **Firecrawl** et **FlashProxy**, ainsi qu’activer chaque fournisseur. Les changements s’appliquent aux prochaines requêtes sans redémarrage. FlashProxy utilise une seule clé active ; activer une autre clé désactive la précédente.

Les clés de l’environnement sont importées une seule fois dans le stockage administré. Supprimer ou désactiver ces clés dans le site ne les réactive pas depuis `.env` au redémarrage. Les variables `FIRECRAWL_ENABLED` et `FLASHPROXY_ENABLED` servent de valeurs initiales avant un choix enregistré en administration.

Les secrets sont chiffrés dans la base. **Sauvegarder ensemble `storage/data.db` et `storage/provider-credentials.key`**, dont les droits sont limités à `600`. Perdre le fichier maître empêche de déchiffrer les clés existantes. Le navigateur ne reçoit que le libellé, l’empreinte abrégée et les derniers caractères masqués. La clé complète n’est jamais consultable après enregistrement.

Le moniteur interroge les endpoints officiels de consommation environ toutes les 5 minutes ; **Vérifier le quota** permet une actualisation manuelle, avec 20 secondes entre deux vérifications d’une même clé. Ces sondes ne lancent aucune extraction d’annonce. Firecrawl fournit des crédits d’équipe : plusieurs clés d’une équipe partagent leur solde. FlashProxy fournit la consommation de chaque plan en GB décimaux. Deux plans sous la même clé API peuvent être utilisés simultanément. Un champ absent, un plan ambigu ou une erreur restent explicitement indisponibles ; aucune estimation de solde n’est présentée comme un quota réel.

Les clés et quotas de cette interface concernent les services utilisés par les collecteurs. Les cookies des portails et les paramètres avancés du navigateur restent configurés côté serveur.

## Firecrawl

Scrapling est le moteur principal. Firecrawl sert de secours pour le rendu HTML des huit connecteurs et l’extraction structurée de Leboncoin/SeLoger. L’image Docker inclut **Node.js 22 et Firecrawl CLI 1.26.22**.

```dotenv
FIRECRAWL_ENABLED=true
# Liste JSON entre apostrophes ; les clés restent sur le serveur.
FIRECRAWL_API_KEYS='["fc-premiere-cle","fc-deuxieme-cle"]'
# Ancien format conservé, facultatif, dédoublonné avec la liste.
FIRECRAWL_API_KEY=
FIRECRAWL_TIMEOUT_SECONDS=90
FIRECRAWL_CACHE_TTL_SECONDS=300
FIRECRAWL_MAX_PAGES=2
FIRECRAWL_WAIT_FOR_MS=1200
FIRECRAWL_COUNTRY=FR
FIRECRAWL_PROXY=auto
```

| Paramètre | Effet |
| :--- | :--- |
| `FIRECRAWL_CLI_PATH` | Chemin explicite du CLI ; sinon, recherche dans `PATH`. |
| `FIRECRAWL_TIMEOUT_SECONDS` | Durée maximale d’un sous-processus de collecte. |
| `FIRECRAWL_CACHE_TTL_SECONDS` | Réutilisation locale des réponses pour limiter les appels ; `0` désactive cette réutilisation. |
| `FIRECRAWL_MAX_PAGES` | Nombre de pages demandé par source et lieu ; les connecteurs limitent aussi ce nombre à leur plafond interne. |
| `FIRECRAWL_WAIT_FOR_MS` | Attente supplémentaire de rendu, en millisecondes. |
| `FIRECRAWL_COUNTRY` | Pays utilisé pour la collecte. |
| `FIRECRAWL_PROXY` | Mode du CLI : `auto` ou `basic`. |

Un rendu, une extraction et une recherche peuvent avoir des coûts et des limites différents selon le compte Firecrawl. Les variantes de lieu et les recherches simultanées multiplient les requêtes. Si le compte atteint sa limite de concurrence, réduisez `PER_SOURCE_CONCURRENCY`, les quotas par source ou `FIRECRAWL_MAX_PAGES`.

Le client suspend ses appels après les erreurs de limitation reconnues. Les connecteurs peuvent tenter leurs replis HTTP ou navigateur. Une réponse vide n’est pas une preuve qu’aucune annonce n’existe : consultez `/debug_sources` et `/debug_sample <source>`.

## Scrapling principal et FlashProxy

Le panneau **🛠️ Admin** ou `/admin` contient un bouton pour activer ou désactiver le moteur principal [Scrapling](https://github.com/D4Vinci/Scrapling). Seuls les identifiants définis dans `ADMIN_TELEGRAM_IDS` peuvent modifier ce choix, y compris en cliquant sur un ancien panneau partagé dans un groupe.

Le réglage est enregistré dans la table `runtime_settings`, créée automatiquement au démarrage. Il prend effet aux prochaines requêtes et persiste après redémarrage. Sa valeur en base a priorité sur `SCRAPLING_ENABLED` ; cette variable fournit uniquement la valeur initiale lorsqu’aucun choix n’a été enregistré.

| Variable | Défaut | Rôle |
| :--- | :--- | :--- |
| `SCRAPLING_ENABLED` | `true` | Valeur initiale du moteur principal, avant le premier choix dans le panneau admin. |
| `SCRAPLING_TIMEOUT_SECONDS` | `25` | Délai des opérations navigateur Scrapling en secondes ; accepte de 5 à 120 secondes. |
| `SCRAPLING_BROWSER_PATH` | Détection automatique | Chemin de Chromium ou Chrome. L’image Docker utilise `/usr/bin/chromium`. |

Les pages HTML passent d’abord par Scrapling (HTTP avec empreinte Chrome, puis Chromium si nécessaire), puis Firecrawl si le transport échoue. Leboncoin et SeLoger demandent aussi une extraction Firecrawl si leurs parseurs ne trouvent aucune annonce cohérente. Les six autres connecteurs conservent leurs parseurs natifs après le rendu HTML de secours. Un résultat vide peut donc toujours signaler un parseur à adapter. Les navigateurs locaux restent limités à deux simultanément.

Les clés Firecrawl sont dédoublonnées et utilisées à tour de rôle. Une clé refusée est mise en pause une heure ; une clé sans crédits, cinq minutes. La clé suivante est essayée dans le même délai total, pour les recherches comme pour les rendus et extractions. Une limitation 429 met tous les appels en pause cinq minutes, sans rotation destinée à la contourner. Plusieurs clés d’un même compte partagent son quota : elles n’ajoutent pas de crédits. Le mode CLI existant sans clé reste disponible tant qu’aucune clé explicite n’a été configurée. Après suppression ou désactivation du pool administré, il ne se réactive pas implicitement.

### Configuration FlashProxy

L’intégration utilise l’[API client officielle FlashProxy](https://www.flashproxy.com/docs/api), avec une clé créée sur le [dashboard API](https://www.flashproxy.com/dashboard/api), permissions **usage** et **proxies**. Elle ne renouvelle ni n’achète de plan. L’ancienne API revendeur n’est pas utilisée.

```dotenv
FLASHPROXY_ENABLED=true
FLASHPROXY_API_KEY=fpk_live_VOTRE_CLE
FLASHPROXY_PLAN_ID=
# Alternative pour deux plans sous la même clé (valeurs séparées par une virgule)
FLASHPROXY_PLAN_IDS=
FLASHPROXY_COUNTRY=FR
FLASHPROXY_STICKY_MINUTES=10
FLASHPROXY_TIMEOUT_SECONDS=12
```

Le bot lit `GET /api/v1/plans` si aucun plan n’est renseigné. Il sélectionne uniquement un plan actif unique ; plusieurs plans exigent de sélectionner leurs IDs dans l’administration ou dans `FLASHPROXY_PLAN_IDS`. Puis `POST /api/v1/plans/{plan_id}/proxies` génère un proxy HTTP au format URL, avec pays FR et session sticky. Le plan doit accepter cette génération et ce pays (les offres à IP fixe nécessitent une autre API et ne sont pas prises en charge par ce connecteur). Le proxy est renouvelé avant la fin de sa session, sous verrou pour éviter les appels API concurrents en double.

Les deux plans sélectionnés reçoivent les connexions en alternance, avec une session sticky en cache pour chacun. Un échec de génération suspend temporairement ce plan et permet d’essayer l’autre. Leurs quotas sont affichés séparément ; un solde inconnu reste inconnu. Les valeurs enregistrées dans **Clés et quotas** ont priorité sur les variables d’environnement. Retirer le second ID conserve le premier. Un ancien formulaire ne peut pas écraser une paire enregistrée.

La clé API sert uniquement à FlashProxy. Scrapling reçoit les identifiants du proxy généré, communs à ses étapes HTTP et navigateur. Le cache des pages est séparé lors d’un changement de configuration. Les limites 429 et `Retry-After` FlashProxy sont respectées ; les autres erreurs sont mises en pause avant réessai. Si FlashProxy est activé mais indisponible, le bot passe au secours Firecrawl, sans requête directe sur le portail. Avant réception de la clé, laisser `FLASHPROXY_ENABLED=false` : Scrapling utilise alors `PROXY_URL` ou la connexion directe.

L’état enregistré dans **Clés et quotas** est prioritaire sur la valeur initiale du `.env`, y compris lorsque Scrapling est désactivé. Le statut de session en cache ne remplace pas un test réel du trafic. Le [diagnostic du 26 octobre 2026](VALIDATION.md) vérifie les moteurs HTTP/navigateur et compare les mêmes pages avec et sans proxy ; il n’a pas constaté de gain de collecte justifiant l’activation permanente sur toutes les sources.

Le panneau admin Telegram et le site affichent le rôle des deux moteurs, le nombre de clés Firecrawl et l’état FlashProxy, jamais les secrets. Modifier les clés dans le `.env` privé du bot puis recréer le conteneur :

```bash
docker compose --project-name tgbbot up -d --no-build --force-recreate tgbbot
```

`/debug_sources` affiche le réglage et la disponibilité locale du navigateur. Le bouton refuse l’activation si le paquet ou Chromium manque. Le bouton ne teste pas l’authentification FlashProxy ; le premier appel et le statut affiché permettent de constater son résultat.

Après une mise à jour des sources ou des dépendances, reconstruire l’image :

```bash
docker compose up -d --build
```

## Cadence et périmètre des collectes

| Variable | Rôle |
| :--- | :--- |
| `DEFAULT_SOURCE_QUOTA` | Nombre de lieux retenus par source et par cycle. |
| `SOURCE_QUOTA_PER_CYCLE` | Surcharges par source, au format `seloger=1,leboncoin=1,pap=2`. |
| `PER_SOURCE_CONCURRENCY` | Nombre de lieux traités simultanément pour une source. |
| `SCRAPER_TASK_TIMEOUT_SECONDS` | Délai de base des collectes HTTP ; les sources avec navigateur ont un budget propre plus élevé. |
| `EXPAND_LOCATION_VARIANTS` | Ajoute ville seule et code postal seul lorsqu’un filtre contient les deux. |
| `INCLUDE_GENERIC_LOCATION_PASS` | Ajoute un passage sans lieu ; désactivé par défaut pour limiter le bruit. |
| `FULL_SCAN` | Ignore les quotas de lieux. `/full_scan` permet aussi un scan complet ponctuel côté admin. |
| `SCHEDULER_SOURCE_BACKOFF_MIN_SECONDS` | Attente minimale appliquée après des échecs répétés d’une source. |
| `SCHEDULER_SOURCE_BACKOFF_MAX_SECONDS` | Plafond de cette attente. |
| `SOURCE_RUN_RETENTION_DAYS` | Conservation des statistiques des collectes ; 14 jours par défaut. |
| `DEDUPE_WINDOW_HOURS` | Fenêtre de recherche des signatures identiques en base ; 72 heures par défaut. |
| `MIN_LISTING_QUALITY_FIELDS` | Seuil de champs utiles exigé par la normalisation. |

## HTTP, cookies et géocodage

`PROXY_URL` configure un proxy HTTP, HTTPS ou SOCKS5 ; `PROXY_FALLBACK_DIRECT=true` autorise une nouvelle tentative directe lorsqu’il échoue. Les cookies manuels éventuels sont lus dans `LEBONCOIN_COOKIE`, `SELOGER_COOKIE` et `PAP_COOKIE`. `SELOGER_USER_AGENT` permet d’associer un agent utilisateur au cookie SeLoger.

Le repli Selenium est optionnel : `USE_SELENIUM_FALLBACK=true` en local. Le fichier Compose fourni le désactive explicitement. Playwright peut être installé pour les chemins de repli qui le prennent en charge ; voir le [guide de développement](DEVELOPMENT.md).

Le filtrage textuel par ville et code postal fonctionne sans géocodage. Les comparaisons par rayon utilisent `ENABLE_GEOCODING=true` et `GEOCODING_USER_AGENT`, avec un agent utilisateur adapté à votre instance. Si le service de géocodage ne répond pas, une correspondance par distance ne peut pas être confirmée.

## IA facultative pour les filtres

```dotenv
AI_SCAM_ENABLED=true
AI_SCAM_ENDPOINT=https://votre-service.example/analyze
AI_SCAM_API_KEY=
AI_SCAM_MODEL=modele-de-votre-endpoint
AI_SCAM_TIMEOUT=12
```

Les variables historiques `AI_SCAM_*` configurent aussi le client de parsing des filtres. L’endpoint reçoit un objet contenant `model`, `task` et `input`, avec la tâche `parse-filter`. Il renvoie les champs du filtre, directement ou enveloppés dans `result` :

```json
{ "city": "Paris", "postal_code": "75011", "price_max": 1200, "surface_min": 35, "rooms_min": 2 }
```

Le parsing de filtre dispose d’un repli local. La détection d’arnaque active dans [`services/scam.py`](../services/scam.py) utilise actuellement des règles locales ; son adaptateur IA n’est pas appelé par le chemin de décision courant. Les signaux restent indicatifs : le bot ne certifie ni un bien ni un annonceur.

## Appliquer un changement

```bash
docker compose up -d --force-recreate
```

Compose recharge alors `.env`. Un simple redémarrage du conteneur conserve les variables de sa création précédente.

## Site auto-hébergé

Le site utilise désormais `WEB_ENABLED`, `WEB_SITE_URL`, `WEB_BIND_HOST`, `WEB_PORT` et `WEB_DIST_PATH`. Les anciennes variables de jeton de service et de secret de bridge ne sont plus utilisées. Voir [WEB.md](WEB.md) pour la configuration locale et la connexion Telegram.

## Recherches immédiates et santé des sources

Enregistrer une recherche sur le site lance immédiatement une collecte persistée par compte, même sans Telegram. Deux tâches au maximum s’exécutent simultanément, une par utilisateur ; les demandes identiques en cours sont regroupées. Modifier ou supprimer une recherche arrête ses anciennes tâches. Les sources désactivées, les quotas de collecte et les pauses automatiques restent respectés. Les annonces validées sont normalisées et dédoublonnées sans la limite des premiers messages Telegram.

Dans **Sources & activité → Santé des sources**, « Normal » demande une collecte récente avec résultats cohérents. Un contrôle dépassant la cadence configurée de plus de 5 minutes est signalé comme ancien ; une collecte vide ne prouve ni le bon fonctionnement du collecteur, ni l’absence de biens. **Diagnostiquer** collecte une zone enregistrée et met à jour les métriques sans envoyer d’alerte ; cette vérification peut utiliser les crédits habituels du fournisseur.

La passe régulière démarre toutes les **30 minutes** (`SCHEDULER_INTERVAL_SECONDS=1800`), à compter du démarrage de la précédente. Deux passes régulières ne se superposent pas ; une passe plus longue décale la suivante. Les nouvelles recherches et diagnostics manuels conservent leur lancement immédiat. Les sources désactivées ou en temporisation restent respectées.

Les erreurs HTTP, de transport, d’extraction et de validation sont journalisées avec leur source et leur étape dès détection. Chaque collecte conserve un diagnostic borné en base : URL sans paramètres ni identifiants, moteur, code HTTP, durée disponible, ville et emplacements de l’exception. Le dernier incident reste consultable après un succès dans **Détails du collecteur**. Le journal structuré `storage/logs/source-diagnostics.jsonl` tourne sur quatre fichiers de 5 Mo maximum ; la rétention des collectes utilise `SOURCE_RUN_RETENTION_DAYS` (14 jours par défaut). Aucun corps de réponse ni en-tête d’authentification n’est enregistré ; clés, cookies et mots de passe sont masqués dans les messages d’exception.

Les métadonnées communales publiques de `geo.api.gouv.fr` sont consultées directement, indépendamment du proxy des portails. Un échec de résolution ne transforme plus une recherche locale ParuVendu en recherche nationale : il produit un incident explicite. Les pages des portails continuent à respecter le proxy configuré.

## Annonces lues et date d’indexation

Ouvrir une fiche sur le site ou les informations dans Telegram enregistre une lecture pour ce profil. Le badge **Déjà lue** remplace **Nouveau** sur le site. L’état est conservé entre sessions et appareils, lors de la liaison Telegram et des regroupements de doublons. Consulter une carte dans le flux sans ouvrir sa fiche ne la marque pas comme lue.

Le temps affiché sous une carte et le tri des annonces récentes utilisent `first_seen_at`, la première indexation. `last_seen_at` continue à suivre les observations et la fraîcheur de l’inventaire, sans rajeunir une annonce à chaque collecte. [Validation des huit sources du 26 octobre](VALIDATION.md).

## Commune introuvable : code postal et code INSEE

Le champ `postal_code` d’une recherche attend le **code postal**, distinct du code INSEE de la commune. Exemple officiel : Bry-sur-Marne a le code postal **94360** et le code INSEE **94015** ([API officielle](https://geo.api.gouv.fr/communes/94015?fields=nom,code,codesPostaux)). Passer `94015` à `codePostal` renvoie une liste vide avec HTTP 200 ; cela ne signifie pas que le service est en panne.

`commune_not_resolved` décrit une absence de correspondance entre les critères saisis et les communes retournées. Le diagnostic précise la ville et le code postal à vérifier. `geolocation_unavailable` décrit un échec réseau, HTTP ou JSON du service. Les erreurs de ce service public ne déclenchent aucun repli vers le proxy des portails.

ParuVendu et OuestFrance-Immo partagent la même résolution et le même cache des correspondances réussies. Si une ville est renseignée, une commune différente n’est pas choisie pour contourner un code postal incohérent. Une requête locale non résolue ne se transforme pas en recherche nationale.

## Préférences personnelles et entretien

Les nouveaux réglages sont enregistrés par compte, sans variable `.env` supplémentaire : fuseau `Europe/Paris`, aucune plage silencieuse initiale, résumé quotidien désactivé (heure proposée `08:00`), alertes de baisse activées. Le résumé groupe les nouvelles annonces éligibles et les baisses ; les rappels de visite sont distincts. Les comptes Free gardent `PREMIUM_FREE_DELAY_SECONDS`, même si une heure de résumé arrive plus tôt. Les dates de visite sont saisies dans le fuseau du navigateur et stockées en UTC. Le fuseau des préférences règle les heures silencieuses et le résumé Telegram.

Les métriques contiennent des noms de mesure, durées et portails, sans texte d’annonce ni données privées. File mémoire limitée à 1 000 mesures, conservation SQL de sept jours et 20 000 lignes au maximum, agrégation des 10 000 mesures les plus récentes des dernières 24 heures. Les éventuelles pertes de la file sont comptabilisées par l’API d’administration.

Le contrôle de fraîcheur adapte la taille des passes au volume et à `FAVORITE_FRESHNESS_TARGET_HOURS` (6 h par défaut, minimum 6 h), dans la limite de `FAVORITE_FRESHNESS_MAX_BATCH` (12 par défaut). Deux publications au maximum par portail entrent dans une passe ; trois contrôles sont simultanés, avec un seul à la fois par portail. La cadence effective dépend de la taille des favoris et du temps de réponse des portails. Il respecte les sources désactivées et les transports configurés. Deux absences espacées d’au moins six heures confirment l’indisponibilité ; une observation positive rétablit le bien. [Compte rendu](RELEASES.md).

## Zones et critères privés

La carte charge Leaflet à l’approche de sa section, avec tuiles OpenStreetMap et attribution visible. Pour une commune identifiée sans ambiguïté, son contour est demandé à l’API officielle et mis en cache jusqu’à 24 h. Un point portail reçoit un cercle indicatif de 500 m ; un repère communal dont le contour ne peut être obtenu reçoit un cercle explicitement indicatif de 3 km. Ces rayons ne constituent pas une précision mesurée. Aucun point d’adresse n’est inventé. Le repli est réévalué après deux minutes et le cache est borné à 256 zones.

Les critères personnels (trajet, stationnement et accessibilité) sont des colonnes facultatives du suivi privé. Les anciennes sauvegardes de notes préservent ces valeurs. Les données annoncées et les vérifications personnelles restent distinctes dans le comparateur. La destination n’est pas envoyée à un service d’itinéraire ; la durée est renseignée par l’utilisateur. Le CSV est généré localement avec neutralisation des cellules susceptibles de devenir des formules.

Les recherches géographiques ont deux places d’exécution et une file de 64 tâches au maximum. Une zone déjà en cache répond immédiatement, même si des requêtes externes sont lentes. En saturation, un repère explicitement indicatif reste disponible.
