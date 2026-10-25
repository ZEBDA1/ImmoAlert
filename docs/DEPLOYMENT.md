# Déployer ImmoAlert

[Retour au README](../README.md) · [Configuration](CONFIGURATION.md)

## Première installation

Sur un serveur disposant de Git et Docker Compose :

```bash
git clone https://github.com/ZEBDA1/ImmoAlert.git
cd ImmoAlert
cp .env.example .env
mkdir -p storage
```

Renseignez `BOT_TOKEN`, vos `ADMIN_TELEGRAM_IDS` et la clé `FIRECRAWL_API_KEY` dans `.env`, puis lancez :

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f tgbbot
```

L’image contient Python 3.11, Chromium, Node.js 22 et Firecrawl CLI 1.26.22. Elle construit aussi le frontend depuis `web/` et l’embarque dans `/app/web_dist`. Aucun port public n’est nécessaire pour le bot seul : il utilise le polling Telegram. Un seul processus doit effectuer le polling pour un même jeton.

## Site et accès administrateur

Le site public du projet est [immoalert.work.gd](https://immoalert.work.gd), avec des comptes sur invitation. Pour héberger sa propre instance, activer le serveur web dans `.env` :

```dotenv
WEB_ENABLED=true
WEB_SITE_URL=http://localhost:8088
WEB_PORT=8088
WEB_DIST_PATH=/app/web_dist
```

Compose publie l’API et le site uniquement sur `127.0.0.1:8088`. Utiliser un reverse proxy HTTPS ou la méthode Funnel du [guide Plesk](PLESK.md) pour un accès distant. Ne pas utiliser l’adresse de l’instance publique comme origine de sa propre installation.

Après recréation du conteneur, créer un premier administrateur local :

```bash
docker compose up -d --force-recreate tgbbot
docker compose exec tgbbot python scripts/create_web_admin.py --login administrateur
```

Conserver immédiatement le mot de passe généré. L’administration propose ensuite **Accès → Créer un accès** pour les membres. Un administrateur configuré dans `ADMIN_TELEGRAM_IDS` peut aussi démarrer depuis la connexion Telegram. [Fonctionnement des sessions et rôles](WEB.md).

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

Les migrations créent les tables et colonnes manquantes. Pour les anciennes bases SQLite, la table `users` est reconstruite en conservant ses identifiants et index afin de rendre Telegram facultatif ; les relations sont contrôlées. Sauvegarder la base avant une mise à jour pour pouvoir revenir à l’état précédent en cas d’échec.

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
