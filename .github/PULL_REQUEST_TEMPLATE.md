## Ce que change cette PR

<!-- Une ou deux phrases : le problème, puis la solution. -->

## Vérifications

- [ ] Tests serveur verts (`server/tests/run_all.sh`) si `server/` est touché
- [ ] Client compilé et essayé (build Dev) si `src/` est touché
- [ ] Compatibilité : un ancien client marche avec ce serveur, et ce client avec un ancien serveur (nouvelles fonctions annoncées dans `SERVER_FEATURES` et masquées sinon)
- [ ] Documentation à jour (README, FAQ, `server/README.md`, page du site si visible par les utilisateurs)
- [ ] Version incrémentée (csproj **et** `installer/setup.iss`, vérifiée par `tools/check-versions.ps1`) si c'est une release

## Déploiement

<!-- Rien de particulier / réglage à ajouter dans config.json / dépendance à installer sur le serveur / etc. -->
