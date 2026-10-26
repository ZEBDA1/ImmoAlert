# Architecture de ImmoAlert

[Retour au README](../README.md)

## Commandes et recherches

`main.py` initialise la base et lance le polling aiogram ainsi que le scheduler. Les handlers de `bot/` gèrent les comptes, les commandes et l’assistant de filtres. Les critères sont enregistrés avec leur utilisateur ; le mode Premium permet plusieurs filtres nommés.

Un enregistrement de filtre peut déclencher une recherche parmi les annonces connues, puis une collecte des annonces déjà en ligne. L’entrée dans l’assistant conserve les alertes existantes actives. `/start` quitte un assistant inachevé et réactive le compte.

## Collecte et réactivité

Le scheduler construit les lieux à visiter depuis les filtres actifs. Les paramètres de quota et de variantes déterminent le périmètre de chaque source. Les connecteurs HTTP et navigateur produisent des objets `ScrapedListing`.

Les collectes synchrones utilisent un `ThreadPoolExecutor` dédié, distinct de l’exécuteur par défaut d’asyncio. Ce dernier reste disponible pour les tâches du bot, dont les résolutions DNS utilisées par les échanges Telegram. Un timeout asyncio arrête l’attente d’une collecte ; une requête synchrone déjà partie peut continuer jusqu’à son propre timeout HTTP. Cette séparation évite qu’elle monopolise les workers utilisés par Telegram.

Les appels Firecrawl passent par des sous-processus avec timeout, fichiers de schéma temporaires et cache local. Le mode d’extraction JSON utilise un schéma, y compris pour le repli par consigne libre. SeLoger recherche aussi l’URL canonique de la zone avant la collecte.

La sauvegarde d’une recherche web crée immédiatement un job dans `services/search_jobs.py`. Son état et sa progression sont conservés dans `runtime_settings` par compte. Deux jobs peuvent s’exécuter simultanément, une seule tâche par utilisateur ; modifier ou supprimer les critères annule les anciens résultats. `collect_filter_inventory_once` réutilise les collecteurs, la normalisation, la persistance et les alertes, même si l’utilisateur n’a pas de compte Telegram. Les sources désactivées, leurs quotas et leurs pauses restent appliqués. Le site invalide ses caches quand la progression change pour présenter les nouvelles fiches sans attendre l’expiration habituelle.

## Qualité et correspondance

`scraper/quality.py` normalise les annonces et extrait les valeurs utiles. `utils/text.py` traite les prix français, surfaces et pièces. Une localisation manquante reste manquante : le lieu recherché n’est pas ajouté comme preuve de localisation.

La déduplication associe identifiant de source, publications d’un même bien, comparaison des descriptions/images et fenêtre de conservation. Le matching applique les limites et le type de bien, puis la zone. Le score compare notamment le prix au budget de l’utilisateur et la surface à son minimum. Les comparaisons par rayon nécessitent le géocodage optionnel.

## Envoi et persistance

Les tables principales sont `users`, `filters`, `listings`, `favorites`, `sent_alerts`, `pending_alerts` et `source_runs`.

- Les annonces Premium sont envoyées dès leur traitement ; les comptes Free utilisent une échéance configurable en base.
- Un envoi enregistré est associé à l’utilisateur et à l’annonce pour éviter les répétitions lors des collectes suivantes.
- Une alerte annulée peut être reprogrammée en réutilisant son enregistrement.
- La modification ou la suppression d’un filtre annule les alertes qui lui sont associées ; celles des autres filtres sont conservées.
- Les erreurs Telegram temporaires conservent l’alerte pour une nouvelle tentative. Un blocage du bot par l’utilisateur met son compte en pause.

L’envoi réseau et l’écriture SQL ne forment pas une transaction distribuée : une interruption entre les deux peut laisser une incertitude sur l’envoi. Les enregistrements persistants réduisent les doublons et les pertes, sans garantir un envoi unique dans tous les scénarios de panne.

## Diagnostic

`source_runs` conserve les volumes bruts, normalisés, rejetés et uniques, ainsi que les erreurs, délais et statuts de chaque collecte. Les échecs répétés déclenchent une attente progressive par source. `/debug_sources` expose ces informations et l’état Firecrawl.

Le healthcheck Docker contrôle uniquement la configuration et la connexion SQL. Il est indépendant de la disponibilité de Telegram et des sites collectés.

## Site, identités et administration

Le frontend React/TypeScript de `web/` est construit par Docker et servi par `services/web_api.py`. Les sessions web portent l’identifiant interne d’un profil ; `users.telegram_id` peut être nul. `web_accounts` conserve les identifiants de connexion, les dérivés scrypt des mots de passe, le rôle et l’état d’accès. Les routes d’administration relisent les droits avant chaque opération.

Les cookies de session sont HttpOnly, SameSite=Lax, et Secure sur HTTPS. Le cookie est renouvelé à la connexion ; seule son empreinte est enregistrée dans `web_sessions`. La réinitialisation d’un mot de passe ou la désactivation d’un accès révoque les sessions correspondantes. La connexion Telegram directe est réservée aux administrateurs configurés et aux profils déjà invités. Un membre connecté peut relier Telegram après approbation dans le bot ; les recherches et favoris sont regroupés sans écraser les relations existantes.

Le site affiche l’inventaire cohérent observé depuis moins de trente jours par pages bornées, sans limite globale à 800 fiches. `/api/bootstrap` renvoie les métadonnées et `/api/inventory` applique pagination, tri, filtres et droits utilisateur côté serveur. Les aperçus sont légers ; `GET /api/listings/{id}` enrichit à la demande la description, les caractéristiques, la galerie et la localisation. Les coordonnées du portail sont distinguées d’un centre de commune approximatif.

## Publications d’un même bien

`listing_publications` conserve chaque identité source/externe et son URL derrière une fiche canonique. `services/deduplication.py` compare la zone, le prix, la surface et les pièces, puis exige une photo commune ou une description suffisamment distinctive. Une nouvelle annonce ambiguë peut déclencher une vérification de ses détails et de ceux de deux candidats avant l’alerte.

La vérification progressive de l’inventaire existant travaille par petits lots en arrière-plan. Les anciennes lignes sont conservées avec une référence au bien canonique ; favoris et historiques sont regroupés. Les critères simples restent insuffisants pour fusionner deux logements.

## Supervision et passerelle Plesk

`web_requests` enregistre uniquement les métadonnées des appels API : profil, méthode, chemin, action, statut et durée. Les requêtes en cours sont suivies en mémoire. Le journal est limité à 20 000 entrées et trois jours ; les sondes de santé et de supervision ne s’enregistrent pas elles-mêmes. Les fichiers statiques servis par Plesk n’entrent pas dans ce journal.

Sur Plesk mutualisé, le navigateur utilise le frontend et la passerelle PHP du même domaine. Le secret de liaison reste hors de `httpdocs`. La passerelle limite les routes et signe méthode, chemin, empreinte du corps, origine, adresse cliente, horodatage et nonce. Le backend vérifie le HMAC et refuse les signatures expirées ou réutilisées. Le backend permanent peut être exposé par un reverse proxy HTTPS ou un tunnel sortant ; le bot et Chromium ne tournent pas dans le mutualisé.

## Fournisseurs administrables

`provider_configurations` et `provider_credentials` conservent les activations, les secrets chiffrés, leur empreinte, les dernières vérifications et les quotas. Le chiffrement Fernet lie chaque valeur au fournisseur et à son identifiant ; la clé maîtresse est conservée en dehors du code dans `storage/provider-credentials.key`. Les clés de l’environnement sont importées une seule fois ; une révocation dans l’admin ne ressuscite pas au redémarrage.

`services/provider_monitor.py` vérifie les soldes officiels hors du chemin des requêtes utilisateur. Les réponses HTTP ne contiennent que des métadonnées autorisées, jamais le secret déchiffré. Les clients résolvent leurs clés à chaque appel et invalident les caches de configuration lors d’un changement. Les sondes de quota sont distinctes des diagnostics de source, qui effectuent une vraie collecte et peuvent consommer les crédits habituels.

## Services de suivi et mises à jour

`property_tracking` conserve les observations de prix par publication, les notes privées et les contrôles de disponibilité. Le rapprochement après enrichissement ne parcourt que les candidats plausibles du bien ; il transfère les relations, notes et historiques et résout les anciens alias. `alert_preferences` calcule les plages silencieuses en heure locale, y compris les changements d’heure. `member_notifications` dédoublonne l’outbox, traite les rappels et le résumé quotidien, et reprend les erreurs temporaires de Telegram. Les envois hérités et nouveaux partagent un verrou.

Deux boucles distinctes démarrent avec `main.py` : les notifications toutes les 30 secondes et la fraîcheur par passes d’environ une minute, dont la taille s’adapte au nombre de publications favorites et à une cible configurable. Le budget global, deux publications par portail et trois contrôles simultanés bornent le débit. Les métriques sont mises en mémoire dans une file bornée puis écrites par lot toutes les 30 secondes ; leur enregistrement ne bloque pas la première réponse sur une écriture SQL. L’administration force une vidange avant agrégation.

Les fiches récentes réutilisent le cache persistant (24 h si complet, 15 min si partiel). Une collecte partagée renvoie d’abord les données connues après au plus 150 ms d’attente externe. En accès natif, `/events` transmet la fin via SSE ; la passerelle PHP utilise `/updates`, une attente HTTP de 18 secondes au maximum, avec reprise bornée côté client. L’onglet masqué ou la fermeture annule l’écoute du client, sans annuler le travail partagé. [Détails et validation](RELEASES.md).
