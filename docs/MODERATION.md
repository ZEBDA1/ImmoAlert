# Signalements et suppression des annonces

Un membre connecté peut signaler une annonce qu’il ne retrouve plus sur son portail d’origine, depuis la fiche du bien. Un commentaire de 500 caractères maximum est facultatif. Le signalement est conservé par compte et par bien ; un envoi répété ne crée pas de doublon. Le signalement seul ne masque pas l’annonce et ne préjuge pas du résultat de la vérification.

Dans **Sources & activité → Signalements**, un administrateur consulte les messages regroupés par bien, leur auteur, leur date et leur état. Il peut ouvrir l’annonce d’origine, **Conserver l’annonce** (signalements traités), ou **Supprimer du site** après confirmation. Le filtre **Traités** conserve l’historique, la date de décision et l’administrateur lorsqu’un identifiant est disponible. La réponse est bornée aux 500 signalements les plus récents, avec priorité à ceux en attente ; la limite est explicitement indiquée si elle est atteinte.

Un administrateur dispose aussi du bouton **Supprimer cette annonce du site** dans n’importe quelle fiche visible, même sans signalement préalable. Le serveur contrôle les droits, l’origine de la requête et la confirmation de suppression ; un membre ne peut pas contourner ces contrôles en appelant directement l’API.

## Effet d’une suppression

La suppression retire le bien du flux, des correspondances, de l’affichage des favoris et de la consultation publique authentifiée de sa fiche. Les publications regroupées et alias connus sont concernés. Les alertes en attente sont annulées ; une nouvelle collecte de l’identité connue ne réactive pas le bien. Un marqueur durable garde la date et l’auteur de la suppression et permet de préserver les relations et l’historique. Une autre publication reconnue comme ce même bien par les règles de dédoublonnage est également masquée.

Cette action retire la fiche d’ImmoAlert ; elle ne supprime pas les publications sur les portails immobiliers. Les messages Telegram déjà envoyés ne peuvent pas être retirés par cette opération. Les boutons d’informations sur une fiche supprimée ne donnent plus accès à ses détails.

## API et déploiement

- Membre : `POST /api/action`, action `report_listing`, avec `listing_id` et `note` facultatif.
- Admin : `GET /api/admin/reports` pour la file et les compteurs.
- Admin : `POST /api/admin/reports`, avec `listing_id`, `operation` (`dismiss` ou `remove`) et `confirmed: true` pour retirer le bien.

Aucune donnée d’un autre membre n’est exposée dans l’état personnel : il ne contient que l’identifiant du bien signalé et l’état de son propre signalement. Les lectures et signalements sont transférés lors de la liaison de profils Telegram ; les regroupements de doublons conservent une demande de vérification en attente lorsqu’elle existe.

Le backend crée la table `listing_reports` et ajoute deux colonnes nullables aux annonces existantes. La passerelle PHP du ZIP Plesk doit être envoyée avec le nouveau frontend pour autoriser la route admin. La configuration privée et le secret de liaison restent identiques.

## Lisibilité des menus

Les options et groupes d’options natifs utilisent explicitement `--surface` et `--text`. Le document annonce `color-scheme: dark` ou `light` selon le thème. Cela corrige le texte clair sur un menu blanc tout en préservant les comportements natifs de sélection au clavier et sur téléphone.
