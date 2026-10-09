# Page d'accueil (landing page)

Sert à `https://waseboard.salsi.bid/` et `https://waseboard.duckdns.org/` (même fichier, même dossier) :
nginx la renvoie directement sur `location = /` exact, tout le reste part vers le serveur WaseBoard
(voir `nginx-waseboard-salsi-bid.conf`, copie de référence de la configuration réellement déployée).

Non protégée par `deploy/backup.sh` (qui ne couvre que les données de l'application) ni par aucune
sauvegarde automatique — c'est pour ça qu'une copie vit ici. Pour la mettre à jour sur le serveur :

Les captures d'écran sont dans `img/` (servies par `location ^~ /img/`). Pour éviter le cache de Cloudflare,
donner un nouveau nom de fichier à une capture remplacée (ex. `hero-flat.png` → `hero-flat-2.png`).

```bash
scp index.html VOTRE_USER@VOTRE_SERVEUR:/tmp/index.html.new
ssh VOTRE_USER@VOTRE_SERVEUR 'sudo install -m 664 -o www-data -g www-data /tmp/index.html.new /var/www/waseboard-landing/index.html && rm /tmp/index.html.new'
```

Pages annexes servies de la même façon (`privacy.html`, `terms.html`, `micro-en-jeu.html`) : chacune a
sa règle `location = /….html` dans le site nginx. Pour en ajouter une, copier le fichier dans
`/var/www/waseboard-landing/` **et** ajouter sa règle (voir `nginx-waseboard-salsi-bid.conf`), puis
`sudo nginx -t && sudo systemctl reload nginx` : sans la règle, l'URL part vers le serveur WaseBoard (404).

Pour l'activer sur un nouveau domaine : ajouter le bloc `root`/`index`/`location = /` de
`nginx-waseboard-salsi-bid.conf` au site nginx concerné (même dossier `/var/www/waseboard-landing`,
pas besoin de dupliquer le fichier) ainsi que le bloc `location ^~ /img/`, puis `sudo nginx -t && sudo systemctl reload nginx`.
