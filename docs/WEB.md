# Site ImmoAlert auto-hébergé

Site du projet : **https://immoalert.work.gd**. Les sources du frontend sont versionnées dans [`web/`](../web/README.md). Ce guide décrit aussi l’hébergement de sa propre instance.

Le site est servi par `services/web_api.py` dans le même processus que le bot. Il utilise directement la base existante pour les annonces, recherches, favoris et accès. L’ancien connecteur d’hébergement et l’authentification ChatGPT ont été retirés du fonctionnement du site.

## Accès et administration

Le site propose **Se connecter** par identifiant et mot de passe. Aucun formulaire ni endpoint d’inscription publique n’est disponible. Seul un administrateur peut créer un accès dans **Accès**, avec un mot de passe généré automatiquement et le choix du Premium. Les identifiants sont normalisés sans distinction de casse. Le mot de passe s’affiche une seule fois, est dérivé avec scrypt et un sel propre au compte, et n’entre pas dans les journaux ou l’historique des commandes.

Telegram est facultatif. **Mon compte → Relier Telegram** demande une approbation explicite dans le bot et un nonce valable dix minutes. Les recherches et favoris du profil local sont regroupés avec ceux du profil Telegram, en préservant les anciennes données. Un compte Telegram déjà relié à un autre accès ne peut pas être récupéré. La connexion Telegram directe reste disponible pour les administrateurs configurés et les profils déjà invités ; démarrer le bot ne crée pas un accès au site.

Les sessions expirent après trente jours. Leur identifiant aléatoire est renouvelé à la connexion et seule son empreinte SHA-256 est enregistrée. Les cookies sont HttpOnly, SameSite=Lax et Secure sur HTTPS. Réinitialiser un mot de passe ou désactiver un compte révoque ses sessions. Les droits sont relus pour chaque action : rôle du compte web ou appartenance à `ADMIN_TELEGRAM_IDS`. Un membre ne peut pas consulter les autres comptes ni les routes de supervision.

**Sources & activité** affiche les huit sources, leur activité en cours, les zones traitées, les volumes bruts, normalisés, rejetés et uniques, les nouvelles annonces, les erreurs et délais dépassés, les dates de succès/échec et les temporisations. L’historique présente les 200 dernières collectes. Le trafic des appels API des utilisateurs est actualisé toutes les 2,5 secondes, avec les requêtes encore en cours, le compte, la méthode, la route, le code HTTP et la durée. Les actifs correspondent aux comptes ayant effectué une requête dans les cinq dernières minutes. Les sondes de supervision et de santé ne s’enregistrent pas elles-mêmes.

L’historique de trafic conserve au maximum 20 000 requêtes sur trois jours. Il ne contient ni corps, ni paramètres d’URL, ni cookies, ni mots de passe. Les fichiers statiques servis par Plesk ne transitent pas par ce journal API. **Accès** permet de créer, désactiver, réactiver et réinitialiser un compte, ainsi que de modifier le Premium. Les préférences, favoris et abonnements utilisent le même profil que le bot. Les profils sans Telegram ne reçoivent pas d’alertes Telegram.

Les collectes longues restent asynchrones et une seconde collecte manuelle simultanée est refusée. Les commandes interrompues sont signalées. Pour un serveur neuf, un administrateur système peut initialiser son accès avec `python scripts/create_web_admin.py --login administrateur` ; aucun compte par défaut n’est livré.

## Exploitation

`WEB_ENABLED=true`, `WEB_SITE_URL=http://localhost:8088`, `WEB_BIND_HOST=0.0.0.0`, `WEB_PORT=8088`, `WEB_DIST_PATH=/app/web_dist`. Pour un accès distant, adapter `WEB_SITE_URL` à l’origine HTTPS du site ou utiliser la passerelle signée de Plesk.

Docker publie seulement `127.0.0.1:8088`. L’image construit le frontend à partir de `web/` et l’embarque dans `/app/web_dist`, sans volume vers un dossier externe. Un reverse proxy HTTPS ou un tunnel Funnel peut donner accès à ce backend. Avec Plesk, le navigateur appelle la passerelle PHP du même domaine ; celle-ci signe les requêtes vers le serveur Python.

Après modification du frontend ou de Python : `docker compose up -d --build tgbbot` depuis le dépôt. Pour un build de développement hors Docker : `npm --prefix web run build`. Vérification : `GET /api/health`. Les logs API n’enregistrent ni cookies, ni nonces, ni corps de requêtes.

Une session navigateur expire après trente jours ; une approbation Telegram après dix minutes. Les annulations et erreurs sont indiquées au visiteur, et les filtres invalides restent modifiables dans le formulaire.

## Inventaire

Le site affiche les locations cohérentes réellement vues sur les portails depuis moins de trente jours, par pages de 18 annonces. Filtres, tri et comptage portent sur tout l’inventaire côté serveur, au-delà des anciennes 800 fiches. Les favoris restent accessibles séparément, avec leurs notes privées et la date de contrôle de chaque publication. L’actualisation est suspendue quand l’onglet est masqué ; les requêtes ne se chevauchent pas. Voir [l’audit des sources](VALIDATION.md) pour les limites de collecte.

Migration du 24 octobre 2026 : copie SQLite avant ajout des tables web, sauvegarde privée de la configuration et image `tgbbot:before-selfhost-20261024` pour référence. Aucun compte, filtre, favori ou historique d’alerte n’est supprimé par cette migration.

## Fiches complètes et doublons entre plateformes

Une fiche représente un bien et conserve ses publications dans `listing_publications`. Les liens de chaque portail restent accessibles ; le filtre par plateforme retrouve aussi les publications secondaires. La détection compare la localisation, le loyer, la surface, les pièces, puis exige une photo commune ou un passage descriptif suffisamment distinctif. Les simples critères identiques ne suffisent pas à fusionner deux logements.

Avant une alerte pour une nouvelle annonce ambiguë, le bot récupère les détails de celle-ci et de deux candidats au maximum. L’inventaire existant est progressivement vérifié par lots de deux, avec une pause d’une minute entre les lots. Les sources désactivées sont respectées. Les fusions conservent les anciennes fiches, redirigent leurs identifiants et regroupent les favoris et historiques d’alertes.

`GET /api/listings/{id}` exige une session authentifiée et renvoie la description complète, toutes les photos récupérées, les caractéristiques publiées, les publications et les coordonnées. Les aperçus du flux restent légers. L’ouverture de la fiche déclenche l’enrichissement partagé avec le bouton **Plus d’informations** du bot. Le site propose une galerie avec miniatures, flèches et agrandissement ; Telegram affiche le texte paginé et un lien vers les photos sur le site.

La collecte réutilise Scrapling et Firecrawl. Elle lit les données structurées, les données d’hydratation liées à l’identifiant de l’annonce et les blocs de description/galerie ; elle exclut les recommandations. Les détails disponibles sont mis en cache 24 heures ; un échec, 15 minutes. Une collecte suivante ne remplace pas une longue description ou une galerie par un simple extrait. Les portails peuvent bloquer une page ou ne pas publier certains champs : la fiche conserve alors les informations déjà connues et indique la limite.

La carte utilise [OpenStreetMap](https://wiki.openstreetmap.org/wiki/Export#Embeddable_HTML). Les coordonnées du portail sont présentées avec une précision à confirmer. En leur absence, le centre de la commune est recherché via l’[API publique de découpage administratif](https://geo.api.gouv.fr/decoupage-administratif/communes) puis conservé en base : le repère est explicitement approximatif. Une commune ambiguë n’est pas choisie au hasard. Cette localisation du bien fonctionne indépendamment de `ENABLE_GEOCODING`, qui garde son rôle existant pour les filtres par rayon. Aucune position du visiteur n’est demandée.

## Version Plesk mutualisée

Le [guide Plesk](PLESK.md) décrit l’archive prête à envoyer depuis le gestionnaire de fichiers : frontend compilé et passerelle PHP 8.2+. Les variables `WEB_PROXY_ORIGIN` et `WEB_PROXY_SECRET` autorisent une origine HTTPS distante avec des requêtes signées, horodatées et non réutilisables. Le secret reste hors de `httpdocs`. Le bot et les collecteurs continuent à tourner sur le serveur Python ; aucun accès public supplémentaire n’est activé automatiquement.

## Suivi des favoris et alertes

Les favoris proposent un comparateur de deux à quatre biens et un suivi personnel : notes privées, étape de contact, visite et rappel facultatif. Les historiques de loyers sont séparés par publication afin de distinguer une vraie baisse d’un écart entre portails. La disponibilité indique la date du contrôle ; deux constats d’absence espacés d’au moins six heures sont nécessaires pour confirmer une disparition. Une erreur reste un échec de vérification.

**Mon compte** règle le fuseau, les heures silencieuses, le résumé quotidien Telegram et les alertes de baisse. Le délai Free reste appliqué. **Alertes** conserve les baisses et rappels du compte ; les envois Telegram nécessitent une liaison. L’administrateur consulte les médianes et 95es percentiles dans **Sources & activité → Performance**. [Réalisation et limites](RELEASES.md).

## Localisation par zone et comparaison personnelle

La fiche présente une zone réellement attachée à la carte, avec zoom, déplacement, clavier et recentrage. Le repère disponible apparaît d’abord ; le contour officiel d’une commune le remplace lorsqu’il est résolu. Un cercle indicatif reste explicitement distinct d’un contour administratif et d’une adresse. L’échec du fond de carte permet de réessayer et conserve la zone ; une erreur du composant laisse le reste de la fiche utilisable.

Dans **Votre suivi → Vos critères de comparaison**, chaque favori peut recevoir une destination, un mode et une durée de trajet relevée, puis un stationnement et une accessibilité vérifiés. Le comparateur affiche ces critères privés et permet un export CSV. Une absence de donnée reste une absence, sans score ou temps de trajet inventé. [Livraison des zones et suites de la ROADMAP](RELEASES.md).

## Libellés et dates des caractéristiques

Les caractéristiques exposées à l’utilisateur passent par une présentation métier commune avec Telegram : libellés français, unités, dates numériques avec secondes à l’heure de Paris, suppression des paramètres techniques et liens nommés. Les anciennes annonces sont corrigées à la lecture ; les données sources restent intactes. [Règles et validation](RELEASES.md).

## Retrouver les suivis des logements

**Mes suivis** réunit les favoris et les notes conservées des anciens favoris. L’accès est disponible dans la navigation, dans le menu mobile et depuis **Mes favoris → Mes suivis**. Rechercher un titre, une ville, une note ou une destination de trajet, filtrer par étape ou par archivage, et retrouver les visites à venir. Les résultats sont paginés par 20 ; le tri initial place les prochaines visites en premier. « Ouvrir le suivi » mène au formulaire de la fiche. « Reprendre le suivi » remet explicitement un ancien favori dans la sélection. Une annonce retirée laisse consultable la note personnelle, sans republier ses détails. Les rappels de visite nécessitent un favori actif.

Dans **Administration → Sources & activité → Clés et quotas → FlashProxy**, les champs **Plan ID FlashProxy 1** et **Plan ID FlashProxy 2 (facultatif)** permettent d’utiliser deux plans en même temps sous la même clé API. La paire est persistante. Les connexions alternent entre deux sessions en cache et les quotas sont affichés par plan après vérification. Si la génération d’une session échoue, l’autre plan est essayé. Retirer le second ID conserve le premier ; vider les deux active la sélection automatique si un seul plan est actif. Les anciens quotas sont effacés lors d’un changement de paire.
