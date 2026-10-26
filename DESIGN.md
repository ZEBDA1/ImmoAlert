# ImmoAlert · Studio

Une direction photographique pour rechercher un logement dans un espace calme et direct. Le mode sombre graphite est activé par défaut ; un mode clair assorti est accessible depuis l’en-tête et mémorisé sur l’appareil. La typographie est sans empattement, les contrôles sont lisibles, et le mouvement indique les changements de contexte.

La charte actuelle est conservée dans [design-system/immoalert-studio/MASTER.md](design-system/immoalert-studio/MASTER.md). Le précédent carnet éditorial est conservé comme historique et n’est plus la direction active.

Motion pilote l’indicateur de navigation, les vues, les panneaux, les favoris et la galerie. Les préférences de mouvement réduit sont appliquées en CSS et en JavaScript. Le formulaire de recherche en deux étapes conserve les valeurs au retour ; une suppression demande une confirmation.

La fiche entre latéralement sur ordinateur et verticalement sur téléphone. Elle occupe toute la hauteur disponible, avec un en-tête de fermeture toujours accessible, y compris en paysage. Les photos gardent leur espace pendant le téléchargement et apparaissent après décodage ; la description et les caractéristiques ont des placeholders qui laissent les informations déjà connues visibles. La galerie accepte un balayage horizontal au doigt, les flèches et le clavier. Une fermeture annule la requête en cours.

Les captures du README montrent les thèmes et parcours sur données fictives. [Origine des visuels et prompts de génération](docs/assets/STUDIO-IMAGES.md).

Validation : compilation TypeScript/Vite, vrai backend sur base isolée, flux immédiat de recherche, clavier, mouvement réduit, préférence de thème persistante et contrôles de rendu/contraste aux largeurs 320, 390 et 1440 px dans les deux thèmes.

Les cartes déjà consultées portent un badge « Déjà lue » lisible dans les deux thèmes, en grille et en liste, sans atténuer la photo. Leur date relative et l’ordre chronologique suivent la première indexation. La supervision affiche la prochaine passe planifiée et des diagnostics structurés ; le dernier incident reste consultable après récupération.

La fiche propose un signalement avec commentaire facultatif, puis une confirmation de réception. L’administration possède une file dédiée pour vérifier ou retirer les biens. La suppression utilise une confirmation native avec focus initial sur l’annulation. Les listes déroulantes conservent leurs interactions natives, avec des surfaces d’options et un `color-scheme` explicites dans chaque thème.
