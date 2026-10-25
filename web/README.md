# Interface ImmoAlert

Site public : **https://immoalert.work.gd**. Les accès sont créés par un administrateur ; Telegram est facultatif.

Le frontend React, TypeScript et Vite se trouve dans ce dossier. L’API Python et les profils du bot sont partagés avec le site. Les polices sont servies localement et leurs licences sont conservées dans `public/fonts/`.

## Développement

Depuis ce dossier, avec Node.js 22.12+ :

```bash
npm ci
npm run dev
```

L’interface écoute sur `http://127.0.0.1:5182`. Le proxy Vite transmet `/api` à `http://127.0.0.1:8088`. Pour tester les écritures en développement, configurer l’origine autorisée du backend sur `WEB_SITE_URL=http://127.0.0.1:5182` et son port sur `WEB_PORT=8088` dans un environnement de développement distinct.

## Construction

```bash
npm run build
```

La vérification TypeScript précède le build dans `dist/`. Docker construit automatiquement ce frontend et l’embarque dans `/app/web_dist` ; aucun autre dépôt ni dossier voisin n’est requis.

Pour Plesk mutualisé, depuis la racine du dépôt :

```bash
npm --prefix web ci
python3 scripts/package_plesk.py
```

L’archive est produite dans `dist/immoalert-plesk.zip`. Elle contient un build utilisant la passerelle PHP ; le build local reste inchangé. Voir le [guide Plesk](../docs/PLESK.md).
