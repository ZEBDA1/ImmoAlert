# Scrapling / Firecrawl — test réel du 24 octobre 2026

**Conclusion : comparaison technique incomplète à cause du quota Firecrawl.** Scrapling récupère 42 pages sur 48 (87,5 %) et fournit des annonces utilisables avec les parseurs actuels sur cinq portails. Firecrawl est authentifié, mais son compte dispose de **0 crédit restant sur 1 000 crédits de forfait** ; les requêtes de contrôle sont refusées avant le scraping. Son taux de réussite et sa vitesse ne sont donc **pas mesurables** dans cet état. Ce résultat ne démontre pas que son moteur est moins performant.

Test exécuté le **24/10/2026, de 16:30:54 à 16:34:52, heure de Paris**, en environ 4,0 minutes. Versions installées : Scrapling **0.5**, CLI Firecrawl **1.26.22**. Il s’agit du service Firecrawl Cloud configuré dans le bot, pas d’une installation open source locale de Firecrawl.

## Mesures

| Critère | Scrapling | Firecrawl configuré |
| --- | --- | --- |
| Pages du corpus testées | 48 requêtes : 16 URL × 3 passages | Corpus non exécuté : crédit épuisé |
| HTML récupéré, HTTP 200 sans blocage détecté | **42/48 — 87,5 %** | Non mesurable |
| Portails dont le HTML est récupéré à chaque essai | **7/8** | Non mesurable |
| Temps médian des récupérations HTML réussies | **2,09 s** | Non mesurable |
| Requêtes donnant des annonces retenues par les contrôles actuels du bot | **30/48 — 5 portails** | Non mesurable |
| Annonces distinctes par URL, tous passages confondus | **159** | Non mesurable |
| Crédits Firecrawl disponibles avant / après | Sans objet pour Scrapling | **0 / 0** |

Les échecs Firecrawl ne sont pas présentés comme un « taux de scraping de 0 % ». Le service n’a pas eu les moyens de tenter la récupération du corpus. De même, son refus rapide n’est pas un temps de scraping à comparer aux secondes nécessaires pour télécharger une page complète.

## Résultats Scrapling par portail

Chaque portail est testé sur une page de recherche de Rouen et une de Mantes-la-Jolie, trois fois chacune. Le nombre d’annonces indique l’étendue constatée par réponse réussie, selon la ville ; ce n’est pas l’intégralité du stock du portail.

| Portail | HTML reçu | Médiane HTML réussi | Annonces retenues / réponse | Lecture du résultat |
| --- | ---: | ---: | --- | --- |
| Entreparticuliers | 6/6 | 1,37 s | 14 | Extraction exploitable, six fois sur six. |
| Leboncoin | 0/6 | Non applicable (refus) | 0 | HTTP 403 avec le transport HTTP puis le navigateur lors des six essais. |
| Logic-Immo | 6/6 | 11,58 s | Non exploité par le parseur | HTML reçu ; connecteur actuel non adapté à ces nouvelles cartes/URLs de détail. |
| OuestFrance-Immo | 6/6 | 0,76 s | 3–25 | Extraction exploitable, six fois sur six. |
| PAP | 6/6 | 3,02 s | 9 | Extraction exploitable, six fois sur six. |
| ParuVendu | 6/6 | 1,19 s | 12–30 | Extraction exploitable, six fois sur six. |
| Rentola | 6/6 | 2,38 s | 21 | Extraction exploitable, six fois sur six. |
| SeLoger | 6/6 | 10,51 s | Non exploité par le parseur | HTML et liens d’annonces reçus ; champs obligatoires non extraits par le parseur actuel. |

Le transport HTTP Scrapling suffit pour **30 requêtes**. Le navigateur est essayé **18 fois** et apporte **12 récupérations supplémentaires** : SeLoger et Logic-Immo. Leboncoin demeure refusé dans ces essais, y compris avec le navigateur. Cela ne contredit pas les récupérations ponctuelles observées lors de l’audit précédent : sa disponibilité varie dans le temps.

**Récupérer le HTML et disposer d’un parseur adapté sont deux étapes différentes.** Les captures SeLoger contiennent des pages de recherche avec des liens de logements ; le parseur de secours actuel ne renseigne notamment pas la localisation, puis les contrôles de qualité rejettent les fiches incomplètes. Logic-Immo fournit aussi une véritable page de recherche ; ses liens utilisent notamment `/detail-annonce/location/`, alors que le connecteur actuel cherche d’autres formes d’URL et hydrate normalement des fiches séparées. Ces limites d’extraction ne doivent pas être attribuées à un échec de téléchargement Scrapling.

## Contrôle Firecrawl

Le CLI signale une authentification valide. Le contrôle du compte via l’API renvoie HTTP 200 et `remainingCredits: 0`, `planCredits: 1000`, avant et après le test.

- Page témoin non protégée `https://example.com` : **HTTP 402**, « Insufficient credits ».
- Même URL PAP Rouen que dans le corpus : **HTTP 402**, même erreur.
- Extraction JSON via le CLI, avec le schéma du bot : **code de sortie 1**, même refus pour crédit insuffisant.

Après ces contrôles, les 48 requêtes du corpus Firecrawl ont été laissées non exécutées. Les répéter avec le même compte épuisé n’aurait apporté aucune mesure du moteur. Aucun crédit n’a été acheté et aucun réglage du bot n’a été modifié.

## Données et méthode

- **Même corpus prévu pour les deux moteurs** : huit portails × deux villes, une page de recherche par lieu. Les URL exactes sont dans `corpus.json` ; elles sont figées avant les essais. Les routes géographiques sont celles vérifiées lors des audits et, pour SeLoger Mantes-la-Jolie, sur la page native du portail.
- **Trois passages**, ordre mélangé avec une graine fixe, deux tâches au maximum et une seule requête simultanée par domaine. Chaque essai s’exécute dans un processus séparé. Le bot reste actif sur le serveur ; ce n’est pas un laboratoire matériel entièrement isolé.
- **Pas de cache applicatif de HTML**, pas de cookie de connexion aux portails ni de proxy supplémentaire. Les caches internes des sites/CDN ne sont pas contrôlés. Scrapling essaie d’abord `Fetcher` avec impersonation Chrome, puis `StealthyFetcher` et le Chromium installé si nécessaire, comme la stratégie de secours du bot.
- Budget Scrapling : 25 s pour HTTP, puis 25 s pour le navigateur si nécessaire ; attente navigateur 1 s, une tentative par phase, processus interrompu après 70 s maximum. Le temps médian ci-dessus mesure la récupération du HTML ; l’import initial du processus et le parsing sont hors de ce chronomètre. Les temps de parsing restent disponibles dans le CSV.
- Pour Firecrawl, le protocole prépare du HTML brut avec cache désactivé (`maxAge=0`), région FR et proxy automatique, puis **les mêmes parseurs du bot**. Le mode JSON avec IA utilisé par le bot fait l’objet du contrôle de disponibilité ; sa qualité d’extraction ne peut pas être évaluée avec le quota actuel.
- Les parseurs et contrôles de qualité existants sont utilisés sans modification et sans nouvelles requêtes de détail. Pour Logic-Immo, le connecteur nécessite normalement ces détails : zéro fiche extraite de la seule page de recherche n’établit pas que le HTML serait inexploitable après adaptation.
- Les **474 observations retenues** représentent **159 URL distinctes** : une annonce reçue trois fois n’est comptée qu’une fois dans ce dernier total. Deux portails peuvent publier le même logement sous deux URL ; il ne s’agit donc pas nécessairement de 159 logements différents.
- Sur les seules observations retenues, prix, surface et localisation sont renseignés à 100 %, pièces à **96,84 %**, et une URL d’image est présente à 100 %. Ce sont des taux de présence après filtrage, pas une mesure de vérité des annonces. Le chargement de chaque photo et la disponibilité réelle des logements n’ont pas été revalidés par ce benchmark.

Le processus de test accède à la base de production par un montage **en lecture seule**. Il ne persiste pas d’annonces, ne change pas les filtres et n’envoie pas d’alertes. Les résultats sont écrits dans un dossier de preuves séparé, privé.

## Conséquence pour ton bot

Scrapling est effectivement utilisable sur ce serveur : rapide sur cinq portails, et capable de récupérer les grilles de deux autres avec son navigateur. Adapter les parseurs SeLoger et Logic-Immo paraît être la prochaine amélioration utile, puisque le HTML arrive déjà dans ces essais. Leboncoin reste un point de fragilité.

Il faut un compte Firecrawl disposant de crédits, puis le même protocole sur les mêmes URL, pour départager les moteurs sur vitesse, couverture et qualité de données. Ce test ne justifie pas une promesse de remplacement universel de Firecrawl par Scrapling.

## Preuves et reproduction

Dossier privé : `storage/benchmarks/20261024-scrapling-firecrawl/`.

- `environment.json` : versions, horaires, configuration non secrète, crédit avant/après, empreintes des parseurs.
- `corpus.json`, `summary.json`, `metrics.json`, `trials.csv` : protocole et mesures agrégées ou par requête.
- `firecrawl_preflight.json` : les trois contrôles de refus.
- `scrapling/round-N/PORTAIL-CODEPOSTAL/` : résultat JSON, HTML compressé et journal de chaque essai.
- `transport_validation.json` : vérification hors ligne des titres et liens de logements dans les HTML reçus.

Script : [scripts/benchmark_fetchers.py](../scripts/benchmark_fetchers.py). Réutiliser un **nouveau dossier de sortie vide** à chaque campagne. Exemple dans un conteneur de diagnostic avec les secrets existants chargés et le stockage de production monté en lecture seule : `python /benchmark_runner.py --output /benchmark --rounds 3`. Pour recalculer uniquement les agrégats sans réseau : ajouter `--summarize-only`.

Références de méthode : [dépôt officiel Scrapling](https://github.com/D4Vinci/Scrapling), [API Scrape officielle Firecrawl](https://docs.firecrawl.dev/api-reference/endpoint/scrape). Ces documents décrivent les fonctionnalités et paramètres ; les chiffres de ce rapport proviennent des mesures locales, pas des benchmarks promotionnels de leurs éditeurs.
