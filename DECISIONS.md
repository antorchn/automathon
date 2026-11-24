# Décisions d'implémentation — Arène Automathon en ligne (V0)

Ce document consigne les choix pris pour les points laissés ouverts par `Spec — Arène Automathon en ligne.md`, conformément à la règle : « Une question que la spec ne tranche pas : prendre sa valeur par défaut, et noter le choix dans un fichier `DECISIONS.md` du dépôt. »

## 1. Hôte d'agent (`agent_host/host.py`)
- **Redirection de `sys.stdout` vers `sys.stderr`** : le protocole JSON-line entre l'arbitre et l'hôte d'agent passe par le descripteur de fichier 1 (`os.dup(1)`). Au démarrage, `sys.stdout` est redirigé vers `sys.stderr` pour que les `print()` d'un agent participant ne corrompent jamais les messages JSON du protocole.
- **Résolution des imports de l'agent** : certains agents importent en relatif (`from .features import ...`) ou en absolu depuis `agents.<nom>` (`from agents.mon_bot.features import ...`). L'hôte injecte un module namespace `agents` et lie `agents.<AGENT_NAME>` vers `AGENT_DIR` en plus d'ajouter le parent et `AGENT_DIR` à `sys.path`.
- **Préchauffage (`init`)** : `init` importe `agent.py`, instancie `ParticipantAgent()` et vérifie l'existence d'une méthode `get_action` appelable sans exécuter de partie complète (les parties réelles de validation suivent immédiatement).

## 2. Isolation Docker et montage des agents (`worker/sandbox.py`, `docker-compose.yml`)
- **Sous-chemin de volume Docker (`volume-subpath`)** : le socket Docker étant monté depuis l'hôte, un montage par chemin du conteneur worker échouerait. Les agents extraits vivent donc dans le volume nommé `arene_agent_work` (monté dans `/agent_work` côté worker) et chaque conteneur d'agent monte uniquement son propre sous-dossier en lecture seule via `--mount type=volume,source=arene_agent_work,target=/agent,readonly,volume-subpath=<subpath>`. Si Docker < 26 sur une machine externe ne supportait pas `volume-subpath`, un fallback peut monter un volume temporaire ou copier le tar avant démarrage ; Debian 13 + dépôt officiel Docker fournit Docker >= 27 qui le supporte nativement.
- **Image `automathon-agent:1.0.0` dans `docker-compose.yml`** : un service `agent-image` dédié (`image: automathon-agent:1.0.0`, `build: agent_host/`, commande no-op `python -c "print('ready')"`, `restart: "no"`) permet à `docker compose build` de construire les trois images (`arene-web`, `arene-worker`, `automathon-agent:1.0.0`) d'une seule commande et garantit que l'image existe avant le démarrage de `arene-worker`.

## 3. Gestion des délais et boucles infinies (`worker/sandbox.py`, `worker/referee.py`)
- **Suivi non bloquant des requêtes en vol (`in_flight`)** : si un agent entre en boucle infinie dans `get_action`, la première décision qui dépasse `DECISION_TIMEOUT_MS` (500 ms) bloque au plus 500 ms, puis marque la requête comme toujours en vol. Aux frames suivantes, l'arbitre n'attend pas inutilement `DECISION_TIMEOUT_MS` supplémentaire tant que le conteneur n'a pas fini sa décision précédente : il garde immédiatement l'action précédente et ignore toute réponse tardive dont `decision_id` est inférieur au `decision_id` courant. Si l'agent ne répond plus à aucune décision ou dépasse `DECISION_MEAN_MS_LIMIT` (5 ms CPU), il perd la partie.
- **Mesure du temps CPU** : lecture directe de `usage_usec` dans `<CGROUP_ROOT>/.../cpu.stat` (cgroup v2) côté worker. En environnement de test local hors Docker (sans cgroup dédié), le worker retombe sur l'horloge CPU processus ou le temps mesuré.

## 4. Base de données et migrations (`arene/db.py`, `arene/migrations.py`)
- **SQLAlchemy 2.0 + migrations SQL versionnées** : les requêtes passent par SQLAlchemy 2.0 (compatible SQLite en V0 et PostgreSQL ensuite), avec mode WAL et `BEGIN IMMEDIATE` pour la réservation atomique des jobs sous SQLite (`FOR UPDATE SKIP LOCKED` sous PostgreSQL). Les migrations numérotées sont enregistrées dans `schema_migrations` et appliquées automatiquement au démarrage d'`arene-web`.

## 5. Bots de référence et classement (`arene/scheduler.py`, `arene/elo.py`)
- **Initialisation des bots de référence** : au démarrage (s'ils ne sont pas encore en base), `arene-web` enregistre un compte système `automathon` contenant `rush_bot` (ancre fixe à `1000.0`, non provisoire, actif) et `dummy_bot` (actif, initialement à `1000.0` et marqué provisoire jusqu'à ce qu'il ait joué assez de parties pour passer sous `PROVISIONAL_MARGIN`).
- **A priori de Bradley-Terry** : 2 nuls virtuels contre `rush_bot` (`n_prior = 2.0`, `w_prior = 1.0`) pour chaque autre agent actif. L'incertitude suit la formule de la spec ($\sigma_i = (400 / \ln 10) / \sqrt{\sum_j n_{ij} p_{ij} (1 - p_{ij})}$, marge $= 1{,}96\,\sigma_i$).
- **Affinage en tâche de fond (`BACKGROUND_DUTY`)** : après la fin d'un job d'affinage de durée $D$, le worker ou le planificateur respecte une pause d'au moins $D \cdot (1 - \text{duty}) / \text{duty}$ (pour 50 %, une pause égale à la durée du dernier job d'affinage) avant de créer le job d'affinage suivant.

## 6. Authentification et proxy (`arene/auth.py`, `arene/middleware.py`)
- **En-têtes `X-Forwarded-*`** : ignorés sauf si l'IP source de la connexion TCP est incluse dans `TRUSTED_PROXY` (défaut `127.0.0.1`, Caddy local).
- **`AUTH_DEV_MODE`** : refus strict au démarrage si `AUTH_DEV_MODE=true` et `PUBLIC_URL` commence par `https://`.

## 7. Dépôt de publication
- **Code public, dans le dépôt des participants** : l'arène vit dans `antorchn/automathon-rl` et part avec l'export du kit (`python tools/build.py export`) vers `antorchn/automathon`, d'où la VM la clone et la déploie (`deploy.sh`, branche `main`). Aucune clé d'accès n'est donc nécessaire sur la VM. Seul `scripts/run_local_leaderboard.py`, qui lit des agents d'organisateurs hors du dépôt, reste exclu de l'export. Le code de l'arène ne contient aucun secret : ils vivent dans `.env`, ignoré par git.
