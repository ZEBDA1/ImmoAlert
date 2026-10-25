# Développement local

[Retour au README](../README.md)

Le runtime Docker utilise **Python 3.11**. La CI teste aussi **Python 3.12**. Pour Firecrawl hors Docker, prévoyez **Node.js 22+**.

## Installer

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
npm install -g firecrawl-cli@1.26.22
```

Sous PowerShell, l’activation de l’environnement virtuel se fait avec `.venv\Scripts\Activate.ps1`.

Renseignez `BOT_TOKEN` et les variables utiles dans `.env`, puis lancez :

```bash
python main.py
```

La base locale utilise `sqlite:///data.db` par défaut. Le fichier Compose utilise une base distincte dans `storage/data.db`.

## Site web

Les sources React et TypeScript sont versionnées dans `web/`. Avec Node.js 22.12+ :

```bash
npm --prefix web ci
npm --prefix web run dev
```

Vite écoute sur `http://127.0.0.1:5182` et transmet les appels API au backend local sur `127.0.0.1:8088`. Dans une configuration de développement distincte, utiliser `WEB_ENABLED=true`, `WEB_PORT=8088`, `WEB_SITE_URL=http://127.0.0.1:5182` et un `WEB_DIST_PATH` pointant vers `web/dist`. Un build initial est nécessaire au démarrage du serveur web Python :

```bash
npm --prefix web run build
```

Docker construit lui-même le site et l’intègre à l’image ; il ne dépend d’aucun dossier voisin. Le script `python3 scripts/package_plesk.py` produit une autre compilation utilisant la passerelle PHP, sans modifier le build local. [Détails du frontend](../web/README.md) et [déploiement Plesk](PLESK.md).

## Scrapling principal et replis navigateur

`requirements.txt` inclut `scrapling[fetchers]`. Pour son navigateur en local, installer Chromium/Chrome et renseigner `SCRAPLING_BROWSER_PATH`, ou utiliser la commande recommandée par le projet :

```bash
scrapling install
```

L’image Docker réutilise son Chromium système. Le moteur principal se contrôle depuis la console web ou `/admin` ; son réglage persistant est décrit dans le [guide de configuration](CONFIGURATION.md).

Certains chemins de collecte peuvent utiliser Playwright lorsqu’il est installé :

```bash
python -m pip install playwright
python -m playwright install chromium
```

Selenium est déclaré dans les dépendances ; son activation dépend de `USE_SELENIUM_FALLBACK`. Les replis navigateur dépendent des protections et du réseau de chaque portail.

## Vérifier

```bash
python -m unittest discover -s tests -v
python -m compileall -q bot database scraper services utils main.py config.py
```

Les tests utilisent des données fictives, une base SQLite de test et des appels réseau simulés. Ils ne nécessitent ni vrai jeton Telegram ni clé Firecrawl et n’envoient pas de message réel. [Périmètre détaillé](VALIDATION.md).

## Ajouter un connecteur

1. Implémenter `fetch_city(city, postal_code)` et le nom `source` à partir de [`scraper/base.py`](../scraper/base.py).
2. Retourner des `ScrapedListing` avec les éléments observés dans l’annonce ; conserver les champs inconnus sans inventer une localisation depuis la recherche.
3. Enregistrer le connecteur dans `SCRAPERS` de [`services/scheduler.py`](../services/scheduler.py).
4. Fournir un budget de temps adapté si le connecteur utilise un navigateur.
5. Tester les cas de parsing utiles avec des données fictives et les réponses d’échec hors ligne.

La normalisation, le matching et les envois sont ensuite assurés par le pipeline commun. Un changement de source doit préserver la séparation entre les threads de collecte et les tâches Telegram.
