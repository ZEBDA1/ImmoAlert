# Chargement progressif des fiches ImmoAlert

Direction validée le 26 octobre 2026 : servir rapidement les données enregistrées, compléter en arrière-plan et conserver les thèmes actuels.

## Périmètre et comportement

- Remplacer le spinner de la fiche par trois traits animés et une ligne de chargement indéterminée, sans pourcentage fictif.
- Conserver les informations connues pendant la récupération ; réserver les squelettes aux zones réellement vides.
- Servir les détails en cache sans appel aux portails et sans rapprochement global des doublons.
- Partager une seule tâche par bien entre les consultations web et Telegram ; limiter les collectes simultanées à deux.
- Limiter l’attente de l’enrichissement dans la requête web à 150 ms, hors lectures locales et transport. Une réponse rapide termine dans la même requête ; sinon les données persistées sont renvoyées avec `enriching`.
- Vérifier les compléments après deux secondes puis espacer les vérifications jusqu’à cinq secondes, avec une seule requête en cours. Après deux minutes, rendre la main avec une invitation à réessayer.
- Charger le module d’administration seulement lorsque cet espace est ouvert pour alléger le JavaScript initial.

## Architecture et erreurs

Le service de détails possède les tâches de collecte et de rapprochement. Le web et le bot attendent la même tâche protégée par `asyncio.shield`. Le cache conserve ses durées existantes : 24 heures après succès, 15 minutes après réponse partielle. Les exceptions sont consommées et journalisées ; les tâches terminées sont retirées. La fermeture de la fiche arrête ses requêtes et temporisateurs sans annuler une collecte partagée.

L’interface distingue ouverture, récupération des compléments, fiche complète, réponse partielle et échec. Les informations existantes restent utilisables en cas d’erreur. Après huit secondes, le texte explique que le portail est lent. Le mouvement utilise les transformations et l’opacité ; `prefers-reduced-motion` affiche un indicateur fixe. Les lecteurs d’écran reçoivent un statut concis.

## Plan de réalisation

1. Unifier la politique de fraîcheur et déplacer le rapprochement dans la tâche partagée.
2. Réduire le budget d’attente HTTP et annoncer l’intervalle de vérification.
3. Ajouter l’animation, la cadence progressive et le chargement différé de l’administration.
4. Vérifier les caches, l’expiration, la concurrence, les erreurs, l’API et le navigateur.
5. Préparer le build et un compte rendu des mesures et des évolutions possibles.

## Validation

Tests de cache, expiration, partage des tâches, déconnexion d’un lecteur, rapprochement unique, authentification et réponse API pendant une collecte bloquée. Compilation TypeScript/Vite et comparaison du JavaScript initial. Scénarios navigateur de chargement lent, mises à jour, fermeture, erreurs, mobile et deux thèmes. Mesures locales simulées clairement distinguées des performances réelles des portails.

## Relecture

Périmètre limité au chargement et à ses performances. Aucun pourcentage ou gain réseau réel n’est annoncé sans mesure. Les seuils et la responsabilité des tâches sont explicites. Aucun déploiement public n’est inclus.
