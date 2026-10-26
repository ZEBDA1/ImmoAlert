# Installer ImmoAlert sur un Plesk mutualisé sans SSH

Cette édition héberge le site et une passerelle PHP dans Plesk. **Le backend Python, le bot Telegram, les collecteurs et leur base restent sur un serveur distinct capable d’exécuter des processus permanents** — par exemple le serveur Docker actuel. L’archive ne transforme pas le mutualisé en serveur de bot : il faut une adresse HTTPS publique et joignable pour ce backend. L’adresse privée `192.168.1.32` ne peut pas être utilisée depuis Plesk.

## Prérequis

- Un domaine Plesk avec certificat HTTPS valide et PHP 8.2 ou plus récent.
- L’extension PHP `curl` et des connexions HTTPS sortantes autorisées par l’hébergeur.
- Le backend ImmoAlert mis à jour, accessible par un sous-domaine HTTPS distinct, par exemple `backend.example.com`.
- L’archive `immoalert-plesk.zip`, déjà compilée. Aucun Node.js, Composer, Docker, terminal ou base MySQL n’est nécessaire **dans Plesk**.

## 1. Préparer le backend sur le serveur actuel

### Sans ouverture de ports : Tailscale Funnel

Le site `https://immoalert.work.gd` peut joindre le backend par Tailscale Funnel. Le serveur conserve sa connexion sortante et aucun port de la box ne doit être redirigé. Le frontend reste sur son domaine Plesk ; seule l’adresse du backend utilise le nom HTTPS `*.ts.net` du serveur.

Le domaine public du projet est `https://immoalert.work.gd`. Pour une autre installation, utiliser son propre domaine et l’adresse réellement fournie par Funnel. La configuration privée d’exploitation, ses secrets et les identifiants du réseau Tailscale ne sont pas livrés dans le dépôt ni dans l’archive.

Sur le serveur du bot, Tailscale doit être installé et son service actif. Authentifier le serveur avec son compte Tailscale :

```bash
tailscale up --hostname=immoalert-backend --accept-dns=false --accept-routes=false --ssh=false
```

Ouvrir le lien d’authentification affiché. Après connexion, publier uniquement le service du bot :

```bash
tailscale funnel --bg http://127.0.0.1:8088
tailscale funnel status
```

Si Tailscale propose un lien d’activation de Funnel, l’ouvrir et valider l’activation. La commande affiche une adresse telle que `https://immoalert-backend.nom-du-reseau.ts.net`. Utiliser **l’adresse réellement retournée**, et non cet exemple, comme `backend_url` dans `private/immoalert.php`. Le mode `--bg` conserve la configuration du service en arrière-plan. Le nom reste lié à l’identité du serveur dans le même réseau Tailscale.

Funnel nécessite MagicDNS et les certificats HTTPS activés sur le réseau Tailscale. Plesk et les visiteurs n’ont pas besoin d’installer Tailscale. Les étapes et les limites du service sont précisées dans la [documentation officielle Funnel](https://tailscale.com/docs/features/tailscale-funnel).

Dans la configuration du bot, l’origine du frontend doit être exactement :

```dotenv
WEB_PROXY_ORIGIN=https://immoalert.work.gd
WEB_PROXY_SECRET=le_secret_aleatoire_partage_avec_la_passerelle
```

La suite de ce guide concernant le secret, la passerelle PHP et le redémarrage du conteneur s’applique aussi à cette méthode. Caddy et un enregistrement DNS supplémentaire ne sont pas nécessaires pour l’URL du backend fournie par Funnel.

### Avec un domaine et des ports accessibles : reverse proxy

Configurer un nom DNS et un reverse proxy HTTPS vers le service Python existant. Exemple pour Caddy sur le **serveur du bot** :

```caddyfile
backend.example.com {
    @api path /api/*
    handle @api {
        reverse_proxy 127.0.0.1:8088
    }
    handle {
        respond 404
    }
}
```

Le certificat du backend doit être valide. Le fichier ci-dessus est un exemple à adapter ; il n’est pas installé automatiquement et aucun port public n’a été ouvert par la préparation de cette archive.

Générer un secret aléatoire sur ce serveur :

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Dans le `.env` du **backend**, ajouter les deux variables suivantes avec ce secret :

```dotenv
WEB_PROXY_ORIGIN=https://immo.example.com
WEB_PROXY_SECRET=coller_ici_le_secret_aleatoire
```

L’origine ne doit pas comporter de chemin ni de barre finale. Conserver les paramètres locaux existants `WEB_SITE_URL`, `WEB_BIND_HOST`, `WEB_PORT`, la base, les clés de collecte et le token Telegram sur ce serveur. Redémarrer le conteneur du bot après modification de son environnement :

```bash
docker compose --project-name tgbbot up -d --force-recreate tgbbot
```

`https://backend.example.com/api/health` doit répondre `{"status":"ok"}`. Les liens « Fiche et photos sur le site » du bot utilisent alors `WEB_PROXY_ORIGIN`.

## 2. Envoyer et extraire l’archive dans Plesk

Dans **Sites Web et Domaines → Gestionnaire de fichiers**, aller dans le dossier du domaine qui **contient** `httpdocs`. Envoyer puis extraire l’archive. Elle contient :

```text
httpdocs/
  index.html
  assets/ et fonts/
  api/index.php
  .htaccess
private/
  immoalert.example.php
INSTALLATION-PLESK.md
SHA256SUMS
```

Les fichiers du dossier `httpdocs` doivent se trouver à la racine web du domaine, et non dans un sous-dossier `httpdocs/httpdocs`. Le dossier `private` doit rester **à côté de `httpdocs`**, hors du répertoire public. Si l’hébergeur interdit cet emplacement, lui demander un emplacement privé lisible par PHP et adapter le chemin dans `httpdocs/api/index.php` ; ne pas placer le fichier secret dans les fichiers publics.

Le frontend appelle explicitement `/api/index.php?route=...`. Son fonctionnement ne dépend donc pas des règles de réécriture Apache, même si Plesk utilise Nginx pour les fichiers statiques. `.htaccess` ajoute des protections lorsque le serveur le prend en charge.

La procédure d’envoi et d’extraction est décrite dans la [documentation Plesk du gestionnaire de fichiers](https://docs.plesk.com/en-US/obsidian/customer-guide/websites-and-domains/website-content/uploading-content-with-file-manager.74105/).

## 3. Configurer la passerelle

Dans le gestionnaire de fichiers, copier `private/immoalert.example.php` vers `private/immoalert.php`, puis renseigner :

```php
<?php
return [
    'backend_url' => 'https://backend.example.com',
    'public_origin' => 'https://immo.example.com',
    'shared_secret' => 'le_meme_secret_que_WEB_PROXY_SECRET',
];
```

Limiter les droits de ce fichier à son propriétaire et à PHP (habituellement `600`, ou `640` si l’hébergeur utilise un groupe PHP distinct). Le navigateur ne reçoit jamais le secret de liaison. Les requêtes sont signées avec HMAC-SHA256, horodatées et protégées contre la réutilisation d’une même signature.

Dans **Paramètres PHP**, sélectionner PHP 8.2 ou supérieur et vérifier `curl`. Activer la redirection HTTP vers HTTPS dans les paramètres d’hébergement Plesk.

## 4. Se connecter et créer les accès

Ouvrir le domaine HTTPS. Un administrateur déjà défini par `ADMIN_TELEGRAM_IDS` peut utiliser **Se connecter → Continuer avec Telegram** pour accéder à l’administration, puis **Comptes utilisateurs → Créer un accès**. Il peut créer son propre identifiant en choisissant son profil Telegram existant. Les comptes nouveaux fonctionnent sans Telegram.

Pour un serveur neuf, un administrateur disposant du terminal du **backend** peut initialiser un accès avec :

```bash
docker compose --project-name tgbbot exec tgbbot python scripts/create_web_admin.py --login administrateur
```

Le mot de passe généré s’affiche une seule fois. Aucun compte par défaut ni mot de passe prédéfini n’est livré. L’interface publique ne comporte aucune inscription. Les mots de passe sont stockés sous forme de dérivés scrypt avec un sel propre à chacun.

Un utilisateur peut ensuite relier Telegram depuis **Mon compte**. Une approbation explicite dans le bot est nécessaire. Ses recherches et favoris sont conservés ; si un profil Telegram existe déjà, les données sont regroupées sans écraser les favoris. Une recherche homonyme est conservée sous un nom distinct et peut être inactive si le compte Free possède déjà sa recherche principale.

## Vérifications après installation

1. `https://immo.example.com/api/index.php?route=%2Fapi%2Fhealth` répond `{"status":"ok"}`.
2. L’identifiant et le mot de passe ouvrent le site sans Telegram.
3. Un membre ne voit ni les comptes, ni la supervision, et reçoit un refus s’il appelle directement les routes admin.
4. L’administrateur voit les sources, les collectes en cours et le trafic dans **Sources & activité**.
5. Un changement de Premium prend effet sur le même profil dans le site et le bot ; désactiver un accès ou régénérer son mot de passe révoque ses sessions web.

Une réponse `503` signale une configuration manquante ou invalide, ou une extension PHP indisponible. Une réponse `502` signale un backend inaccessible, un problème TLS ou une réponse inattendue. Un `403` peut signaler une origine ou un secret qui ne concorde pas. Les erreurs publiques ne révèlent pas les clés ni les détails cURL.

## Reconstruire une archive après une modification

Sur le poste de développement, avec Node.js 22.12+ et Python, depuis la racine du dépôt :

```bash
npm --prefix web ci
python3 scripts/package_plesk.py
```

Le frontend est versionné dans `web/`. Cette commande produit `dist/immoalert-plesk.zip` et son SHA-256. Elle compile dans un répertoire temporaire et conserve le build local du site. Elle embarque exclusivement les fichiers du site, la passerelle, la configuration d’exemple et ce guide. Aucune base, session, clé API, configuration `.env` ni mot de passe utilisateur n’est inclus.

## Version 2.1

Télécharger l’archive `immoalert-plesk.zip` et son empreinte SHA-256 depuis les [versions GitHub](https://github.com/ZEBDA1/ImmoAlert/releases/latest). Transférer tout `httpdocs/`, y compris `api/index.php`, et conserver la configuration privée propre à l’instance. Mettre à jour le backend depuis la même version pour disposer des routes de suivi, de comparaison, de notifications et de localisation.

Les migrations de démarrage sont additives. Effectuer une sauvegarde cohérente de la base et du fichier maître des clés avant toute mise à jour. Aucun fichier de production ne fait partie de l’archive. Les champs des deux plans FlashProxy se trouvent dans Administration → Sources & activité → Clés et quotas.
