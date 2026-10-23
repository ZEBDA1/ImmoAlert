# Validation

[Retour au README](../README.md)

## Tests de régression

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
```

La suite comporte **96 tests** au moment de la publication initiale de ce dépôt.

| Module | Tests | Comportements couverts |
| :--- | ---: | :--- |
| `test_text_parsing.py` | 32 | Prix, décimales françaises, milliers, loyers, surfaces et pièces |
| `test_matching_and_scam.py` | 22 | Types de bien, score relatif au filtre et signaux d’arnaque |
| `test_firecrawl_backoff.py` | 18 | Quotas, dégradation des connecteurs, schéma JSON, cache et statut CLI |
| `test_alert_delivery.py` | 9 | Reprogrammation, reprises Telegram, suivi des envois, filtres et assistant |
| `test_location_matching.py` | 5 | Villes, codes postaux, arrondissements et preuves de localisation |
| `test_scheduler.py` | 4 | Collectes partielles, recherche initiale et isolation des collectes lentes ou expirées |
| `test_quality_repo.py` | 3 | Normalisation, signature canonique et persistance des collectes |
| `test_ai_features.py` | 2 | Nettoyage des résultats IA et repli local |
| `test_premium_limits.py` | 1 | Accès au filtre IA réservé aux comptes Premium |

Les appels réseau et les sources sont simulés. Ces tests ne mesurent ni la disponibilité continue des portails ni un délai d’alerte garanti.

## GitHub Actions

Le workflow [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) :

1. Installe les dépendances et exécute la suite sous Python 3.11 et 3.12.
2. Construit l’image Docker après le succès des tests.
3. Vérifie l’import de l’application, l’initialisation d’une base neuve et le healthcheck dans un conteneur sans réseau.

Aucun secret de production n’est nécessaire pour cette CI. Les résultats courants sont visibles dans [GitHub Actions](https://github.com/ZEBDA1/TGBBot/actions).

## Vérifications réalisées sur le serveur

Avant cette publication, les 96 tests ont passé sous Python 3.12 sur l’hôte. Une suite de 96 tests a également passé sous Python 3.11 sur GitHub Actions lors de l’intégration précédente du bot. L’image Docker a été construite et le bot a établi son polling Telegram avec sa configuration d’exploitation.

Les migrations ont été exécutées deux fois sur des copies temporaires des deux bases existantes ; les nombres d’utilisateurs, filtres et annonces ont été conservés. Le contrat du CLI Firecrawl réel a été vérifié contre un serveur HTTP local pour les extractions avec schéma et par consigne, sans consommer de crédit de collecte.

## Limites

- Une CI verte ne prouve pas qu’un portail protégé répondra depuis une IP donnée.
- Un compte Firecrawl peut disposer de crédits et atteindre sa limite de concurrence.
- Le healthcheck local ne valide pas le jeton Telegram.
- Les visuels du README utilisent des données fictives et sont des illustrations, pas des captures d’une session utilisateur.
