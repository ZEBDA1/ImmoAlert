# Architecture de TGBBot

[Retour au README](../README.md)

## Commandes et recherches

`main.py` initialise la base et lance le polling aiogram ainsi que le scheduler. Les handlers de `bot/` gèrent les comptes, les commandes et l’assistant de filtres. Les critères sont enregistrés avec leur utilisateur ; le mode Premium permet plusieurs filtres nommés.

Un enregistrement de filtre peut déclencher une recherche parmi les annonces connues, puis une collecte des annonces déjà en ligne. L’entrée dans l’assistant conserve les alertes existantes actives. `/start` quitte un assistant inachevé et réactive le compte.

## Collecte et réactivité

Le scheduler construit les lieux à visiter depuis les filtres actifs. Les paramètres de quota et de variantes déterminent le périmètre de chaque source. Les connecteurs HTTP et navigateur produisent des objets `ScrapedListing`.

Les collectes synchrones utilisent un `ThreadPoolExecutor` dédié, distinct de l’exécuteur par défaut d’asyncio. Ce dernier reste disponible pour les tâches du bot, dont les résolutions DNS utilisées par les échanges Telegram. Un timeout asyncio arrête l’attente d’une collecte ; une requête synchrone déjà partie peut continuer jusqu’à son propre timeout HTTP. Cette séparation évite qu’elle monopolise les workers utilisés par Telegram.

Les appels Firecrawl passent par des sous-processus avec timeout, fichiers de schéma temporaires et cache local. Le mode d’extraction JSON utilise un schéma, y compris pour le repli par consigne libre. SeLoger recherche aussi l’URL canonique de la zone avant la collecte.

## Qualité et correspondance

`scraper/quality.py` normalise les annonces et extrait les valeurs utiles. `utils/text.py` traite les prix français, surfaces et pièces. Une localisation manquante reste manquante : le lieu recherché n’est pas ajouté comme preuve de localisation.

La déduplication associe identifiant de source, signature canonique et fenêtre de conservation. Le matching applique les limites et le type de bien, puis la zone. Le score compare notamment le prix au budget de l’utilisateur et la surface à son minimum. Les comparaisons par rayon nécessitent le géocodage optionnel.

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
