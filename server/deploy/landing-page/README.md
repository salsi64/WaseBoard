# Page d'accueil (landing page)

Sert à `https://waseboard.salsi.bid/` et `https://waseboard.duckdns.org/` (même fichier, même dossier) :
nginx la renvoie directement sur `location = /` exact, tout le reste part vers le serveur WaseBoard
(voir `nginx-waseboard-salsi-bid.conf`, copie de référence de la configuration réellement déployée).

Non protégée par `deploy/backup.sh` (qui ne couvre que les données de l'application) ni par aucune
sauvegarde automatique — c'est pour ça qu'une copie vit ici. Pour la mettre à jour sur le serveur :

```bash
scp index.html VOTRE_USER@VOTRE_SERVEUR:/tmp/index.html.new
ssh VOTRE_USER@VOTRE_SERVEUR 'sudo install -m 664 -o www-data -g www-data /tmp/index.html.new /var/www/waseboard-landing/index.html && rm /tmp/index.html.new'
```

Pour l'activer sur un nouveau domaine : ajouter le bloc `root`/`index`/`location = /` de
`nginx-waseboard-salsi-bid.conf` au site nginx concerné (même dossier `/var/www/waseboard-landing`,
pas besoin de dupliquer le fichier), puis `sudo nginx -t && sudo systemctl reload nginx`.
