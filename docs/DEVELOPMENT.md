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

## Replis navigateur optionnels

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
