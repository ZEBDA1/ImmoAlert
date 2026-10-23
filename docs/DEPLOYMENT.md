# Déployer TGBBot

[Retour au README](../README.md) · [Configuration](CONFIGURATION.md)

## Première installation

Sur un serveur disposant de Git et Docker Compose :

```bash
git clone https://github.com/ZEBDA1/TGBBot.git
cd TGBBot
cp .env.example .env
mkdir -p storage
```

Renseignez `BOT_TOKEN`, vos `ADMIN_TELEGRAM_IDS` et la clé `FIRECRAWL_API_KEY` dans `.env`, puis lancez :

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f tgbbot
```

L’image contient Python 3.11, Chromium, Node.js 22 et Firecrawl CLI 1.26.22. Aucun port public n’est nécessaire : le bot utilise le polling Telegram. Un seul processus doit effectuer le polling pour un même jeton.

## Stockage et santé

Le volume `./storage:/app/storage` conserve `storage/data.db` entre les recréations du conteneur. Le redémarrage automatique est configuré avec `restart: unless-stopped`.

Le healthcheck exécute `scripts/healthcheck.py`. Il vérifie la configuration minimale et une connexion SQL locale ; il n’appelle ni Telegram ni les portails. Un conteneur `healthy` peut donc rencontrer une erreur d’authentification Telegram ou un blocage de source.

```bash
docker compose exec -T tgbbot python scripts/healthcheck.py
docker compose logs --tail 100 tgbbot
```

## Mettre à jour

```bash
git pull --ff-only
docker compose up -d --build
docker compose ps
```

Les migrations incluses créent les tables manquantes et ajoutent des colonnes pour les fonctionnalités du bot. Une sauvegarde avant mise à jour permet de revenir aux données précédentes en cas d’échec.

Pour une modification de configuration seule :

```bash
docker compose up -d --force-recreate
```

## Sauvegarder SQLite

La fonction de sauvegarde SQLite produit une copie cohérente pendant que le bot fonctionne :

```bash
docker compose exec -T tgbbot python - <<'PY'
import sqlite3
from datetime import datetime, timezone
stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
backup = f'/app/storage/backup-{stamp}.db'
with sqlite3.connect('/app/storage/data.db') as source:
    with sqlite3.connect(backup) as target:
        source.backup(target)
print(backup)
PY
```

La copie apparaît dans `storage/` sur l’hôte. Ce dossier contient des données d’utilisateurs et reste exclu de Git. Pour restaurer une copie, arrêtez le bot, conservez une copie de la base courante, remplacez `storage/data.db` par la sauvegarde choisie, puis relancez le conteneur.

## Dépannage

| Symptôme | Vérification |
| :--- | :--- |
| **Telegram : Unauthorized / 401** | Remplacer `BOT_TOKEN` par un jeton valide de BotFather, puis recréer le conteneur. |
| **Conflict / autre getUpdates** | Arrêter l’autre instance qui utilise le même jeton Telegram. |
| **Le bot répond lentement** | Examiner les journaux, la charge du serveur et les collectes ; vérifier que la version exécutée contient les threads de scraping dédiés. |
| **Zéro annonce sur une source** | Utiliser `/debug_sources` et `/debug_sample <source>` ; vérifier le lieu, les filtres, les cookies et les erreurs réseau. |
| **Quota ou concurrence Firecrawl** | Vérifier le compte, réduire les lieux simultanés et les pages ; voir [Configuration](CONFIGURATION.md#firecrawl). |
| **Aucune alerte malgré des annonces** | Vérifier `/status`, les critères du filtre, `/stop`, les annonces déjà envoyées et le délai Free. |

Commandes de diagnostic admin : `/admin`, `/debug_sources`, `/debug_sample leboncoin`, `/all_listings` et `/full_scan`.
