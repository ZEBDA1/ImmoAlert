# Validation

La version 2.1 passe **407 exécutions de tests backend** sur Python 3.11. La CI exécute également la suite avec Python 3.12. Les appels fournisseurs sont simulés dans les tests ; les comptes et logements des contrôles navigateur sont fictifs.

## Backend et données

La suite couvre l’authentification par mot de passe et Telegram, les droits, les migrations additives, les collecteurs, le regroupement des publications, les prix et la disponibilité, les suivis privés, les notifications, les comparaisons, les zones de localisation et les fournisseurs. Les migrations sont réexécutables et les tests vérifient la conservation des données existantes.

Les tests FlashProxy répartissent notamment 20 attributions entre deux plans avec seulement deux générations de session simulées. Ils contrôlent l’expiration indépendante, le repli, les limites API, les quotas partiels, les changements de configuration concurrents et la compatibilité de l’ancien réglage.

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
npm --prefix web ci
npm --prefix web run build
```

## Navigateur et passerelle PHP

Les contrôles utilisent une vraie API et une base jetable. Ils vérifient les parcours ordinateur, téléphone, tablette et paysage dans les thèmes sombre et clair, les brouillons non enregistrés, les préférences persistantes, les suivis, le comparateur et les deux plans FlashProxy. La passerelle est aussi testée avec PHP, cURL, TLS et des requêtes signées.

Installer Playwright et Chromium pour les contrôles visuels ; `IMD_PLAYWRIGHT` peut désigner le chemin de son module `index.mjs`. Les scripts résolvent aussi une installation dans le projet ou dans `web/`.

```bash
node scripts/check_member_ui.mjs
node scripts/check_flashproxy_plan_ui.mjs
node scripts/check_comparison_csv.mjs
python3 scripts/check_member_gateway.py
python3 scripts/package_plesk.py
```

Le contrôle PHP/TLS nécessite Docker et les images du projet/PHP. Les autres scripts créent leur démonstration sur l’interface de boucle locale, sans charger le `.env` ni démarrer le bot ou ses collectes. Les contrôles de carte utilisent des tuiles et contours fictifs.

## Publication

La CI contrôle les fichiers du snapshot Git puis exécute Gitleaks sur ce snapshot. Les bases, clés, configurations réelles, journaux et preuves opérationnelles restent exclus. Les captures publiques sont issues de démonstrations isolées. Voir [SECURITY.md](../SECURITY.md) et les [visuels](assets/README.md).

Une CI verte ne garantit pas l’accès futur aux portails protégés, les quotas d’un fournisseur ou un délai de détection des annonces. Les tests ne doivent pas déclencher de messages Telegram réels.
