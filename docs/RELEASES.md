# Nouveautés

## 2.1.0

Les logements ont maintenant un espace **Mes suivis** : recherche, filtres par étape, notes, visites, rappels et conservation des anciens suivis. Un favori peut être comparé à trois autres, avec des critères personnels et un export CSV. La carte montre une zone délimitée en respectant la précision disponible.

Les fiches chargent progressivement. Dates, libellés et valeurs sont présentés en français. Les comptes disposent de préférences de notification, de plages silencieuses et d’un résumé quotidien Telegram facultatif. Les prix et contrôles de disponibilité conservent leur historique.

L’administration ajoute la modération, des diagnostics détaillés et les temps de réponse. Les clés fournisseur sont administrables sans redémarrage. **Deux plans FlashProxy** peuvent fonctionner ensemble sous une même clé, avec sessions indépendantes, rotation, repli lors d’un échec de génération et quotas séparés.

Cette version comprend 407 exécutions de tests backend, des parcours navigateur dans les deux thèmes et un contrôle de la passerelle PHP/TLS. Les captures et bannières promotionnelles sont renouvelées ; les diagnostics opérationnels restent locaux.

### Mise à jour

Sauvegarder la base et le fichier maître des clés, puis installer la même version du backend et du frontend. Les migrations de démarrage sont additives. Pour Plesk, transférer l’intégralité de `httpdocs/` depuis l’archive de la release, puis conserver la configuration privée propre à l’instance. [Guide Plesk](PLESK.md).

L’ancienne configuration d’un seul plan FlashProxy continue de fonctionner. Le second ID se renseigne dans Administration → Sources & activité → Clés et quotas. Les plans doivent être compatibles avec la génération HTTP sticky et le pays configuré.

## 2.0.0

Site React, comptes administrés, connexion par mot de passe et liaison Telegram facultative, console de supervision et édition Plesk avec passerelle PHP signée.
