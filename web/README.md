# Interface ImmoAlert

Site public : **https://immoalert.work.gd**. Les accès sont créés par un administrateur ; Telegram est facultatif.

Le frontend React, TypeScript et Vite se trouve dans ce dossier. L’API Python et les profils du bot sont partagés avec le site. Les polices sont servies localement et leurs licences sont conservées dans `public/fonts/`.

## Direction et interactions

La charte Studio est décrite dans [DESIGN.md](../DESIGN.md) et [le système de design](../design-system/immoalert-studio/MASTER.md). Le site utilise Manrope servie localement, un accueil photographique et deux thèmes : graphite par défaut et clair. `theme-init.js` applique le choix avant le premier affichage ; `theme.tsx` le mémorise et le synchronise entre onglets.

Motion for React est chargé avec `LazyMotion` et `MotionConfig reducedMotion="user"`. La navigation partage un indicateur animé ; les vues, panneaux, favoris et images utilisent les transformations et l’opacité. `motion.tsx` garde les panneaux montés jusqu’à la fin de leur sortie, avec le focus et le verrouillage du défilement. Les durées s’adaptent aux appareils modestes et à `prefers-reduced-motion`.

Les recherches enregistrées occupent une colonne latérale sur ordinateur et un sélecteur natif sur mobile. Le contenu mobile défile entre les deux barres de navigation. La recherche se crée en deux étapes et préserve les critères au retour. Supprimer une recherche demande une confirmation ; les favoris et la galerie restent utilisables au clavier. Les [captures publiques](../README.md#aperçu) illustrent la direction sur des données fictives.

La fiche d’annonce entre depuis le côté sur ordinateur et depuis le bas sur téléphone. Son en-tête reste accessible pendant le défilement. `progressive-image.tsx` réserve l’espace et attend le décodage avant le fondu d’une photo, y compris depuis le cache ; une erreur produit un état explicite. La description, les caractéristiques et la carte possèdent leurs propres chargements. `listing-motion.css` adapte la fiche aux petits écrans, au paysage, aux zones de sécurité et au mouvement réduit. La galerie accepte aussi un balayage horizontal tactile sans bloquer le défilement vertical.

Le scénario [check_listing_ui.mjs](../scripts/check_listing_ui.mjs) vérifie ces comportements avec des réponses retardées, des photos lentes ou cassées, une fermeture pendant la requête, les deux thèmes et six tailles/orientations. Voir [les commandes de validation](../docs/VALIDATION.md).

## Développement

Depuis ce dossier, avec Node.js 22.12+ :

```bash
npm ci
npm run dev
```

L’interface écoute sur `http://127.0.0.1:5182`. Le proxy Vite transmet `/api` à `http://127.0.0.1:8088`. Pour tester les écritures en développement, configurer l’origine autorisée du backend sur `WEB_SITE_URL=http://127.0.0.1:5182` et son port sur `WEB_PORT=8088` dans un environnement de développement distinct.

## Construction

```bash
npm run build
```

La vérification TypeScript précède le build dans `dist/`. Docker construit automatiquement ce frontend et l’embarque dans `/app/web_dist` ; aucun autre dépôt ni dossier voisin n’est requis.

Pour Plesk mutualisé, depuis la racine du dépôt :

```bash
npm --prefix web ci
python3 scripts/package_plesk.py
```

L’archive est produite dans `dist/immoalert-plesk.zip`. Elle contient un build utilisant la passerelle PHP ; le build local reste inchangé. Voir le [guide Plesk](../docs/PLESK.md).

## Lecture et ancienneté

Ouvrir une fiche appelle l’action idempotente `mark_read`, puis met à jour le badge du profil. La mutation reste indépendante du chargement de la fiche et n’est pas annulée à sa fermeture. Le polling récupère les lectures enregistrées depuis un autre appareil. Les cartes et le tri chronologique utilisent `first_seen_at` ; `last_seen_at` sert à vérifier la présence du bien.

Le panel affiche la cadence et la prochaine passe, les événements de diagnostic et le dernier incident même après récupération. La progression des nouvelles recherches reste distincte des passes planifiées.

## Chargement progressif des fiches

L’API attend au plus 150 ms l’enrichissement externe avant de renvoyer la fiche enregistrée. La récupération et le rapprochement s’exécutent dans une tâche partagée ; une lecture en cache récent ne les relance pas. L’interface conserve le contenu connu, affiche un indicateur discret et reçoit les compléments par SSE ou attente JSON bornée. Une cadence progressive de deux à cinq secondes reste disponible pour un ancien backend. Après huit secondes, le texte explique la lenteur du portail ; après deux minutes, une nouvelle tentative est proposée. Le mouvement réduit affiche un indicateur fixe.

Le module d’administration et son CSS sont chargés à son ouverture. `scripts/check_listing_ui.mjs` vérifie ce chargement différé ainsi que les états lents, partiels et les délais. [Mesures, validation et prochaines évolutions](../docs/VALIDATION.md).

## Inventaire et suivi personnel

`use-inventory.ts` consomme la pagination/les filtres côté serveur et ne conserve que la page utile. `polling.ts` sérialise l’actualisation et la suspend avec la visibilité. `use-listing.ts` gère la fiche immédiate, SSE en accès natif ou les attentes JSON bornées pour la compilation PHP, avec un délai de récupération maximal de deux minutes. La galerie précharge une seule voisine, sauf économie de données ou connexion lente.

Le comparateur, le suivi privé, les préférences et l’inbox sont chargés à l’ouverture. Les vues conservent les thèmes Studio, les labels clavier et les défilements internes sur mobile. Le menu compact est utilisable aussi entre 901 et 1050 px. [Réalisation, captures et vérifications](../docs/RELEASES.md).

## Carte par zone et critères de comparaison

`location-map.tsx` est chargé par `IntersectionObserver` à l’approche de la carte. Leaflet, son CSS et sa licence restent hors du téléchargement JavaScript initial. La géométrie provient de la route privée `/api/listings/{id}/area`, avec un repère indicatif immédiat puis un contour officiel lorsque disponible. Zoom, déplacement, recentrage et erreur/réessai sont contrôlés dans le navigateur, y compris en mouvement réduit. Une erreur du moteur est isolée à cette section.

`comparison-data.ts` centralise les valeurs du tableau et de son export CSV. Les critères privés sont séparés des informations de l’annonce ; le comparateur ne reçoit pas le texte des notes. `scripts/check_comparison_csv.mjs` vérifie les guillemets, séparateurs, caractères français et tentatives d’injection de formule.
