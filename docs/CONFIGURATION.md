# Configuration de TGBBot

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

Leboncoin et SeLoger utilisent principalement le CLI Firecrawl. L’image Docker inclut **Node.js 22 et Firecrawl CLI 1.26.22**.

```dotenv
FIRECRAWL_ENABLED=true
FIRECRAWL_API_KEY=votre_cle_firecrawl
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
