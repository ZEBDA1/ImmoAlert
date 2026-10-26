# Visuels ImmoAlert

[Retour au README](../../README.md)

La direction Studio utilise un fond graphite, un mode clair assorti, Manrope et des photographies d’ambiance. La charte active est décrite dans [DESIGN.md](../../DESIGN.md).

## Captures de l’interface

Les dix-huit captures de l’application comprennent l’accueil et le flux en sombre et en clair (`landing-demo.png`, `landing-light-demo.png`, `website-demo.png`, `website-light-demo.png`), la connexion (`login-demo.png`), la fiche détaillée (`listing-demo.png`), le flux mobile (`mobile-demo.png`), le formulaire et la progression de recherche (`search-form-demo.png`, `search-progress-demo.png`), puis les sources, les clés, le trafic et l’administration mobile (`admin-demo.png`, `providers-demo.png`, `traffic-demo.png`, `admin-mobile-demo.png`).

Elles proviennent d’une **base temporaire de démonstration**. Comptes, annonces, prix, adresses, coordonnées et résultats de collecte sont fictifs. Les photos sont des créations synthétiques utilisées pour cette démonstration, jamais des annonces réelles ni des photos reprises des portails. [Fichiers et prompts des trois images](STUDIO-IMAGES.md).

Deux captures complémentaires, `listing-loading-demo.png` et `listing-mobile-demo.png`, illustrent la fiche sur téléphone avant et après chargement. Elles proviennent du serveur fictif et des réponses retardées de `scripts/check_listing_ui.mjs`, sans données de production. Leurs visuels utilisent les mêmes images synthétiques.

Les captures ne présentent aucun secret d’exploitation, cookie, identifiant Telegram réel ou clé de fournisseur. Les dates et délais visibles correspondent à l’exécution de la démonstration. Un fond de carte de substitution est utilisé hors ligne lorsqu’une iframe cartographique est chargée.

## Reproduire les captures

Avec Docker, Node.js 22.12+ et le dépôt cloné :

```bash
npm --prefix web ci
npm --prefix web run build
docker build -t immoalert:docs .
docker run --init --rm --network none \
  -v "$PWD:/app" -w /app \
  immoalert:docs python scripts/capture_docs.py
```

L’image contient Playwright et Chromium. Le script crée ses propres profils, désactive le chargement du `.env`, ne démarre pas le bot et remplace le collecteur par une réponse locale. Il vérifie les deux thèmes, le formulaire, les favoris, la galerie, la gestion d’une clé factice et la progression d’une recherche sur le vrai backend de test. La base temporaire est supprimée à la fin. Le navigateur n’accède qu’au serveur de test local et aux illustrations interceptées.

Le visuel Open Graph `web/public/social.png` est exporté depuis `docs/assets/social-banner.svg`. La bannière du README est `docs/assets/banner.svg`. Ces compositions utilisent la charte Studio et le visuel d’ambiance synthétique déjà documenté. L’image d’accueil `web/public/images/studio-interior.webp` porte la mention « Visuel d’ambiance ».

## Aperçus publics

Les [captures du README](../../README.md#aperçu) montrent les thèmes et les parcours sur des données fictives. Le prototype interactif utilisé pendant la conception reste un outil local et ne fait pas partie de la distribution publique.

## Autres éléments

`banner.svg`, `telegram-preview.svg` et `demo-property.svg` sont des créations vectorielles du projet, sans script ni ressource externe. L’aperçu Telegram illustre une alerte et les boutons du bot ; il ne s’agit pas d’une capture de Telegram. Les deux anciens dessins restent disponibles pour l’historique et les exemples.

Les polices sont livrées avec leurs licences dans `web/public/fonts/`. Seule Manrope est utilisée par la charte Studio.

## Signalements et menus

`report-form-demo.png`, `reports-demo.png` et `report-delete-demo.png` présentent le formulaire membre, la file d’administration et la confirmation de suppression. `select-dark-demo.png` et `select-light-demo.png` montrent les menus natifs dans les deux thèmes. Ces cinq captures sont produites par `scripts/capture_docs.py` avec les mêmes comptes et biens fictifs. La suppression du scénario ne touche aucune annonce réelle.

## Chargement progressif (26 octobre 2026)

`listing-progressive-dark.png` et `listing-progressive-light.png` proviennent de `scripts/check_listing_ui.mjs`, sur le serveur local du scénario `pending`. Elles montrent les données fictives du test, respectivement en thème sombre et en thème clair avec mouvement réduit. Aucun compte ni bien de production n’est utilisé. [Compte rendu](../VALIDATION.md).

## Suivi, comparateur et préférences

Les fichiers `member-comparison-desktop.png`, `member-tracking-desktop.png`, `member-preferences-mobile-dark.png`, `member-comparison-mobile-light.png` et `member-performance-desktop.png` proviennent de `scripts/check_member_ui.mjs`, exécuté contre la vraie API et une base temporaire. Les identités, prix, notes et horaires sont fictifs. Les illustrations utilisent les images synthétiques déjà documentées ; aucune donnée de production n’est présente.

## Zones et critères personnels

Les captures `zones-map-desktop.png`, `zones-map-mobile-dark.png`, `zones-map-mobile-light.png`, `zones-personal-criteria.png` et `zones-comparison-desktop.png` proviennent du contrôle navigateur natif sur la vraie API et une base jetable. Le polygone et les fonds de tuiles sont des fixtures graphiques fictives ; ils ne représentent ni un bien ni un contour géographique réel. Identités, trajets, notes et horaires sont fictifs. Les photos reprennent les créations synthétiques déjà documentées.

`features-readable-desktop.png` provient du scénario navigateur sur une base temporaire : annonceur, référence, dates et caractéristiques sont fictifs. La capture montre la présentation des caractéristiques, sans identifiants techniques ni données utilisateur réelles.

## Promotion de la version 2.1

Les bannières SVG sont autonomes, sans script ni ressource réseau. Leurs textes et leur composition restent éditables. `scripts/render_promotion.mjs` exporte les PNG avec Chromium, en bloquant les requêtes HTTP. Il réutilise la photo synthétique Studio ; aucune image de logement réel n’est utilisée.

```bash
node scripts/render_promotion.mjs
```

`tracking-overview-demo.png` et `flashproxy-two-plans-demo.png` proviennent des contrôles réels de l’interface sur une base de démonstration jetable. Notes, logements, IDs de plan, clés masquées et quotas y sont fictifs. Les rapports et mesures provenant du serveur en service restent locaux.
