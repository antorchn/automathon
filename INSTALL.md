# Installation et exploitation — Arène Automathon en ligne (V0)

Guide de déploiement pour la VM dédiée **vr-4v-4g** (4 vCPU, 4 Go de RAM, 30 Go de disque, Debian 13). Le code vient
du dépôt public [antorchn/automathon](https://github.com/antorchn/automathon) : aucune clé d'accès n'est nécessaire
pour le cloner. Toutes les commandes se lancent sur la VM, connecté en SSH, sauf le tunnel de la section 5.

## 1. Récupérer le code

```bash
sudo apt-get update && sudo apt-get install -y git
sudo mkdir -p /opt/automathon-arene && sudo chown "$USER": /opt/automathon-arene
git clone https://github.com/antorchn/automathon.git /opt/automathon-arene
cd /opt/automathon-arene
```

## 2. Préparer la VM (une seule fois)

Le script installe Docker CE et Compose v2 (dépôt officiel de Docker), crée un swap de 2 Go, vérifie les cgroups v2
et l'isolation des agents, active le pare-feu `ufw` (SSH seulement) et ajoute l'utilisateur courant au groupe
`docker` :

```bash
sudo ./scripts/setup_vm.sh --mode dev
```

- SSH sur un autre port que 22 : ajouter `--ssh-port <port>`, sinon le pare-feu coupe l'accès. Garder sa session
  ouverte et vérifier dans un second terminal qu'on peut toujours se connecter.
- Se déconnecter puis se reconnecter, pour que l'ajout au groupe `docker` prenne effet : `docker ps` doit alors
  marcher sans `sudo`.

Pour l'ouverture au public, relancer plus tard le script avec l'une des deux variantes :

```bash
# Sous-domaine public (Caddy gère HTTPS avec Let's Encrypt)
sudo ./scripts/setup_vm.sh --mode subdomain --domain arene.exemple.fr

# Derrière le reverse proxy du site principal (port 8000 ouvert uniquement à son IP)
sudo ./scripts/setup_vm.sh --mode proxy --proxy-ip 10.0.0.2
```

Vérifier ensuite que seuls les ports prévus écoutent : `sudo ufw status verbose` et `ss -tlnp`.

## 3. Configurer `.env`

Pour les tests, connexion sans GitHub et accès par tunnel SSH :

```bash
cp .env.example .env && chmod 600 .env
sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$(openssl rand -hex 32)/; s/^WORKER_TOKEN=.*/WORKER_TOKEN=$(openssl rand -hex 32)/; s/^AUTH_DEV_MODE=.*/AUTH_DEV_MODE=true/" .env
```

Garder `PUBLIC_URL=http://localhost:8001/arene` : c'est l'adresse du tunnel de la section 5.

Pour la production, renseigner en plus :

- `PUBLIC_URL` : URL publique exacte, en `https://` (ex. `https://exemple.fr/arene` ou `https://arene.exemple.fr`) ;
- `ARENE_BASE_PATH` : `/arene` derrière le proxy du site, `/` pour un sous-domaine. **Avec `/`, mettre aussi
  `SITE_URL=http://arene-web:8000`**, sinon le worker ne trouve plus le site ;
- `GITHUB_CLIENT_ID` et `GITHUB_CLIENT_SECRET` : fournis par l'OAuth App GitHub, dont l'URL de retour est
  `<PUBLIC_URL>/auth/github/callback` ;
- `ADMIN_GITHUB_LOGINS` : logins GitHub autorisés sur `/admin`, séparés par des virgules ;
- `AUTH_DEV_MODE=false` : l'arène refuse de démarrer avec `true` si `PUBLIC_URL` est en HTTPS.

> Ne jamais commiter `.env` dans git.

## 4. Déployer et mettre à jour

Dans `/opt/automathon-arene` :

```bash
./scripts/deploy.sh --local
```

Ce script sert aussi aux mises à jour. Il :

1. récupère la dernière version de la branche `main` ;
2. télécharge et vérifie par SHA-256 les archives de la release `v1.0.0` (`scripts/fetch_sources.py --extract`) ;
3. construit les trois images (`arene-web`, `arene-worker`, `automathon-agent:1.0.0`). La première fois, comptez
   plusieurs minutes : l'image des agents télécharge PyTorch ;
4. démarre la pile (`docker compose up -d`), applique les migrations de la base et vérifie `/health` et `ss -tlnp`.

À la fin, `docker compose ps` doit montrer `arene-web` et `arene-worker` démarrés, et `ss -tlnp` ne doit montrer que
SSH et `127.0.0.1:8001`.

Depuis un poste qui a bash et SSH, `./scripts/deploy.sh <utilisateur>@<adresse-vm>` fait la même chose à distance.

## 5. Ouvrir le site pendant les tests

Depuis votre poste, ouvrir un tunnel SSH :

```bash
ssh -L 8001:localhost:8001 <utilisateur>@<adresse-vm>
```

Puis ouvrir `http://localhost:8001/arene/`. Le port local est le même que dans `PUBLIC_URL`, pour que les liens du site
marchent.

## 6. Test de bout en bout

Avec `AUTH_DEV_MODE=true`, sur la VM (`python3-httpx` est installé par `setup_vm.sh`) :

```bash
python3 scripts/e2e_test.py --url http://127.0.0.1:8001/arene --include-hostile
```

## 7. Sauvegarde et restauration

### Sauvegarde à chaud
```bash
./scripts/backup.sh /var/backups/automathon-arene
```
Le script utilise `sqlite3 data/arene.db ".backup ..."` pour copier la base SQLite à chaud sans arrêter le site, puis archive les zips et replays de `data/`.

### Restauration
```bash
docker compose down
cp /var/backups/automathon-arene/<horodatage>/arene.db data/arene.db
tar -xzf /var/backups/automathon-arene/<horodatage>/data_files.tar.gz -C data/
docker compose up -d
```

## 8. Journaux et supervision

```bash
docker compose ps
docker compose logs -f arene-web
docker compose logs -f arene-worker
docker stats
```

## 9. Ajouter un worker sur une autre machine

Sur une machine externe disposant de Docker :
1. Cloner le dépôt et exécuter `python3 scripts/fetch_sources.py --extract` ;
2. Construire les images `automathon-agent:1.0.0` et `arene-worker` :
   ```bash
   docker compose build agent-image arene-worker
   docker compose run --rm agent-image
   ```
3. Démarrer uniquement `arene-worker` avec l'URL publique et le même `WORKER_TOKEN` :
   ```bash
   SITE_URL=https://exemple.fr/arene WORKER_TOKEN=... WORKER_PARALLEL_MATCHES=4 WORKER_NICE=10 docker compose up -d arene-worker
   ```
4. Monter `MAX_PLACEMENTS_IN_PROGRESS`, `PLACEMENT_MAPS_PER_OPPONENT`, etc. dans le `.env` du site et redémarrer `arene-web`.

## 10. Ajouter des agents invités (administrateur)

Pour des agents sans compte GitHub, par exemple ceux du tournoi final. Chaque zip au format de l'arène (un dossier
`<nom>/` avec `agent.py`) crée ou met à jour le compte invité de ce nom de dossier, affiché sous le nom de l'équipe.
Les agents passent par la validation et le placement habituels, un à la fois, sans cooldown ni limite de file.
Personne ne peut se connecter sur un compte invité.

- **Un ou quelques zips** : page `/admin`, section « Ajouter des agents invités ». Pour un seul zip, le champ
  « Nom affiché » donne le nom de l'équipe ; sinon, c'est le nom du dossier.
- **Beaucoup d'agents, avec leurs noms d'équipe** : un dossier qui contient les zips et un `equipes.json`
  (`[{"zip": "<nom>.zip", "team": "Nom de l'équipe"}, ...]`), copié dans `data/import/` sur la VM :

```bash
# Depuis votre poste
scp -r <dossier-des-zips> <utilisateur>@<adresse-vm>:/opt/automathon-arene/data/import
# Sur la VM, dans /opt/automathon-arene
docker compose exec arene-web python -m arene.import_agents /data/import
```

Comptez de 2 à 11 minutes de calcul par agent importé : 21 agents occupent la VM une à quatre heures. La page `/admin`
montre la file.
