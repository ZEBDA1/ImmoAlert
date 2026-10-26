# ImmoAlert Studio · Système de design

Direction active au 26 octobre 2026, établie avec ui-ux-pro-max puis adaptée au produit réel. Cette charte remplace les suggestions automatiques non retenues. [Intention et validation](../../DESIGN.md).

## Intention

Un espace de recherche immobilier calme, photographique et direct. Le mode sombre graphite est proposé à la première visite ; le mode clair reste accessible dans l’en-tête. Le produit fonctionne sur invitation, avec Telegram facultatif. Chaque écran doit rendre évidente son action principale et les états réels de la collecte.

## Couleurs

Les jetons de référence sont dans `web/app/design.css`. Ne pas coder les couleurs des panneaux ou du texte directement dans un composant.

- Sombre : fond `#101418`, surface `#181e24`, surface secondaire `#222b34`, texte `#f0f3f5`, texte secondaire `#b0bac5`, bordure `#36424e`, action `#b3c9f9`, texte sur action `#15233f`.
- Clair : fond `#f2f4f7`, surface `#ffffff`, surface secondaire `#e8edf3`, texte `#19232e`, texte secondaire `#536272`, bordure `#cdd7e2`, action `#315b9c`, texte sur action blanc.
- Succès, attention et erreur possèdent chacun une couleur de texte et de fond dédiée dans les deux thèmes.
- Sur une photo : texte clair avec fond de lecture discret ; boutons blancs et icônes foncées pour garder un contraste constant.

## Typographie et espaces

Manrope locale, sans police distante ni titres à empattement. Graisses 400, 500, 600 ; 12 px au minimum pour les informations secondaires, 14–16 px pour l’interface, 32 px pour un titre de vue, jusqu’à 64 px sur l’accueil de bureau. Texte courant et champs mobiles à 16 px.

Échelle d’espacement 4, 8, 12, 16, 24, 32, 48 et 64 px. Contrôles tactiles d’au moins 44 px. Surfaces plates, bordures fines, rayons souples. Les ombres servent aux panneaux superposés.

## Composition

- Accueil : une promesse courte, une action d’accès et une photographie d’ambiance explicitement légendée. Trois bénéfices pratiques, puis le lien du projet.
- Annonces : navigation supérieure compacte, titre et action de création, recherches enregistrées dans un rail sur ordinateur, sélection native sur mobile. Photos généreuses ; prix, lieu et caractéristiques directement visibles.
- Formulaire : deux étapes, erreurs près des champs, critères conservés au retour et récapitulatif avant validation.
- Administration : onglets explicites pour santé, clés/quotas, trafic et historique ; clés masquées et états sémantiques lisibles.
- Mobile : contenu défilant entre les barres de navigation sans débordement horizontal ni bouton masqué.

## Mouvement

Motion 14.6.0, import exclusivement `motion/react`. Composants `m` sous `LazyMotion`, `MotionConfig reducedMotion="user"` et `useReducedMotion`.

- Navigation : indicateur partagé à ressort (raideur 430, amortissement 38).
- Vues et panneaux : `AnimatePresence mode="wait"`, opacité et petits déplacements ; entrée 180–260 ms, sortie 120–180 ms. Garder le focus et le verrouillage du défilement jusqu’à la sortie.
- Cartes : arrivée légèrement décalée, 25 ms par carte, plafonnée à cinq incréments ; clés stables pour ne pas rejouer les arrivées au polling.
- Favoris : réponse brève à la pression ; galerie : opacité et translation de l’image courante.
- Fiche : entrée latérale sur ordinateur, verticale sur téléphone ; en-tête de fermeture accessible pendant le défilement. Photos révélées après décodage et description remplacée en fondu, avec espace réservé et placeholders pour les données réellement attendues. Les données déjà disponibles restent visibles pendant l’enrichissement.
- Galerie tactile : balayage horizontal avec conservation du défilement vertical ; garder la photo sélectionnée si l’enrichissement ajoute des images.
- Réduction des animations : supprimer les déplacements, réduire les durées à zéro et neutraliser les boucles CSS. Appareil modeste : raccourcir les transitions.
- Animer les transformations et l’opacité. Ne pas mesurer toute la page avec `layout`. Réserver les boucles aux tâches réellement actives (spinner, chargement).

## Accessibilité et comportement

Contraste du texte courant d’au moins 4,5:1, focus visible, noms accessibles pour les boutons à icône, états `aria-pressed`/`aria-expanded`, labels de champs et messages d’erreur. Piéger le focus des dialogues, fermer par Échap et restaurer le focus à la fermeture. Demander une confirmation pour la suppression d’une recherche.

La préférence locale `immoalert.theme` contient seulement `dark` ou `light`. Un script local compatible CSP l’applique avant affichage ; les onglets synchronisent le choix. Aucun identifiant de session n’est stocké dans localStorage.

## Visuels et contenu

Ne pas inventer de notes, témoignages, statistiques, succès de collecte ou quotas. Les photos d’ambiance sont distinctes des annonces. Les trois images synthétiques des captures ne sont jamais ajoutées à l’inventaire réel. [Origine et prompts](../../docs/assets/STUDIO-IMAGES.md).

L’aperçu HTML autonome provient de l’interface rendue et utilise des données fictives, sans API de production. Il permet d’essayer thèmes, navigation, recherche locale, favoris et formulaire.

## Vérifications

Compilation TypeScript/Vite ; parcours réel sur base isolée ; contrôles mobile, clavier, thème persistant et mouvement réduit ; tests de rendu et contraste sur accueil/annonces dans les deux thèmes. Voir [VALIDATION.md](../../docs/VALIDATION.md).
