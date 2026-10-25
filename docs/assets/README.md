# Visuels ImmoAlert

[Retour au README](../../README.md)

La présentation reprend l’interface du site : fond sombre `#10110f`, accent citron `#dcff43`, typographie condensée et repères numérotés.

## Captures de l’interface

| Fichier | Vue |
| :--- | :--- |
| `website-demo.png` | Flux, filtres, recherche enregistrée et favoris |
| `listing-demo.png` | Fiche avec galerie, description et publications regroupées |
| `mobile-demo.png` | Flux sur un écran mobile |
| `admin-demo.png` | Diagnostic détaillé des sources |
| `traffic-demo.png` | Métadonnées des requêtes API |
| `login-demo.png` | Connexion par identifiant et mot de passe |

Ces images sont des captures de l’application exécutée sur une **base temporaire de démonstration**. Les comptes, annonces, prix, adresses, coordonnées et résultats des collectes sont fictifs. Elles ne proviennent ni d’un compte de production ni d’une annonce réellement proposée. Les illustrations des biens sont rendues depuis `demo-property.svg`, un dessin vectoriel original ; aucune photographie d’un portail n’a été réutilisée.

Les captures ne présentent aucun mot de passe, cookie, secret, identifiant Telegram réel ou clé de fournisseur. Les dates et délais visibles correspondent à l’exécution de la démonstration. Un fond de carte de substitution est utilisé hors ligne lorsqu’une iframe cartographique est chargée.

## Illustrations vectorielles

`banner.svg`, `telegram-preview.svg` et `demo-property.svg` sont des créations vectorielles propres au projet. Les SVG ne contiennent ni script, police distante, suivi ou ressource externe.

La bannière présente le site et le bot. L’aperçu Telegram illustre une alerte et les boutons actuels de `services/notification.py` : lien d’origine, **Plus d’informations** et favoris. Il ne s’agit pas d’une capture de Telegram.

## Reproduire les captures

Avec Docker, Node.js 22.12+ et le dépôt cloné :

```bash
npm --prefix web ci
npm --prefix web run build
docker build -t immoalert:docs .
docker run --rm --network none \
  -v "$PWD:/app" -w /app \
  immoalert:docs python scripts/capture_docs.py
```

L’image contient Playwright via les dépendances de collecte et réutilise Chromium. Le script crée ses propres profils de test, désactive le chargement du `.env`, ne démarre pas le bot et ne lance aucun scraper. Les six captures sont écrites dans `docs/assets/` ; la base temporaire est supprimée à la fin. Le navigateur n’accède qu’au serveur de test local et aux illustrations interceptées.

Les polices de l’application sont livrées avec leurs licences dans `web/public/fonts/`.
