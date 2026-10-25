# Audit réel des sources — 24 octobre 2026

La vérification porte sur les recherches actives **Rouen / 76000** et **Mantes-la-Jolie / 78200**, depuis le serveur de production. Elle distingue les résultats réellement collectés des portails bloqués. Les tests automatiques seuls ne prouvent pas qu’un portail répond.

## Résultats constatés

| Source | Rouen | Mantes-la-Jolie | État depuis le serveur |
| --- | ---: | ---: | --- |
| Entreparticuliers | 14 | 14 | Annonces, prix, surfaces, villes et photos récupérés |
| ParuVendu | 30 | 14 | Extraction des cartes ; pas de rafale de requêtes sur les fiches |
| PAP | 11 | 20 | Résolution du véritable identifiant géographique ; Scrapling fonctionne en secours |
| Rentola | 21 | 20 | Données JSON-LD de chaque logement, sans prix des FAQ ni confusion chambres/pièces |
| OuestFrance-Immo | 25 | 4 | Routes de location par commune INSEE ; cartes correctement délimitées |
| Leboncoin | 32 | 28 | Accès intermittent ; récupération via le JSON natif après réparation du parseur |
| SeLoger | 0 | 0 | Relevé isolé bloqué/délai 125 s ; dernier cycle : 31 fiches brutes, toutes rejetées pour données insuffisantes |
| Logic-Immo | 0 | 0 | Page canonique refusée pendant l’audit ; aucune annonce exploitable au dernier cycle |

**233 observations exploitables** sur les six sources accessibles, dont 60 Leboncoin après un dernier contrôle et une correction supplémentaire. PAP propose aussi des logements des communes voisines : la ville propre à chaque annonce est conservée et le moteur de filtres applique ensuite les critères de l’utilisateur. Ce relevé couvre les pages effectivement parcourues, pas la totalité des stocks des portails.

Les identifiants des routes SeLoger et Logic-Immo ont été vérifiés sur leurs pages natives. Ils ne sont pas déduits des codes INSEE. SeLoger et Logic-Immo restent non opérationnels pour la publication et pilotables dans les panneaux admin. SeLoger exige encore une extraction complète et vérifiable de la localisation et des autres champs : recevoir une page ne suffit pas. Logic-Immo ne fournit aucun enregistrement exploitable. Les refus d’accès et les crédits Firecrawl épuisés limitent également les chemins de secours ; aucun fonctionnement complet n’est revendiqué pour ces deux sources. Leboncoin répond parfois 403 avant de fournir une page exploitable ; son résultat positif ne garantit pas chaque requête future.

## Défauts corrigés

- Leboncoin : les ancres superposées des nouvelles cartes empêchaient de lire un JSON natif pourtant présent dans une réponse HTTP 200. Ce JSON est désormais prioritaire ; les prix en tableaux, les localisations imbriquées, surfaces, pièces et photos sont correctement lus. Les parkings et autres biens non résidentiels sont filtrés. Deux nouveaux relevés réels récupèrent 32 locations à Rouen et 28 à Mantes-la-Jolie, en environ 39 et 10 secondes.

- Entreparticuliers : la première ancre d’image donnait « En attente de photo » au lieu du logement ; le parseur lit désormais toute la carte et ses champs.
- PAP : l’ancien paramètre `?q=` pouvait ramener une page générale avec des ventes et locaux commerciaux. L’autocomplétion native fournit maintenant le bon identifiant de lieu, sur les routes de location résidentielle. Les mentions « à N km de … » sont séparées de la ville du bien.
- Rentola : une recherche avec un code postal numérique menait à une autre ville. Les mots Cloudflare présents dans des scripts faisaient également rejeter une page normale. Les routes par ville et le détecteur de blocage sont corrigés.
- ParuVendu : les cartes contiennent déjà les informations utiles. Ouvrir des dizaines de fiches provoquait le captcha du portail.
- OuestFrance-Immo : les URLs de location utilisent la commune réelle ; la ville du logement est lue dans les métadonnées de l’annonce, pas dans l’adresse de l’agence.
- Photos : le stockage JSON et la lecture des anciennes valeurs préservent les virgules internes des URLs CDN.
- Persistance : une annonce revue actualise son prix, ses photos et ses caractéristiques tout en conservant son identifiant, sa première observation, ses favoris et son historique d’alertes.
- Qualité : les ventes, biens non résidentiels, valeurs numériques impossibles et fiches insuffisantes sont écartés. Une ville absente n’est plus inventée depuis le filtre de recherche. Deux logements similaires avec des URLs distinctes ne sont plus fusionnés sur quelques champs génériques.
- Supervision : une extraction vide n’est plus déclarée réussie. Les échecs de transport et les quotas épuisés remontent dans l’état des sources. Les crédits Firecrawl épuisés déclenchent une temporisation.

## Contrôle de quelques annonces

Les prix, surfaces et localisations extraits ont été rapprochés de fiches natives, notamment :

- [PAP, Rouen : 920 € et 44 m²](https://www.pap.fr/annonces/appartement-rouen-76000-r465700540).
- [ParuVendu, Rouen : 1 250 € et 63 m²](https://www.paruvendu.fr/immobilier/location/appartement/1293524376A1KILHAP000).
- [OuestFrance-Immo, Rouen : 605 € et 46 m²](https://www.ouestfrance-immo.com/immobilier/location/appartement/rouen-76-76540/2-pieces-20843261.htm).
- [Rentola, Rouen : 556 €, 60 m² et 4 pièces](https://rentola.fr/listings/t4-a-rouen-a-louer-locagestion-expert-en-gestion-locative-p1016a7).
- [Entreparticuliers, Rouen : 690 € et 31 m²](https://www.entreparticuliers.com/annonces-immobilieres/appartement/location/rouen-76000/appartement-2-pieces-31-m2/ref-22717752), fiche HTTP 200 depuis le serveur.

Une photo témoin de chacun de ces cinq portails répond HTTP 200 avec un type MIME image. Cela ne garantit ni la disponibilité de tous les logements ni la véracité des déclarations des annonceurs. Une observation récente signifie que le bien était présenté sur le portail au moment du relevé ; certaines locations ont une date d’entrée future.

## Inventaire partagé avec le site et le bot

L’inventaire visible contient uniquement les locations cohérentes **effectivement vues sur une source depuis moins de 30 jours**. L’ancien stock n’est pas rendu actuel par une simple validation de ses champs.

Lors de la première réconciliation, avant l’ajout des 60 observations Leboncoin : 10 374 fiches initiales, 164 nouvelles fiches et 9 fiches actualisées ; 10 538 lignes conservées au total. L’état résultant compte **173 observations récentes**, **953 fiches écartées de l’affichage** pour incohérence ou informations insuffisantes, et **9 412 fiches historiques à revérifier**. Les annonces et les relations historiques ne sont pas supprimées. Les favoris anciens portent une indication de revérification ; les fiches invalides ne sont plus affichées comme locations. Les alertes différées ne sont pas envoyées pour une fiche hors de l’inventaire courant.

Sur ce serveur, les requêtes répétées ville/code postal ont aussi été désactivées (`EXPAND_LOCATION_VARIANTS=false`) et chaque source utilise une seule collecte géographique simultanée (`PER_SOURCE_CONCURRENCY=1`). Les recherches restent celles des filtres existants.

Le site affiche jusqu’à 800 annonces courantes, avec date d’observation ; il distingue ce nombre de l’historique total. Les commandes admin et les recherches utilisent le même bot, les mêmes filtres et les mêmes réglages persistants. La réconciliation n’a créé ni envoyé aucune alerte Telegram.

## Validation et preuves

- **151 tests automatiques réussis**, dont les régressions sur des extraits HTML réellement reçus, les droits admin, la bascule Scrapling, la liaison site/bot, la qualité, la déduplication et la livraison d’alertes. Le dernier ajustement de l’état des favoris anciens a aussi passé les 12 tests ciblés de l’audit.
- Migration et réconciliation exécutées plusieurs fois sur une copie isolée de la base ; identifiants stables, intégrité SQLite `ok`.
- Site : compilation TypeScript et build de production réussis ; contrôles navigateur ordinateur/mobile, navigation, filtres, affichage liste, formulaire de recherche, neuf interrupteurs admin, absence de débordement horizontal et d’erreur JavaScript.
- Contrôle des accès anonymes, du secret du bridge et du refus d’une origine tierce ; seuls les comptes Telegram administrateurs peuvent modifier les réglages admin.

Preuves privées sur le serveur :

- `storage/audits/20261024-baseline/` : état initial des huit sources.
- `storage/audits/20261024-rouen-final/` et `storage/audits/20261024-mantes-final/` : résultats JSON, traces HTTP, pages HTML compressées et journaux. Certains résultats sont rejoués sur ces captures après le dernier ajustement de parseur ; le JSON le signale.
- `storage/audits/20261024-lbc-rouen/` et `storage/audits/20261024-lbc-mantes/` : nouveaux relevés Leboncoin après réparation, 69 cartes brutes et 60 locations conservées.
- `storage/audits/20261024-reconcile-test/` : copie isolée de validation.
- `storage/backups/pre-inventory-audit-20261024T045009Z/` : sauvegarde SQLite et comptages avant la modification de production.

Reproduire l’audit : `python scripts/audit_sources.py --city Rouen --postal 76000 --output storage/audits/nouveau-releve`. L’audit ne persiste pas d’annonces et n’envoie pas de messages. `scripts/reconcile_inventory.py` est en lecture seule par défaut ; `--apply` active explicitement la migration et la réconciliation à partir des captures récentes.

Quinze photos Rentola sont fournies par un tiers en HTTP dont le certificat HTTPS est invalide. Le site affiche une image de remplacement pour ces photos ; la vérification TLS reste active.

## Dernier cycle de production après déploiement

Le cycle commencé à **04:58:48 UTC** et terminé vers **05:00:07 UTC** utilise les deux lieux réels et la concurrence réduite : ParuVendu 44 annonces, Entreparticuliers 28, Leboncoin 56 (69 brutes, 13 rejetées), PAP 31, Rentola 41, OuestFrance-Immo 29. Ces six sources terminent avec le statut `success`, sans dépassement de délai. Les écarts avec les relevés précédents reflètent les pages et annonces reçues à des moments différents. Les fiches déjà observées restent dans l’inventaire pendant sa fenêtre de 30 jours ; ces nombres ne sont donc pas le total cumulé du site.

SeLoger termine en échec : 31 résultats bruts, aucun résultat publiable après contrôle de qualité. Logic-Immo termine avec `empty`, ce qui signifie « extraction à vérifier » et ne prouve pas l’absence de locations sur le portail. Le bot est sain, sans redémarrage automatique. La base D1 du site reçoit bien des fiches du nouvel inventaire avec `last_seen_at` et `inventory_status`.

## Complément du 24 octobre, 16 h 35 (Paris)

Le [benchmark Scrapling / Firecrawl](BENCHMARK_SCRAPLING_FIRECRAWL_2026-10-24.md) apporte un constat plus récent : le navigateur Scrapling récupère les véritables grilles de SeLoger et Logic-Immo à chaque passage du test, mais les connecteurs actuels ne produisent pas encore de fiches complètes à partir de ces grilles. Leboncoin refuse les six requêtes du benchmark. La disponibilité varie dans le temps ; les chiffres de l’audit initial décrivent leur propre créneau. Le compte Firecrawl est toujours authentifié avec zéro crédit restant.
