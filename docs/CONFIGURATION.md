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

Les clés Firecrawl sont dédoublonnées et utilisées à tour de rôle. Une clé refusée est mise en pause une heure ; une clé sans crédits, cinq minutes. La clé suivante est essayée dans le même délai total, pour les recherches comme pour les rendus et extractions. Une limitation 429 met tous les appels en pause cinq minutes, sans rotation destinée à la contourner. Plusieurs clés d’un même compte partagent son quota : elles n’ajoutent pas de crédits. Le mode CLI existant sans clé reste disponible si aucune clé explicite n’est configurée.

### Configuration FlashProxy

L’intégration utilise l’[API client officielle FlashProxy](https://www.flashproxy.com/docs/api), avec une clé créée sur le [dashboard API](https://www.flashproxy.com/dashboard/api), permissions **usage** et **proxies**. Elle ne renouvelle ni n’achète de plan. L’ancienne API revendeur n’est pas utilisée.

```dotenv
FLASHPROXY_ENABLED=true
FLASHPROXY_API_KEY=fpk_live_VOTRE_CLE
FLASHPROXY_PLAN_ID=
FLASHPROXY_COUNTRY=FR
FLASHPROXY_STICKY_MINUTES=10
FLASHPROXY_TIMEOUT_SECONDS=12
```

Le bot lit `GET /api/v1/plans` si aucun plan n’est renseigné. Il sélectionne uniquement un plan actif unique ; plusieurs plans exigent de préciser `FLASHPROXY_PLAN_ID`. Puis `POST /api/v1/plans/{plan_id}/proxies` génère un proxy HTTP au format URL, avec pays FR et session sticky. Le plan doit accepter cette génération et ce pays (les offres à IP fixe nécessitent une autre API et ne sont pas prises en charge par ce connecteur). Le proxy est renouvelé avant la fin de sa session, sous verrou pour éviter les appels API concurrents en double.

La clé API sert uniquement à FlashProxy. Scrapling reçoit les identifiants du proxy généré, communs à ses étapes HTTP et navigateur. Le cache des pages est séparé lors d’un changement de configuration. Les limites 429 et `Retry-After` FlashProxy sont respectées ; les autres erreurs sont mises en pause avant réessai. Si FlashProxy est activé mais indisponible, le bot passe au secours Firecrawl, sans requête directe sur le portail. Avant réception de la clé, laisser `FLASHPROXY_ENABLED=false` : Scrapling utilise alors `PROXY_URL` ou la connexion directe.

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
