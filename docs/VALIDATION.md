# Validation

[Retour au README](../README.md)

## Suite actuelle

La suite compte **216 tests de régression**, vérifiés en conteneur Python 3.11 avec le réseau externe désactivé. Les données de compte et les réponses des portails utilisées par les tests sont fictives ou simulées.

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
npm --prefix web ci
npm --prefix web run build
```

| Module | Tests | Périmètre |
| :--- | ---: | :--- |
| `test_admin_auth.py` | 15 | Comptes sur invitation, mots de passe, sessions, rôles, liaison Telegram, trafic, signature PHP et migration SQLite |
| `test_web_api.py` | 12 | API privée, connexion Telegram autorisée, origine, fiches complètes, actions, favoris, scans et serveur statique |
| `test_site_sync.py` | 18 | Profils partagés, recherches, accès Premium, commandes et activation des sources |
| `test_listing_details.py` | 17 | Doublons entre plateformes, publications, conservation des favoris, détails, galerie, extraction ciblée et pagination Telegram |
| `test_scraping_audit.py` | 13 | Cohérence des locations, parseurs des portails, inventaire courant et préservation de l’historique |
| `test_scrapling_backup.py` | 25 | Moteur principal, droits admin, persistance, replis, cache, navigateur et erreurs |
| `test_flashproxy.py` | 18 | Contrat du fournisseur, cache, erreurs, limites et protection des identifiants |
| `test_firecrawl_backoff.py` | 18 | Quotas, concurrence, schémas JSON, cache et disponibilité |
| `test_firecrawl_cli_pool.py` | 1 | Rotation réelle du CLI face à un serveur API local simulant les clés |
| `test_proxy_transport.py` | 1 | Transport HTTP et navigateur via un proxy local authentifié |
| `test_text_parsing.py` | 32 | Prix français, surfaces, pièces et loyers mensuels |
| `test_matching_and_scam.py` | 22 | Critères, types de bien, score et signaux d’arnaque |
| `test_location_matching.py` | 5 | Villes, codes postaux, arrondissements et preuves de localisation |
| `test_scheduler.py` | 4 | Collectes partielles, recherche initiale et isolation des tâches lentes |
| `test_quality_repo.py` | 3 | Qualité, signature canonique et persistance des collectes |
| `test_alert_delivery.py` | 9 | Envois, reprises, annulations et reprogrammation |
| `test_ai_features.py` | 2 | Nettoyage des résultats IA et parsing local |
| `test_premium_limits.py` | 1 | Filtre IA réservé au Premium |

Les tests qui nécessitent un navigateur réel peuvent être ignorés lorsque Chromium n’est pas installé. L’image Docker du projet inclut Chromium ; les contrôles de publication sont exécutés dans cet environnement.

## Frontend et déploiement

- Le build exécute TypeScript puis Vite depuis `web/`.
- L’image Docker construit et embarque le frontend dans `/app/web_dist` ; un clone du dépôt ne dépend d’aucun dossier voisin.
- `scripts/package_plesk.py` compile un frontend utilisant la passerelle PHP dans un répertoire temporaire, sans modifier le build local.
- L’archive est contrôlée pour exclure les bases, journaux, `.env` et configurations privées réelles ; un SHA-256 accompagne le ZIP.
- La syntaxe de la passerelle est vérifiée avec PHP 8.3. Une intégration locale complète a testé TLS, signatures HMAC, cookies Secure/HttpOnly, droits admin, refus d’origine et liste des routes autorisées.
- Les migrations ont été appliquées sur une copie de la base existante : identifiants des profils et relations conservés, création d’un compte sans Telegram vérifiée, `PRAGMA foreign_key_check` sans erreur.

## Vérification navigateur

L’interface a été exécutée sur une base de démonstration isolée, sur ordinateur et sur mobile : connexion par mot de passe, création d’un compte, Premium, accès réservés aux administrateurs, états des sources, trafic API, galerie, agrandissement, navigation au clavier et absence de débordement horizontal.

Les images du README sont reproductibles avec `scripts/capture_docs.py`. Ce script désactive la lecture du `.env` du projet, crée une base temporaire et ne lance ni scheduler ni collecte. Les illustrations des biens et les métriques sont fictives. [Détails des visuels](assets/README.md).

## GitHub Actions

Le workflow [CI](../.github/workflows/ci.yml) vérifie :

1. La suite Python sous 3.11 et 3.12.
2. Le frontend TypeScript/Vite, l’archive Plesk et la syntaxe PHP.
3. La construction Docker, l’import de l’application, une base neuve, la présence du frontend embarqué et le healthcheck dans un conteneur sans réseau.

L’archive testée est conservée comme artefact de la CI pendant 14 jours. Aucun secret d’exploitation n’est nécessaire. [Résultats GitHub Actions](https://github.com/ZEBDA1/ImmoAlert/actions).

## Limites

Une CI verte ne garantit pas la disponibilité d’un portail protégé, les crédits ou les quotas d’un fournisseur, un délai de détection, ni l’authentification du bot auprès de Telegram. Le healthcheck valide la configuration et la base locale. Les vérifications navigateur utilisent des comptes et annonces fictifs ; elles ne constituent pas une mesure de disponibilité de la production.

Les relevés et benchmarks conservés dans la documentation sont datés. Ils décrivent les conditions de leurs essais, pas une garantie de performances actuelles.
