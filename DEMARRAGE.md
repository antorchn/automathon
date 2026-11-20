# Démarrage rapide — Automathon 1.0.0

Bienvenue ! Automathon est un duel 2D de vaisseaux : deux IA s'affrontent dans une arène avec murs, mitrailleuse,
missile guidé, bouclier et dash, pendant qu'une zone sûre se referme sur elles. Le jeu existe en deux versions :

- le **jeu graphique** (Unity), pour regarder les matchs et jouer contre votre agent ;
- le **Headless**, un simulateur sans affichage, beaucoup plus rapide, pour entraîner et évaluer les agents.

Les agents s'écrivent en Python, dans le kit `PythonAI/`. Le kit de votre plateforme contient le jeu graphique et le
Headless déjà compilés : vous n'avez besoin ni de Unity ni de .NET.

## 1. Installation (une fois, environ 10 minutes)

1. **Le kit** : décompressez l'archive de votre plateforme (`Automathon_1.0.0_Windows.zip`, `…_Linux-x86-64.zip`,
   `…_MacOS-arm64.zip` ou `…_MacOS-x64.zip`). Sous Windows, choisissez un chemin court, par exemple `C:\automathon`.
2. **uv**, qui installe Python et toutes les dépendances aux versions exactes du kit :

   - Windows (PowerShell) :

     ```powershell
     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
     ```

   - macOS et Linux :

     ```bash
     curl -LsSf https://astral.sh/uv/install.sh | sh
     ```

   Puis fermez et rouvrez le terminal.
3. **Les dépendances**, depuis le dossier `PythonAI` du kit :

   ```bash
   cd PythonAI
   uv sync
   ```

   `uv sync` crée l'environnement Python dans `PythonAI/.venv` (PyTorch compris, c'est l'étape la plus longue).
   **Toutes les commandes ci-dessous se lancent depuis `PythonAI/`.**
4. **Vérifiez que tout marche** :

   ```bash
   uv run python ../tools/smoke_test.py
   ```

   Le test joue quelques matchs dans le Headless et finit par `OK`.

Au premier lancement, le pare-feu de Windows peut demander d'autoriser Python ou le jeu : acceptez (réseau privé).
Sur Mac, le kit retire lui-même la quarantaine de macOS sur le jeu et le Headless quand il les lance.

## 2. Regarder un match

Ouvrez deux terminaux dans `PythonAI/` :

```bash
uv run python agents/run_my_agent.py rush_bot --launch-game
```

```bash
uv run python agents/run_my_agent.py dummy_bot --port 5556
```

Dans le menu du jeu :

1. cliquez sur **PLAYER** au-dessus d'un vaisseau pour le passer en **AI** ;
2. saisissez le port (`5555` pour le premier agent, `5556` pour le second) dans **AI TCP PORT**, puis Entrée :
   « CONNECTION SUCCESSFUL ! » doit s'afficher ;
3. faites de même pour l'autre vaisseau, puis lancez la partie (**PLAY**).

Un vaisseau laissé en **PLAYER** se pilote au clavier ou à la manette : vous pouvez jouer contre votre agent.
Un match dure au plus une minute environ : à partir de 10 s, la zone sûre se resserre (l'extérieur est teinté de
rouge) et se ferme à 40 s ; ensuite, les deux vaisseaux perdent des PV jusqu'à ce que le plus faible tombe.

Les agents fournis :

| Agent | Ce qu'il fait |
|---|---|
| `rush_bot` | Fonce sur l'adversaire en déclenchant tout ce qui est rechargé : l'adversaire de référence |
| `dummy_bot` | Reste immobile et tire tout droit |
| `my_rl_agent` | Modèle d'agent RL : joue le modèle que vous entraînez (partie 4). Aucun modèle n'est fourni : sans entraînement, il ne se lance pas |

## 3. Faire des duels sans affichage

Beaucoup plus rapide que le jeu graphique, avec exactement les mêmes résultats :

```bash
uv run python evaluation/run_matchup.py rush_bot dummy_bot --games 20
```

## 4. Entraîner un agent par renforcement

```bash
uv run python training/rl_training.py
```

C'est un exemple d'entraînement, à modifier ou à remplacer : un PPO simple contre `rush_bot`, qui atteint environ
43 % de victoires contre lui en 3 millions de pas. Le modèle final est écrit dans `training/rl_model.zip`, les
checkpoints dans `training/checkpoints/`.
Ensuite :

```bash
uv run python evaluation/evaluate_model.py training/rl_model.zip --opponent rush_bot
uv run python agents/run_my_agent.py my_rl_agent --launch-game
```

La première commande mesure son taux de victoire sur 100 matchs. La seconde permet de le regarder jouer (au premier
lancement, l'extracteur de features est compilé : une vingtaine de secondes). Pour rendre cet agent, copiez le
modèle en `agents/my_rl_agent/model.zip` : le dossier de l'agent contient alors tout ce qu'il lui faut.

Tous les réglages se changent en ligne de commande, par exemple `total_timesteps=10000000` ou
`opponent=dummy_bot` : voir la section 10 de `PythonAI/README.md`. Sans PyTorch (Mac Intel),
entraînez sur Databricks (partie 6).

## 5. Écrire son propre bot

Un bot est un dossier `agents/<nom>/` qui contient un fichier `agent.py` avec une classe `ParticipantAgent`. À
chaque décision, le jeu appelle sa méthode `get_action(state)`, qui reçoit l'état du match (`GameState` : votre
vaisseau, l'adversaire, balles, missiles, boucliers, murs) et renvoie une action (`AIAction` : direction de
déplacement, direction de visée et quatre boutons). Le plus simple est de copier `agents/rush_bot/` (30 lignes) et
de le modifier. Votre bot se lance ensuite comme les autres : `run_my_agent.py`, `run_matchup.py`.

Les règles à connaître :

- le jeu tourne à 60 images par seconde, mais **une IA ne décide qu'une frame sur 4** (15 fois par seconde) et
  garde son action entre-temps ;
- une balle fait 60 dégâts, un missile 600 au contact et 400 autour (les vaisseaux ont 1 000 PV) ; **un missile
  n'est armé qu'après 1 unité de vol** : tiré à bout portant, il disparaît sans effet ;
- **la zone sûre** : à partir de 10 s, elle rétrécit vers le centre et se ferme à 40 s ; un vaisseau dont le centre
  est dehors perd 50 PV par seconde. Sa taille est dans l'état (`state.ZoneHalfSize`) ;
- le jeu attend la réponse au plus 500 ms ; visez quelques millisecondes ;
- les distances sont en unités Unity : l'arène va de -13,70 à 13,70 en x et de -7,70 à 7,70 en y
  (`ARENA_HALF_WIDTH`, `ARENA_HALF_HEIGHT` dans `bridge/datatypes.py`), les vitesses sont en unités par seconde.

## 6. Entraîner dans le cloud (Databricks)

Pendant le hackathon, suivez le guide `databricks/guide_participants_automathon.html` (à ouvrir dans un navigateur) :
il présente le cluster de votre équipe, le notebook `databricks/automathon_demarrage.ipynb`, qui entraîne un premier
agent et prépare son zip, et l'envoi de votre agent dans l'Arène.

Le notebook `databricks/Automathon_Databricks.ipynb` détaille davantage l'entraînement sur Databricks, avec le kit
`Automathon_1.0.0_Linux-ARM-databricks.zip` (machines ARM) ou `Automathon_1.0.0_Linux-x86-64.zip` (machines x86) :
il choisit le bon selon la machine, et explique chaque étape, de l'installation à la récupération du modèle sur
votre PC.

## Pour aller plus loin

`PythonAI/README.md` est la documentation complète du kit : mécaniques et règles du jeu, protocole, environnement
d'entraînement, extracteur de features, évaluation, ce qui rend l'entraînement rapide et comment aller plus vite
(section 14), règles du hackathon (section 15), dépannage.
