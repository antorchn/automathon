# Automathon — Kit Python

> **Version** 1.0.0 · **Jeu** : Unity (Windows, Linux, macOS) · **Agents** : Python 3.10+ (3.12 recommandé)
>
> Pour démarrer en quelques minutes, suivre d'abord le guide [DEMARRAGE.md](../DEMARRAGE.md). Ce document est la
> référence complète du kit.

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Contenu du kit](#2-contenu-du-kit)
3. [Installation](#3-installation)
4. [Entraîner dans le cloud, jouer sur son PC](#4-entraîner-dans-le-cloud-jouer-sur-son-pc)
5. [Structure du dossier `PythonAI`](#5-structure-du-dossier-pythonai)
6. [Communication avec le jeu (`bridge/`)](#6-communication-avec-le-jeu-bridge)
7. [Types de données (`bridge/datatypes.py`)](#7-types-de-données-bridgedatatypespy)
8. [Environnement RL (`core/env/`)](#8-environnement-rl-coreenv)
9. [Agents (`agents/`)](#9-agents-agents)
10. [Entraînement (`training/`)](#10-entraînement-training)
11. [Évaluation (`evaluation/`)](#11-évaluation-evaluation)
12. [Écrire et tester son agent](#12-écrire-et-tester-son-agent)
13. [Constantes du jeu](#13-constantes-du-jeu)
14. [Performance de l'entraînement](#14-performance-de-lentraînement)
15. [Règles du hackathon](#15-règles-du-hackathon)
16. [Dépannage](#16-dépannage)

---

## 1. Vue d'ensemble

**Automathon** est un duel de vaisseaux spatiaux en 2D, conçu comme support d'un **hackathon d'intelligence
artificielle**. Deux vaisseaux s'affrontent dans une arène parsemée de murs. Chaque équipe écrit en Python l'agent
qui pilote son vaisseau : règles écrites à la main, apprentissage par renforcement, ou les deux.

Le jeu existe en deux versions, qui simulent exactement les mêmes matchs :
- le **jeu graphique** (Unity), pour regarder les matchs et jouer contre son agent ;
- le **Headless**, un simulateur sans affichage, beaucoup plus rapide, pour entraîner et évaluer les agents.

Le kit fournit deux choses de nature différente :
- **ce qui est commun à tous** : le jeu et ses règles, la communication avec le jeu (`bridge/`) et l'interface d'un
  agent (`get_action(state)`, section 9). Les matchs officiels se jouent avec eux ;
- **un exemple d'entraînement** : un environnement Gymnasium (`core/env/`, section 8), une récompense, un extracteur
  de features et un script PPO (`training/`, section 10), qui apprend à jouer contre `rush_bot`. C'est un point de
  départ : vous pouvez le modifier ou le remplacer entièrement, et entraîner votre agent comme vous l'entendez.

### Mécaniques de jeu

| Mécanique | Description |
|---|---|
| **Machine Gun** | Rafale de 10 balles (60 dégâts chacune) en 0,5 s, dans la direction de visée. Balles à 17 u/s. Recharge 2 s (120 frames). |
| **Missile** | Part 1,5 unité devant le vaisseau, à 9 u/s, et tourne vers la direction de visée actuelle du tireur. **Armé après 1 unité de vol** : s'il touche quoi que ce soit avant, il disparaît sans aucun effet (un tir à moins d'environ 3 unités de l'adversaire est perdu). Armé, il explose au premier contact : 600 dégâts à ce qu'il touche, 400 dans un rayon de 1 (chaque entité reçoit au plus une fois le plus fort des deux), et il détruit les boucliers dans ce rayon. Le tireur n'est pas protégé de sa propre explosion. Recharge 1,5 s (90 frames). Dans le jeu, un missile non armé est petit et gris-bleu ; il reprend sa couleur jaune en s'armant. |
| **Shield** | Mur de 1,5 × 0,2 (1 500 PV, 10 s) posé 2 unités devant le vaisseau, perpendiculaire à la visée. Recharge 2 s (120 frames). |
| **Dash** | 25 u/s pendant 0,15 s dans la direction de déplacement, invincible, traverse balles, missiles et boucliers (pas les murs). Recharge 0,7 s (42 frames). |
| **Arène** | Rectangle de 27,4 × 15,4 centré en (0, 0), fermé par quatre murs invisibles qui arrêtent les vaisseaux et les missiles. Les balles les traversent et sont détruites 2 unités plus loin. Ces murs ne sont pas dans `WallStates` : leur face intérieure est en x = ±`ARENA_HALF_WIDTH` (13,70) et y = ±`ARENA_HALF_HEIGHT` (7,70), constantes de `bridge/datatypes.py`. Le centre d'un vaisseau (rayon 0,419) s'arrête donc vers \|x\| = 13,3 et \|y\| = 7,3. |
| **Zone sûre** | Rectangle centré sur l'arène : il la couvre entière jusqu'à 10 s, puis rétrécit linéairement et **se ferme complètement à 40 s**. Un vaisseau dont le centre est hors de la zone perd 50 PV par seconde (sauf pendant un dash, où il est invincible comme contre tout dégât). Sa demi-taille est dans l'état (`GameState.ZoneHalfSize`) ; dans le jeu, l'extérieur de la zone est teinté de rouge. |
| **Murs** | Obstacles statiques, générés aléatoirement à chaque match (une quinzaine), symétriques par rapport au centre. Ils bloquent les projectiles et les déplacements. Aucun mur à moins de 1,5 unité des points d'apparition (±10, 0). |
| **Décisions à 15 Hz** | Le jeu tourne à 60 frames par seconde, mais un agent IA ne décide qu'une frame sur 4 (frames 0, 4, 8… de chaque match, les mêmes pour les deux vaisseaux) et garde son action entre-temps. Les deux agents décident sur le même état, celui de la fin de la frame précédente. C'est exactement l'entraînement avec `frame_skip=4` (valeur par défaut). Les joueurs humains ne sont pas limités. Constante `DECISION_INTERVAL` (`bridge/datatypes.py`). |
| **Fin du match** | Quand un vaisseau tombe à 0 PV ; match nul si les deux tombent dans la même frame. Il n'y a pas de limite de temps : après 40 s, la zone est fermée, les deux vaisseaux perdent 50 PV/s, et celui qui a le plus de PV gagne. Un match dure donc au plus un peu plus de 60 s. Les outils du kit coupent quand même les matchs à 4 200 frames (70 s), sans vainqueur. Dans le jeu graphique, la fin passe au ralenti, une explosion de lumière part de l'épave, puis l'écran de fin apparaît. |

---

## 2. Contenu du kit

Le kit tout-en-un de chaque plateforme contient :

```
Automathon_1.0.0_<plateforme>/
├── DEMARRAGE.md               ← guide de démarrage
├── README.md
├── PythonAI/                  ← ce kit : bridge, environnement, agents d'exemple, entraînement, évaluation
├── Headless/                  ← simulateur sans affichage, pour l'entraînement (Headless.exe ou Headless)
└── jeu graphique
    ├── Windows : AutomathonGame.exe (+ AutomathonGame_Data/, UnityPlayer.dll…)
    ├── Linux   : AutomathonGame.x86_64 (+ AutomathonGame_Data/, UnityPlayer.so…)
    └── macOS   : AutomathonGame.app (binaire universel Intel + Apple Silicon)
```

| Kit | Pour |
|---|---|
| `Windows` | PC Windows 10 ou 11, 64 bits |
| `Linux-x86-64` | PC Linux x86-64 |
| `MacOS-arm64` / `MacOS-x64` | Mac Apple Silicon / Mac Intel |
| `Linux-ARM-databricks` | Databricks sur processeurs ARM (AWS Graviton) : Headless et kit Python, sans jeu graphique (Unity ne produit pas de jeu Linux ARM) |

Le code source du jeu (C#, projet Unity et Headless) est dans le dépôt du kit, dossier `game/`, pour qui veut lire
les règles exactes ou recompiler (`tools/build.py`, voir le README du dépôt).

### Communication

```
Python (agent)  <──── ZeroMQ TCP (localhost) ────>  Unity (Headless ou jeu graphique)
  Envoie : AIMessage                                    Renvoie : GameState
```

---

## 3. Installation

### 3.1 Binaires

Le jeu graphique et le dossier `Headless/` sont à côté de `PythonAI/` dans le kit : le code les trouve
automatiquement. Ailleurs, définir `AUTOMATHON_HEADLESS_PATH` et `AUTOMATHON_GAME_PATH`.

### 3.2 Environnement Python

Toutes les commandes se lancent depuis `PythonAI/`.

**Avec uv (recommandé)** : uv installe au besoin Python 3.12 et les versions exactes de `uv.lock`, identiques sur
tous les postes (même `torch` et `stable-baselines3` partout, donc des modèles compatibles). Installation de uv :
https://docs.astral.sh/uv/getting-started/installation/

```bash
cd PythonAI
uv sync                    # crée PythonAI/.venv
uv run python ../tools/smoke_test.py   # vérifie le Headless (3 matchs, termine par OK)
```

**Avec pip** :

```bash
cd PythonAI
python -m venv .venv
# Windows : .venv\Scripts\activate    |  Linux / macOS : source .venv/bin/activate
pip install -r requirements_participant.txt
```

`pyproject.toml` est la référence des dépendances ; `requirements_participant.txt` est gardé pour pip et Databricks.
Sous Linux, uv installe la version **CPU** de `torch` (plus légère : PPO avec un petit réseau ne profite pas du GPU).

### 3.3 Spécificités par OS

| OS | Points d'attention |
|---|---|
| **Windows** | Au premier lancement d'un agent, le pare-feu peut demander d'autoriser Python ou le jeu : accepter (réseau privé). |
| **Linux** | Les binaires doivent être exécutables : le code fait `chmod +x` automatiquement sur le Headless et le jeu. Pour lancer le jeu à la main : `chmod +x AutomathonGame.x86_64 && ./AutomathonGame.x86_64`. |
| **macOS (Apple Silicon)** | Un dossier téléchargé ou dézippé est mis en quarantaine par Gatekeeper. Le code retire la quarantaine (et re-signe ad hoc si besoin) du Headless et du jeu quand il les lance. À la main : `xattr -dr com.apple.quarantine AutomathonGame.app Headless/Headless`. `torch` requiert macOS 14+. |
| **macOS (Intel)** | `torch` n'est plus publié pour les Mac Intel : le jeu, le Headless et les agents écrits à la main fonctionnent, mais pas `my_rl_agent` ni l'entraînement (à faire sur Databricks). |

### 3.4 Databricks (entraînement dans le cloud)

Pendant le hackathon, le guide `databricks/guide_participants_automathon.html` et le notebook
`databricks/automathon_demarrage.ipynb` mènent du cluster de l'équipe à l'envoi d'un premier agent dans l'Arène.

Le notebook `databricks/Automathon_Databricks.ipynb` fait tout, cellule par cellule : installation des
bibliothèques, décompression du kit déposé dans un Volume (`Linux-ARM-databricks` ou `Linux-x86-64`, choisi selon le
processeur de la machine), diagnostic de la machine et du Headless, mesure du débit, duel de test, entraînement,
évaluation, sauvegarde des checkpoints et des modèles dans le Volume, export de l'agent.

---

## 4. Entraîner dans le cloud, jouer sur son PC

```
[Databricks]                              [PC Windows, Linux ou macOS]
─────────────────────────────────────     ────────────────────────────────────
1. Kit Linux-ARM-databricks + notebook
2. Coder son agent dans agents/
3. python training/rl_training.py
   → checkpoints auto (save.checkpoint_dir)
4. python training/export_agent.py
   → mon_agent_TIMESTAMP.zip
                                      5. Télécharger mon_agent_TIMESTAMP.zip
                                      6. Copier le modèle en
                                         PythonAI/agents/my_rl_agent/model.zip
                                      7. python agents/run_my_agent.py my_rl_agent --launch-game
                                         → dans le jeu : vaisseau en AI, port 5555, PLAY
```

Les modèles SB3 (`.zip`) sont **cross-platform** : un modèle entraîné sur Databricks se charge directement sur
Windows, Linux ou macOS (Apple Silicon), avec les versions de `torch` et `stable-baselines3` du kit.

### Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `AUTOMATHON_PORT_BASE` | `5600` | Port de base des instances Headless. Changer en cas de collision. |
| `AUTOMATHON_NUM_ENVS` | `min(nb CPU, 16)` | Nombre d'instances Headless en parallèle, quand `unity.num_envs` vaut `null` (`rl_training.py` en fixe 8). |
| `AUTOMATHON_STARTUP_WAIT` | `15` | Attente **maximale** (s) du démarrage d'un Headless. |
| `AUTOMATHON_CHECKPOINT_DIR` | `training/checkpoints/` | Répertoire des checkpoints. Pointer vers un stockage persistant sur Databricks. |
| `AUTOMATHON_HEADLESS_PATH` | *(auto-détecté)* | Chemin vers le binaire Headless. |
| `AUTOMATHON_GAME_PATH` | *(auto-détecté)* | Chemin vers le jeu graphique (`.exe`, binaire Linux ou `.app`). |
| `AUTOMATHON_RULES` | *(aucune)* | Jeu graphique : fichier JSON de règles à utiliser à la place des règles par défaut (champs de `GameRules`, section 6). Sans cette variable, le jeu lit `rules.json` à côté de son exécutable s'il existe. Pour tester une variante : les matchs officiels se jouent avec les règles par défaut. |
| `NUMBA_DISABLE_JIT` | *(aucune)* | `1` : le code Numba s'exécute en Python normal, pour déboguer un extracteur ou un bot compilé. |

Les réglages d'entraînement se font aussi avec **Hydra** (section 10), en ligne de commande.

### Ports utilisés

| Script | Ports (défaut) |
|---|---|
| `agents/run_my_agent.py` | `5555`, ou `--port` (le jeu graphique s'y connecte) |
| `training/rl_training.py` | `PORT_BASE` → `PORT_BASE + num_envs - 1` (5600-5607) |
| `evaluation/run_matchup.py` | `PORT_BASE + 200` (5800) |
| `evaluation/evaluate_model.py` | `--port-base` (6400) et suivants, un par worker |
| `evaluation/match_stats.py` | `--port` (6950) |

---

## 5. Structure du dossier `PythonAI`

```
PythonAI/
├── README.md
├── pyproject.toml                     ← Dépendances (référence) pour uv
├── uv.lock                            ← Versions exactes, identiques sur tous les OS
├── .python-version                    ← Python 3.12 pour uv
├── requirements_participant.txt       ← Pour pip / Databricks
│
├── bridge/
│   ├── datatypes.py                   ← Modèles Pydantic (GameState, AIAction…), constantes du jeu
│   ├── fixed.py, fixed_numba.py       ← Nombres en virgule fixe, identiques au jeu
│   ├── gym.py                         ← Client ZMQ (mode entraînement, JSON)
│   ├── binary.py                      ← Protocole binaire (entraînement rapide)
│   └── play.py                        ← Serveur ZMQ (mode match, jeu graphique)
│
├── core/env/
│   ├── automathon_env.py              ← Environnement Gymnasium d'exemple
│   ├── fast_env.py                    ← Même environnement, protocole binaire
│   ├── vec_env.py                     ← Un seul processus pour tous les Headless (env.backend=vec)
│   └── reward_shaping.py              ← Fonction de récompense (poids réglables)
│
├── agents/
│   ├── run_my_agent.py                ← Lanceur d'agent (mode match) + lancement du jeu
│   ├── dummy_bot/                     ← Immobile, tire tout droit
│   ├── rush_bot/                      ← Fonce sur l'adversaire en tirant (adversaire de référence)
│   └── my_rl_agent/                   ← Modèle d'agent RL, point de départ des équipes
│       ├── agent.py                   ← Joue votre modèle (model.zip, sinon training/rl_model.zip)
│       └── feature_extractor.py       ← Extracteur 112 dimensions (Numba, partagé partout)
│
├── training/
│   ├── utils.py                       ← Utilitaires cross-platform (OS, ports, binaires…)
│   ├── conf/                          ← Configs Hydra (hyperparamètres)
│   │   ├── rl_training.yaml
│   │   └── common.yaml                ← Réglages Headless / environnement partagés
│   ├── rl_training.py                 ← Exemple d'entraînement : PPO from scratch contre un adversaire
│   ├── export_agent.py                ← Export modèle (Databricks → PC)
│   ├── IMPORT_INSTRUCTIONS.md         ← Instructions embarquées dans l'export
│   ├── outputs/                       ← Config + logs de chaque run Hydra
│   └── tensorboard/                   ← Logs TensorBoard
│
└── evaluation/
    ├── run_matchup.py                 ← Duel entre deux agents
    ├── evaluate_model.py              ← Taux de victoire d'un modèle ou d'un dossier de checkpoints
    ├── match_stats.py                 ← Comment un agent gagne : dégâts par arme et par distance, durée, zone
    └── benchmark.py                   ← Entraînements sur plusieurs graines, pour comparer des réglages
```

---

## 6. Communication avec le jeu (`bridge/`)

### `bridge/gym.py` — Mode entraînement (Headless)

Socket ZMQ `REQ`. Python initie chaque échange et contrôle les deux vaisseaux.

```python
from bridge.gym import Gym
from bridge.datatypes import ResetOptions
gym = Gym(tcp_port="5555")
state = gym.reset()                                       # carte aléatoire, votre vaisseau à gauche
state = gym.reset(options=ResetOptions(Seed=42, MapDensity=0.3, SelfOnRight=True))
next_state = gym.step(self_action, enemy_action)          # une frame
next_state = gym.step(self_action, enemy_action, frames=4)  # 4 frames avec les mêmes actions
gym.end_training()
```

- **`ResetOptions`** : `Seed` (même graine = même carte, et la simulation est déterministe : mêmes actions = même
  match), `MapDensity` (0 = arène vide, 0,7 par défaut), `SelfOnRight` (départ à droite), `Rules` (règles du match,
  voir ci-dessous). Toutes facultatives.
- **`GameRules`** : règles du match, pour tester des variantes du jeu sans recompiler. Seules les règles renseignées
  changent, les autres gardent leur valeur par défaut :

| Règle | Défaut | Effet |
|---|---|---|
| `ShipMaxHealth` | 1 000 | PV d'un vaisseau en début de match |
| `BulletDamage` | 60 | dégâts d'une balle de mitrailleuse |
| `MissileDirectDamage` / `MissileAreaDamage` | 600 / 400 | dégâts d'un missile au vaisseau touché / en zone |
| `MissileGuided` | `true` | le missile tourne vers la visée actuelle du tireur (`false` : tout droit) |
| `ShieldLifespan` | 10 | durée de vie d'un bouclier, en secondes |
| `MaxActiveShields` | 0 | boucliers simultanés par vaisseau, le plus ancien disparaît (0 = sans limite) |
| `MatchDuration` | 0 | durée d'un match en secondes ; à la fin, le plus de PV gagne, PV égaux = nul (0 = sans limite) |
| `MissileArmingDistance` | 1 | distance qu'un missile doit parcourir avant d'être armé ; avant, un impact le fait disparaître sans effet |
| `ZoneDamagePerSecond` | 50 | PV perdus par seconde par un vaisseau dont le centre est hors de la zone sûre (0 = pas de zone) |
| `ZoneStart` | 10 | instant (s) où la zone commence à rétrécir |
| `ZoneShrinkDuration` | 30 | durée (s) du rétrécissement |
| `ZoneFinalScale` | 0 | taille finale de la zone, en fraction de l'arène (0 = elle se ferme complètement) |

  Le jeu graphique et les matchs officiels utilisent les règles par défaut, celles du tableau. Côté Python,
  `DEFAULT_RULES` (`bridge/datatypes.py`) les donne, et `rules.value("BulletDamage")` la valeur effective d'une règle
  (renseignée ou par défaut). Les règles ne sont pas transmises dans l'état : un agent qui en a besoin les connaît.
- **`frames`** (répétition d'action) : le Headless joue plusieurs frames avec les mêmes actions et ne renvoie que
  l'état final. Il s'arrête plus tôt si un vaisseau meurt. Moins d'allers-retours, donc beaucoup plus de frames de
  jeu par seconde.

### `bridge/binary.py` — Protocole binaire (entraînement rapide)

Mêmes échanges que `bridge/gym.py`, mais les steps et les états circulent en entiers bruts (valeurs Q16.16 des
`Fixed`) au lieu de JSON. Le format est décrit dans `game/AutomathonUnity/Assets/Core/AI/BinaryProtocol.cs`.

```python
from bridge.binary import BinaryGym, encode_state, to_game_state
gym = BinaryGym(tcp_port="5555")
raw = gym.reset(ResetOptions(Seed=42))            # RawState : tableaux NumPy d'entiers bruts
raw = gym.step(self_action, enemy_action, frames=4) # actions : AIAction ou 8 floats
state = to_game_state(raw)                         # GameState identique à celui du JSON
raw = encode_state(state)                          # et l'inverse : les mêmes octets que le Headless
```

`RawState` :
- `header` : les 12 entiers de l'en-tête (colonnes `H_*`) : version (`H_VERSION`), fin du match (`H_DONE`), résultat
  (`H_OUTCOME`, indice dans `OUTCOMES`), frame (`H_FRAME`), dégâts infligés et subis pendant le dernier step
  (`H_DAMAGE_DEALT`, `H_DAMAGE_TAKEN`), nombres de balles, missiles, boucliers et murs (`H_N_*`), demi-taille de la
  zone sûre (`H_ZONE_X`, `H_ZONE_Y`) ;
- `self_ship` et `enemy_ship` : 16 entiers chacun (colonnes `SHIP_X`, `SHIP_HP`, `SHIP_CD_MG`…), tout à zéro si le
  vaisseau est détruit ;
- une ligne par entité dans `bullets`, `missiles`, `shields` et `walls` (colonnes `B_*`, `M_*`, `S_*`, `W_*` ; un
  missile a `M_ARMED`, 1 s'il est armé).

Une valeur `Fixed` brute vaut `raw / 65536` unités Unity ; équipes et propriétaires : 0 Green, 1 Red, -1 aucun.

- `core/env/fast_env.py` : `FastAutomathonEnv`, même interface et mêmes options qu'`AutomathonEnv`, sur ce
  protocole. Observations, récompenses et fins d'épisode identiques à celles d'`AutomathonEnv`.
- `core/env/vec_env.py` : `AutomathonVecEnv` (`env.backend=vec`, la valeur par défaut), un environnement vectorisé
  où **un seul processus Python pilote tous les Headless**. Les requêtes partent vers tous les Headless, qui simulent
  en parallèle, puis une seule fonction Numba traite toutes les réponses (lecture, features, récompense, fin de
  match). Les adversaires y sont calculés dans leur version Numba (`agents/<bot>/fast.py`, voir section 9) ; un bot
  sans version Numba fonctionne, mais ralentit tout l'entraînement (`env.backend=binary` est alors plus rapide).
- Les bots adverses reçoivent un `LazyGameState` : les listes de balles, missiles, boucliers et murs ne sont
  construites que s'ils les lisent.

### `bridge/play.py` — Mode match (jeu graphique)

Socket ZMQ `REP`. Le jeu initie chaque échange et Python contrôle un seul vaisseau. Le jeu envoie un état une
frame sur 4 (15 Hz, au début de la frame) et attend la réponse au plus 500 ms ; l'action est gardée jusqu'à la
décision suivante. `agents/run_my_agent.py` s'en charge pour vous.

```python
from bridge.play import Play
server = Play(my_decision_function, tcp_port="5555")
while True:
    server.respond()
```

---

## 7. Types de données (`bridge/datatypes.py`)

Le jeu calcule tout en **virgule fixe Q16.16** (simulation déterministe, identique sur toutes les machines). Côté
Python, chaque grandeur physique est un `Fixed` (`bridge/fixed.py`) et chaque vecteur un `FixedVector2` (alias
`Vector2`) :

- ils s'utilisent comme des floats et des vecteurs 2D : `+`, `-`, `*`, `/`, comparaisons, `float(x)`,
  `v.length()`, `v.normalized()`, `v.dot(w)`, `v.angle()`… Les opérations donnent exactement les mêmes résultats
  que le jeu ;
- un `FixedVector2` se construit avec `FixedVector2(1, 0.5)`, ou avec un tuple `(1, 0.5)` partout où un vecteur
  est attendu ;
- **unités** : 1 = 1 unité Unity. L'arène fait 27,4 × 15,4 à l'intérieur de ses bords, centrée en (0, 0)
  (`ARENA_HALF_WIDTH`, `ARENA_HALF_HEIGHT`). Vitesses en unités par seconde, angles en radians ;
- pour des calculs en masse (features, réseau de neurones), convertir en floats (`float(v.X)`) : c'est beaucoup
  plus rapide que l'arithmétique en virgule fixe.

### `GameState`

```python
class GameState(BaseModel):
    SelfShip: ShipState | None        # Votre vaisseau (None si détruit)
    EnemyShip: ShipState | None       # Vaisseau adverse
    BulletStates: List[BulletState]
    MissileStates: List[MissileState]
    WallStates: List[WallState]
    ShieldStates: List[ShieldState]
    Done: bool                        # True dès la frame du coup fatal (le vaisseau détruit est alors absent : None)
    Outcome: str                      # "Ongoing", "Win", "Loss" ou "Draw" (les deux détruits), de votre point de vue
    Frame: int                        # frames jouées depuis le début du match
    DamageDealt: Fixed                # dégâts infligés depuis l'état précédent (le dernier step, ou dans le jeu
                                      # visuel la décision précédente : 4 frames), coup fatal compris
    DamageTaken: Fixed                # dégâts subis depuis l'état précédent (zone sûre comprise)
    ZoneHalfSize: FixedVector2        # demi-taille de la zone sûre, centrée en (0, 0) : toute l'arène avant 10 s,
                                      # (0, 0) une fois fermée (40 s)
```

### `ShipState` (état d'un vaisseau)

```python
class ShipState(BaseModel):
    Position: FixedVector2            # Position dans l'arène
    Width: Fixed                      # Dimensions du vaisseau
    Height: Fixed
    Radius: Fixed                     # Rayon de collision
    Rotation: Fixed                   # Orientation du corps (radians)
    Velocity: FixedVector2            # Vitesse en unités/seconde
    AimingDirection: FixedVector2     # Direction de visée (vecteur unitaire)
    Health: Fixed                     # PV (commence à MaxHealth)
    MaxHealth: Fixed                  # PV en début de match (1000 par défaut, voir GameRules)
    Team: str                         # "Green" (départ à gauche) ou "Red" (départ à droite)
    ShieldCooldownFramesLeft: int     # 0 = prêt
    MissileCooldownFramesLeft: int
    MachineGunCooldownFramesLeft: int
    DashCooldownFramesLeft: int
```

Balles, missiles et boucliers ont un `Owner` (équipe du tireur), à comparer avec `Team` : vos balles ne vous
touchent pas, vos missiles si. Un missile a aussi `Armed` : `False` tant qu'il n'a pas parcouru 1 unité.

### `AIAction`

```python
class AIAction(BaseModel):
    MovingDirection: FixedVector2  # Norme > 1 ramenée à 1 ; norme < 1 = déplacement plus lent
    AimingDirection: FixedVector2  # Normalisée par le jeu ; (0, 0) garde la visée précédente
    MachineGun: bool
    Missile: bool
    Shield: bool
    Dash: bool
```

> **Convention :** `FixedVector2(1, 0)` = direction droite, norme 1. Une capacité part dans la direction de la frame
> précédente : à la première frame d'une décision, la balle, le missile et le bouclier partent encore dans la visée
> de la décision précédente, et le dash dans sa direction de déplacement.

---

## 8. Environnement RL (`core/env/`)

L'environnement d'entraînement du kit est un **exemple** : observations (l'extracteur de features, section 9),
actions, récompense et fins d'épisode sont des choix du kit, à modifier ou à remplacer. Le jeu, lui, ne change pas :
le Headless simule les mêmes matchs que le jeu graphique (section 6).

### Espaces

| Espace | Dimensions | Plage |
|---|---|---|
| `observation_space` | 112 (`FEATURE_SIZE` de l'extracteur) | `[-1, 1]` |
| `action_space` | 8 : déplacement (2), visée (2), puis mitrailleuse, missile, bouclier, dash (pressés si > 0) | `[-1, 1]` |

### Terminaison d'épisode

| Condition | `terminated` | `truncated` |
|---|---|---|
| `Done == True` (victoire, défaite ou destruction simultanée) | ✅ | ❌ |
| `max_steps` frames jouées (défaut 4 200, soit 70 s ; un match finit avant) | ❌ | ✅ |
| Timeout TCP (crash du Headless) | ✅ | ❌ |

### Options de l'environnement

Section `env` de `training/conf/common.yaml` (ou arguments de `AutomathonEnv`) :

| Option | Défaut | Effet |
|---|---|---|
| `backend` | `vec` | `vec` : la version optimisée, un seul processus pour tous les Headless (`AutomathonVecEnv`). `json` : la version simple, un processus par environnement, sur des objets `GameState` (`AutomathonEnv`) ; elle accepte un extracteur Python quelconque. `binary` : un processus par environnement, protocole binaire (`FastAutomathonEnv`) ; plus rapide que `vec` quand l'adversaire n'a pas de version Numba. Mêmes observations et récompenses (section 14) |
| `frame_skip` | 4 | frames jouées par step avec la même action, pour l'agent **et** l'adversaire. 4 = conditions d'un match (décisions à 15 Hz) ; une autre valeur entraîne l'agent à un rythme qu'il n'aura pas en match |
| `map_density` | `null` | densité de murs (0 = arène vide, `null` = 0,7) |
| `random_side` | `true` | côté de départ de l'agent tiré au hasard à chaque épisode |
| `rules` | `{}` | règles du match (`GameRules`) pour tester une variante, ex. `+env.rules.MissileArmingDistance=3` ; `{}` = règles du jeu |
| `canonical_side` | `true` | parti à droite, l'agent voit et agit dans le repère tourné de 180° (voir l'extracteur) |

Débit et réglages de vitesse (nombre d'environnements, minibatchs) : voir la section 14.

Les cartes sont tirées avec le générateur aléatoire de l'environnement : avec `env.reset(seed=...)` (ou `seed=`
dans `rl_training.yaml`), la suite des cartes et des côtés est reproductible.
`reset(options={"map_seed": ..., "map_density": ..., "self_on_right": ...})` impose une carte précise.

### Récompense

Valeurs par défaut (section `reward` de `training/conf/rl_training.yaml`), à l'échelle d'une victoire = 10 :

```python
reward = -0.001 * frames         # time_penalty : pénalité de temps par frame jouée (force l'engagement)
reward += DamageDealt * 0.01     # damage_dealt_coef : dégâts infligés (coup fatal compris)
reward -= DamageTaken * 0.001    # pain_factor : dégâts subis
# En fin d'épisode (Outcome) :
reward += 10  # win_bonus : victoire
reward -= 10  # loss_penalty / draw_penalty : défaite, ou destruction simultanée
```

`DamageDealt` et `DamageTaken` comptent toutes les pertes de PV, zone sûre comprise : l'agent est pénalisé quand il
reste hors de la zone, et récompensé quand l'adversaire y perd des PV.

---

## 9. Agents (`agents/`)

| Agent | Rôle |
|---|---|
| `dummy_bot` | Reste immobile et tire tout droit |
| `rush_bot` | Fonce sur l'adversaire en déclenchant tout ce qui est rechargé : l'adversaire de référence |
| `my_rl_agent` | **Modèle d'agent RL**, point de départ des équipes : joue votre modèle PPO avec l'extracteur de features du kit. Il cherche le modèle dans `agents/my_rl_agent/model.zip`, puis dans `training/rl_model.zip` (là où l'entraînement l'écrit). Aucun modèle n'est fourni : entraînez le vôtre (section 10). |

### Interface obligatoire

Un agent est un dossier `agents/<nom>/` qui contient `agent.py` :

```python
from bridge.datatypes import GameState, AIAction

class ParticipantAgent:
    def __init__(self): pass

    def get_action(self, state: GameState) -> AIAction:
        return AIAction(...)
```

Le jeu attend la réponse au plus 500 ms : visez quelques millisecondes par décision. Tout ce que l'agent charge
(modèle, extracteur, données) doit être dans son dossier : c'est ce dossier que vous rendrez (section 15). Pour
`my_rl_agent`, copiez votre modèle en `agents/my_rl_agent/model.zip`.

### Version Numba d'un bot (`fast.py`)

Un bot peut fournir, à côté de son `agent.py`, une version compilée avec Numba dans `fast.py` : une fonction
`get_action_raw(self_ship, enemy_ship, bullets, missiles, shields, walls)` qui reçoit l'état binaire de son point de
vue (voir `bridge/binary.py`) et renvoie 5 entiers : déplacement x, y et visée x, y (valeurs `Fixed` brutes), puis
les boutons (1 mitrailleuse, 2 missile, 4 bouclier, 8 dash). L'environnement `vec` l'utilise automatiquement quand
ce bot est l'adversaire : environ 2 µs par action au lieu de 40 à 50 µs en Python.

Les deux versions doivent jouer **exactement** les mêmes actions : `bridge/fixed_numba.py` reproduit au bit près les
calculs en virgule fixe de `bridge/fixed.py`. `rush_bot` et `dummy_bot` ont leur `fast.py`, identique à leur
`agent.py` sur plus de 10 000 états testés.

### Extracteur de features (112 dimensions)

`agents/my_rl_agent/feature_extractor.py` — une seule implémentation, compilée avec
[Numba](https://numba.pydata.org/), utilisée partout : sur l'état binaire à l'entraînement
(`extract_features_raw`), et en match sur un `GameState` (`extract_features`, qui le convertit d'abord avec
`encode_state`). Les observations sont donc identiques au bit près à l'entraînement et en match.

| Groupe | Features |
|---|---|
| Votre vaisseau | 11 (pos, vit, PV, 4 recharges, visée) |
| Vaisseau adverse | 12 (existe, pos rel, vit, PV, visée, 4 recharges) |
| 2 missiles les plus proches | 14 (existe, pos rel, vit, à vous ou non, armé) |
| 5 balles adverses qui se rapprochent | 25 (existe, pos rel, vit) |
| 2 boucliers les plus proches | 18 (existe, pos rel, vit, PV, à vous ou non, orientation) |
| 4 murs les plus proches | 28 (existe, pos rel, taille, orientation) |
| Zone sûre | 4 (demi-largeur et demi-hauteur en fraction de l'arène, marge de votre vaisseau et de l'adversaire jusqu'à son bord, négative dehors) |

- Recharges : chacune normalisée par sa durée (1 = tout juste utilisée, 0 = prête). Vitesses : normalisées par la
  vitesse maximale de chaque type (vaisseau 7, balle 17, missile 9).
- Orientation d'un mur ou d'un bouclier : (cos 2θ, sin 2θ), identique à 180° près comme le segment lui-même.
- « À vous ou non » : comparaison de `Owner` (balles, missiles, boucliers) avec `Team` de votre vaisseau.
- **Repère retourné** (`flip=True`) : un agent parti à droite (équipe `Red`) voit tout tourné de 180°, comme s'il
  était parti à gauche. Les cartes étant symétriques par rapport au centre, l'agent n'a qu'un côté à apprendre.
  L'environnement (`env.canonical_side`, activé par défaut) et `my_rl_agent` retournent aussi ses actions
  (`flip_action`). Avec un extracteur personnalisé sans paramètre `flip`, rien n'est retourné.

#### Écrire son extracteur de features

Modifiez la fonction `_features` de `feature_extractor.py` (et `FEATURE_SIZE` si la taille change) : elle sert alors
à l'entraînement, avec tous les backends, et en match. Elle reçoit l'état binaire complet, `header`, `self_ship`,
`enemy_ship`, `bullets`, `missiles`, `shields`, `walls` (colonnes décrites section 6), et `flip`, et renvoie un
vecteur `float32`. L'en-tête donne la frame, les dégâts du dernier step et la zone : par exemple, le temps écoulé
s'ajoute en une ligne, `out[i] = min(header[H_FRAME] / 3600.0, 1.0)` (avec `H_FRAME` importé de `bridge.binary`, et
`FEATURE_SIZE` augmenté de 1).

- **Numba compile un sous-ensemble de Python** : nombres, tableaux NumPy, boucles, `if`, fonctions de `math`, et
  d'autres fonctions décorées `@njit`. Pas d'objets `GameState`, de dictionnaires, ni de listes de types mélangés.
  Les constantes de colonnes (`SHIP_X`, `B_VX`…) et les constantes globales du module sont utilisables.
- **Débogage** : avec la variable d'environnement `NUMBA_DISABLE_JIT=1`, le code s'exécute en Python normal
  (lentement), avec les messages d'erreur habituels.
- **Compilation** : la première exécution compile la fonction (quelques secondes), puis Numba la garde en cache
  (`__pycache__`). Un agent appelle `warmup()` à son démarrage pour ne pas compiler pendant la première frame du match.
- **Vitesse** : environ 3 µs par état pour l'extracteur par défaut. Une boucle simple est plus rapide sous Numba que
  des opérations NumPy sur de petits tableaux.
- Pour une fonction impossible à compiler avec Numba, passez-la en `extractor_fn` à `AutomathonEnv`
  (`env.backend=json`, plus lent).
- Changer les features rend incompatibles les modèles entraînés avec les anciennes.

---

## 10. Entraînement (`training/`)

`training/rl_training.py` est un **exemple** d'entraînement : un PPO (stable-baselines3) from scratch, contre un seul
adversaire (`rush_bot` par défaut), sur l'environnement de la section 8. Rien n'oblige à le garder : algorithme,
récompense, features, adversaires, données… la façon d'entraîner votre agent vous appartient. Seul compte l'agent que
vous rendez (section 9).

```bash
python training/rl_training.py
```

Avec les réglages par défaut (8 Headless en parallèle, 3 000 000 de steps), il atteint environ 43 % de victoires
contre `rush_bot` (41 à 44 % selon la graine, mesuré avec `evaluation/benchmark.py` sur 3 graines) : de quoi vérifier
que tout fonctionne, pas un optimum.

- Checkpoints automatiques dans `save.checkpoint_dir` / `AUTOMATHON_CHECKPOINT_DIR` (défaut `training/checkpoints/`)
- Modèle final : `training/rl_model.zip` (`rl_model_interrupted.zip` sur Ctrl+C), joué par `my_rl_agent` (copiez-le
  en `agents/my_rl_agent/model.zip` pour rendre l'agent)
- Suivi : `tensorboard --logdir training/tensorboard` (taux de victoire, défaites, nuls sur les 100 derniers épisodes : `rollout/win_rate`…)

### `training/utils.py` — Utilitaires cross-platform

- `get_unity_headless_path()` — détecte l'OS ; `chmod +x` sur Linux/macOS, retrait de la quarantaine sur macOS
- `launch_headless(port)` — lance un Headless et attend qu'il écoute (au plus `AUTOMATHON_STARTUP_WAIT` s)
- `get_game_path()` / `launch_game()` — trouve et lance le jeu graphique sur les 3 OS
- `get_port_base()`, `get_num_envs()`, `get_checkpoint_dir()` — lisent les variables d'environnement

### Hyperparamètres avec Hydra

`rl_training.py` lit sa configuration dans `training/conf/` avec [Hydra](https://hydra.cc). Toute valeur se
surcharge en ligne de commande, sans toucher au code :

```bash
python training/rl_training.py --cfg job                       # affiche la config finale sans lancer
python training/rl_training.py total_timesteps=10000000 ppo.ent_coef=0.01
python training/rl_training.py opponent=dummy_bot               # autre adversaire (un dossier de agents/)
python training/rl_training.py reward.pain_factor=0.005 unity.num_envs=16 ppo.n_steps=512
python training/rl_training.py -m ppo.learning_rate=1e-4,3e-4  # multirun : un entraînement par valeur
python training/rl_training.py init.model=checkpoints/rl_model_500000_steps.zip  # reprise d'un checkpoint
```

| Fichier | Contenu |
|---|---|
| `rl_training.yaml` | `opponent`, `total_timesteps`, `unity.num_envs`, `ppo.*` (lr, n_steps, batch_size, gamma, clip_range, ent_coef…), `policy_kwargs`, `reward.*`, `init.model` (reprise d'un modèle), `save.*` (chemins, checkpoints) |
| `common.yaml` | `unity.*` (port_base, startup_wait : `null` = variable d'environnement), `env.*` (backend, max_steps, frame_skip, random_side, rules, tcp_timeout) |

Points à connaître :
- **Traçabilité** : chaque run écrit sa config complète et ses overrides dans `training/outputs/<script>/<date>/.hydra/`.
- **Chemins** : relatifs à `PythonAI/training/` ; un chemin absolu (ex : `/Volumes/...`) est utilisé tel quel.
- **Reprise d'un modèle** (`init.model=<chemin>`) : les valeurs `ppo.*` remplacent celles enregistrées dans le
  modèle, mais l'**architecture** du réseau reste la sienne (`policy_kwargs` ne sert qu'en from scratch).
- **Adversaire** : `opponent` est un dossier de `agents/`, le même pour tous les environnements pendant tout
  l'entraînement. Pour un adversaire qui n'est pas un dossier (une copie de votre modèle, par exemple), voir `train()`
  dans `rl_training.py` : `env.env_method("set_opponent", objet)` accepte tout objet qui a une méthode
  `get_action(state)`.
- **Depuis un notebook** (sans passer par la ligne de commande) :

```python
import sys; sys.path.insert(0, "<chemin>/PythonAI")
from hydra import initialize_config_dir, compose
from training.rl_training import train

with initialize_config_dir(config_dir="<chemin>/PythonAI/training/conf", version_base="1.3"):
    cfg = compose("rl_training", overrides=["total_timesteps=500000", "save.checkpoint_dir=/Volumes/.../mon_equipe"])
train(cfg)
```

### Export (Databricks → PC)

`python training/export_agent.py [nom_agent]` crée `mon_agent_TIMESTAMP.zip` avec le modèle et le code de l'agent,
et détecte Databricks ou Colab pour le téléchargement.

---

## 11. Évaluation (`evaluation/`)

### Duel

```bash
python evaluation/run_matchup.py                                   # rush_bot contre dummy_bot
python evaluation/run_matchup.py mon_equipe rush_bot --games 51
```

Les duels se jouent sur le Headless dans les conditions d'un match : les deux agents décident aux mêmes frames,
une sur 4. Mêmes résultats que dans le jeu graphique, à la frame près.

### Évaluer un modèle entraîné

```bash
python evaluation/evaluate_model.py training/rl_model.zip --opponent rush_bot
python evaluation/evaluate_model.py training/checkpoints/ --maps 50 --workers 4 --json eval.json   # tous les checkpoints
python evaluation/evaluate_model.py random                                                      # politique aléatoire
```

Politique déterministe, sur un jeu fixe de cartes (graines 1 000 000 et suivantes) joué des deux côtés : les
résultats de modèles différents sont directement comparables. Affiche le taux de victoire avec son intervalle de
confiance à 95 %, et le détail défaites / nuls / temps écoulé. `--frame-skip` vaut 4 par défaut :
les conditions exactes d'un match. `--backend binary` accélère l'évaluation (mêmes résultats avec l'extracteur par
défaut).

### Comprendre comment un agent gagne

```bash
python evaluation/match_stats.py model:training/rl_model.zip rush_bot --games 20
python evaluation/match_stats.py mon_equipe rush_bot --json stats.json
```

Joue des matchs frame par frame et attribue chaque perte de PV à une arme (balles, missile, zone sûre), avec la
distance entre les vaisseaux. Affiche les issues, la durée moyenne, le temps passé à moins de 3 unités, la part des
dégâts par arme et par distance, l'usage des capacités et les dégâts de zone subis. Un agent est un dossier de
`agents/`, ou `model:<chemin>` pour un modèle PPO joué avec l'extracteur du kit.

### Comparer des réglages

```bash
python evaluation/benchmark.py mon_essai --override ppo.ent_coef=0.01
```

Entraîne un PPO from scratch sur plusieurs graines (`--seeds`, 0 1 2 par défaut) avec un protocole fixe (les
valeurs par défaut du kit) et les options données, puis évalue tous les checkpoints sur les mêmes cartes. Résultats
dans `build/benchmarks/<nom>/` ; le score est la moyenne, sur les graines, du taux de victoire des 4 derniers
checkpoints. Une graine seule ne suffit pas pour comparer deux réglages : l'écart entre graines peut dépasser 15 points.

---

## 12. Écrire et tester son agent

### Créer votre agent

```python
# agents/mon_equipe/agent.py
from bridge.datatypes import GameState, AIAction

class ParticipantAgent:
    def get_action(self, state: GameState) -> AIAction:
        if state.SelfShip and state.EnemyShip:
            to_enemy = state.EnemyShip.Position - state.SelfShip.Position
            return AIAction(
                MovingDirection=to_enemy,
                AimingDirection=to_enemy,
                MachineGun=True,
            )
        return AIAction()
```

Copier `agents/rush_bot/` est un bon point de départ.

### Tester en visuel

```bash
cd PythonAI
uv run python agents/run_my_agent.py mon_equipe --launch-game   # lance l'agent (port 5555) et le jeu
uv run python agents/run_my_agent.py mon_equipe --port 5557     # autre port, jeu à lancer soi-même
uv run python agents/run_my_agent.py                            # menu interactif (agent puis port)
```

Dans le menu du jeu, pour chaque vaisseau piloté par une IA :
1. cliquer sur **PLAYER** (en haut du panneau du vaisseau) pour le passer en **AI** ;
2. cliquer dans le champ **AI TCP PORT**, saisir le port de l'agent (ex. `5555`) puis **Entrée** :
   le jeu vérifie que l'agent répond (« CONNECTION SUCCESSFUL ! ») ; l'agent doit donc être lancé **avant** ;
3. lancer la partie (**PLAY**, ou **Entrée** / **+**).

**Regarder deux agents s'affronter** : un agent par terminal, chacun sur son port, un seul `--launch-game` :

```bash
uv run python agents/run_my_agent.py mon_equipe --launch-game   # terminal 1 : port 5555 + jeu
uv run python agents/run_my_agent.py rush_bot --port 5556       # terminal 2 : port 5556
# Dans le jeu : vaisseau de gauche en AI sur 5555, vaisseau de droite en AI sur 5556, puis PLAY.
```

Un vaisseau peut aussi rester en **PLAYER** (clavier ou manette) pour jouer contre son agent.

Sans uv, remplacer `uv run python` par `python` (dans le venv activé).

---

## 13. Constantes du jeu

| Constante | Valeur | Unité |
|---|---|---|
| Frames par seconde | 60 | frames/s |
| Décisions d'un agent | 15 (une frame sur 4) | par seconde |
| PV initiaux | 1 000 | PV |
| Dégâts : balle / missile au contact / missile en zone | 60 / 600 / 400 | PV |
| Distance d'armement d'un missile | 1 | unités |
| Zone sûre : début du rétrécissement, fermeture, dégâts hors zone | 10 s, 40 s, 50 PV/s | |
| Vitesse max des vaisseaux | 7 | unités/s |
| Rayon d'un vaisseau | 0,419 | unités |
| Vitesse des balles | 17 | unités/s |
| Vitesse des missiles | 9 | unités/s |
| Bords de l'arène (face intérieure) | x = ±13,70, y = ±7,70 | unités |
| Points d'apparition | (±10, 0) | unités |
| Durée maximale d'un épisode d'entraînement (défaut) | 4 200 | frames (70 s) |

---

## 14. Performance de l'entraînement

Un **step** d'entraînement est une décision de l'agent, soit 4 frames simulées (15 décisions par seconde de jeu).
L'environnement d'exemple existe en deux versions, qui donnent exactement les mêmes observations, récompenses et
matchs (vérifié au bit près) :

- **la version simple**, `env.backend=json` (`AutomathonEnv`, `core/env/automathon_env.py`) : un processus Python
  par environnement (`SubprocVecEnv`), qui échange avec son Headless en JSON et travaille sur des objets `GameState`.
  C'est la plus lisible, et elle accepte n'importe quel code Python (extracteur, récompense, adversaire) ;
- **la version optimisée**, `env.backend=vec` (`AutomathonVecEnv`, `core/env/vec_env.py`, par défaut) : un seul
  processus pilote tous les Headless avec un protocole binaire, et les calculs de chaque step sont compilés avec
  Numba. À nombre d'environnements égal, elle est environ deux fois plus rapide.

`env.backend=binary` (`FastAutomathonEnv`) garde un processus par environnement, avec le protocole binaire. C'est le
bon choix quand l'adversaire n'a pas de version Numba (un bot Python à vous, une copie de votre agent…) : `vec`
calcule alors cet adversaire état par état, dans son unique processus.

### Mesures

Débit d'un entraînement PPO complet (collecte et mise à jour du réseau ; petit réseau, rollouts de 8 192 steps,
`torch_threads=2`), contre `rush_bot`, sur un portable à 14 cœurs. Les valeurs dépendent de la machine ; l'écart
entre les versions, beaucoup moins.

| Configuration | Steps/s |
|---|---|
| `json`, 8 environnements | 2 100 – 2 400 |
| `vec`, 8 environnements (réglage par défaut) | 4 240 – 4 850 |
| `vec`, 32 environnements (`ppo.n_steps=256`) | 7 180 |
| `vec`, 32 environnements, `ppo.batch_size=2048` | 9 410 |

### Où passe le temps

Profil de la version optimisée, à 32 environnements :

- la **mise à jour PPO** prend 30 % du temps total, et la collecte s'arrête pendant ce temps ;
- dans la **collecte** : attente des Headless 38 %, réseau (inférence PyTorch) 20 %, envoi des actions (calcul des
  adversaires, encodage) 13 %, traitement des réponses (Numba) 10 %, surcoût de stable-baselines3 10 %, resets 8 %.

La simulation elle-même coûte environ 0,43 ms de CPU par step : ce qui limite le débit, c'est le processus Python
qui orchestre tout.

### Ce qui rend la version optimisée rapide

Aucune de ces techniques ne change la simulation : chacune a été vérifiée au bit près (mêmes observations, mêmes
récompenses, mêmes matchs).

1. **Décisions à 15 Hz et répétition d'action** (les deux versions). Le Headless joue `Frames` frames avec les mêmes
   actions et ne renvoie que l'état final (il s'arrête plus tôt si un vaisseau meurt) : un aller-retour pour 4
   frames au lieu de 4. C'est aussi la règle des matchs (`DECISION_INTERVAL`), donc l'entraînement reste dans les
   conditions d'un match.
2. **Protocole binaire** (`bridge/binary.py`, côté jeu `Core/AI/BinaryProtocol.cs`). Les états et les actions
   circulent en entiers bruts (les valeurs Q16.16 des `Fixed`) au lieu de JSON : plus de texte à produire et à
   relire, plus de validation pydantic. La lecture d'un état est deux fois plus rapide ; sur un entraînement complet,
   le gain est de 25 à 35 %, parce que le reste de la chaîne pèse aussi.
3. **Un seul processus pour tous les Headless** (`core/env/vec_env.py`). L'approche classique (`SubprocVecEnv`)
   lance un processus Python par environnement et copie chaque observation entre processus. Ici, un seul processus
   envoie les actions à tous les Headless, qui simulent en parallèle, puis traite toutes les réponses d'un coup, dans
   **une seule fonction Numba** : lecture de l'état, observation, récompense, fin de match. Pendant que Python
   travaille, les Headless simulent : les deux se recouvrent.
4. **Numba dans la boucle chaude.** [Numba](https://numba.pydata.org/) compile du Python numérique en code machine :
   - l'extracteur de features prend environ 3 µs par état, contre 38 µs en Python sur un `GameState` et 70 µs en
     NumPy sur l'état binaire ; une boucle simple bat NumPy sur de petits tableaux, car chaque appel NumPy a un coût
     fixe ;
   - les bots adverses ont une version compilée (`agents/<bot>/fast.py`, section 9) : environ 2 µs par action
     contre 40 à 50 µs en Python ;
   - `bridge/fixed_numba.py` reproduit au bit près la virgule fixe du jeu, pour que la version compilée d'un bot joue
     exactement les mêmes actions que sa version Python.
5. **État paresseux pour les bots Python** (`LazyGameState`) : les listes de balles, missiles, boucliers et murs ne
   sont construites que si le bot les lit.
6. **Simulateur allégé** (C#, sans changer un seul match) : la génération de carte ne teste la connectivité qu'autour
   de chaque mur (un reset prend environ 0,44 ms) ; le moteur physique écarte les paires d'objets éloignés par un
   test de cercles englobants avant les tests de collision exacts ; côté Python, la lecture des `Fixed` a un chemin
   rapide.
7. **Deux threads PyTorch** (`torch_threads: 2`). Par défaut, PyTorch prend autant de threads que de cœurs : pour un
   réseau de cette taille, ils ne font que disputer les cœurs aux Headless.
8. **Plus d'environnements que de cœurs.** Un Headless ne travaille que pendant une partie de chaque aller-retour : le
   reste du temps, il attend Python. Avec `vec`, 2 à 3 environnements par cœur gardent les cœurs occupés (voir les
   mesures).
9. **Taille des rollouts et des minibatchs.** Quand vous changez le nombre d'environnements, gardez la taille des
   rollouts constante (`ppo.n_steps` × nombre d'environnements = 8 192 par défaut : `ppo.n_steps=256` pour 32
   environnements). Au-delà de 16 environnements, la mise à jour PPO pèse autant que la collecte : des minibatchs
   de 2 048 l'accélèrent, mais font moins de pas de gradient par rollout. Le kit garde des minibatchs de 1 024.

### Conseils pour vos propres modifications

- **Gardez votre extracteur compilable par Numba** : sinon il faut la version `json`, nettement plus lente.
- **Écrivez un `fast.py`** pour tout bot que vous utilisez comme adversaire d'entraînement, et vérifiez qu'il joue les
  mêmes actions que son `agent.py`.
- **Rien de coûteux en Python à chaque step** : callbacks, logs, calculs sur des `GameState`. Faites-le tous les N
  steps, ou dans la fonction Numba.
- **Mesurez** : le débit s'affiche pendant l'entraînement (`time/fps`) ; la qualité se compare avec
  `evaluation/benchmark.py` (section 11), sur plusieurs graines.
- **Une machine à beaucoup de cœurs peut faire tourner plusieurs runs** : un run est limité par son unique processus
  Python, donc plusieurs runs indépendants (graines ou réglages différents) occupent mieux la machine.
- **Suivez plus que le taux de victoire** : dégâts infligés, durée des matchs (`evaluation/match_stats.py`). Un agent
  peut stagner longtemps avant de progresser.
- **Pour déboguer** du code Numba : `NUMBA_DISABLE_JIT=1`.

Sur Databricks, le notebook mesure le débit de la machine (étape « Mesure du débit ») et règle le nombre
d'environnements selon ses cœurs.

---

## 15. Règles du hackathon

> **Section à compléter par les organisateurs.** Les points marqués **À COMPLÉTER** ne sont pas encore fixés.

Ce que le kit impose déjà :

- **Un agent est un dossier** `agents/<nom>/` avec un `agent.py` qui définit `ParticipantAgent` et sa méthode
  `get_action(state: GameState) -> AIAction` (section 9). Tout ce que l'agent charge (modèle, extracteur, données)
  est dans ce dossier.
- **Le jeu et ses règles sont ceux du kit** : règles par défaut (`GameRules`, section 6), décisions à 15 Hz, le jeu
  attend la réponse d'un agent au plus 500 ms. Les matchs officiels se jouent sur le jeu fourni, non modifié.
- Les deux vaisseaux reçoivent le même état complet ; chaque agent en tire ses propres observations.

À fixer :

- **Format et date de rendu** : **À COMPLÉTER** (dossier d'agent zippé ? dépôt ? nom du dossier ?).
- **Temps de décision** : **À COMPLÉTER** (limite moyenne et maximale par décision, machine des matchs officiels).
- **Dépendances autorisées** : **À COMPLÉTER** (les bibliothèques du kit : numpy, numba, torch, stable-baselines3,
  pydantic… ; autres ?).
- **Tracks** : **À COMPLÉTER** (par exemple « RL pur » et « tout permis » : ce qui est autorisé dans chacune,
  imitation d'une heuristique, règles écrites à la main).
- **Tournoi** : **À COMPLÉTER** (nombre de matchs, cartes, côtés, départage des égalités).

---

## 16. Dépannage

### `FileNotFoundError: Binaire Unity Headless introuvable` / `Jeu graphique introuvable`
Garder `Headless/` et le jeu à la racine du kit, à côté de `PythonAI/` (section 2).
Ou définir `AUTOMATHON_HEADLESS_PATH` / `AUTOMATHON_GAME_PATH`.

### `RuntimeError: Le port XXXX est deja utilise`
Un agent `run_my_agent.py` tourne sur ce port, ou un autre programme l'occupe. Changer `AUTOMATHON_PORT_BASE`, ou
arrêter le programme en question.

Les Headless lancés par les scripts (`launch_headless()`) s'arrêtent avec le processus Python qui les a lancés, même
s'il plante ou est tué : Job Object sous Windows, `PR_SET_PDEATHSIG` sous Linux. Sous macOS, ils sont arrêtés à la
sortie de Python, sur exception ou Ctrl+C, mais **pas** après un `kill -9` ou un arrêt forcé. Pour arrêter des
Headless orphelins :
- Windows : `taskkill /F /IM Headless.exe`
- Linux / macOS : `pkill -f Headless`

### `RuntimeError: Le Headless (port XXXX) n'ecoute pas apres 15s`
Machine lente : augmenter `AUTOMATHON_STARTUP_WAIT` (ex : `30`).

### `TimeoutError: Make sure the Headless version is running...`
Le Headless ne répond plus : vérifier qu'il n'a pas planté.

### macOS : « AutomathonGame.app est endommagé » ou « impossible de vérifier le développeur »
Retirer la quarantaine : `xattr -dr com.apple.quarantine AutomathonGame.app Headless/Headless`, puis relancer. Sur
Apple Silicon, si le Headless est tué au lancement (`killed: 9`) : `codesign --force --sign - Headless/Headless`.

### macOS Intel : `Veuillez installer stable-baselines3 et torch`
Normal : `torch` n'existe plus pour Mac Intel. Écrire des agents à la main, entraîner sur Databricks.

### Conflits de ports sur Databricks
Définir `AUTOMATHON_PORT_BASE` (ou `unity.port_base=...` en override Hydra) avec une valeur différente par notebook.

### Checkpoint perdu après un arrêt de Databricks
Vérifier `save.checkpoint_dir` / `AUTOMATHON_CHECKPOINT_DIR` : sans stockage persistant (un Volume), les checkpoints
sont perdus au redémarrage. Le notebook du kit les recopie dans le Volume.

### Modèle entraîné sur Linux qui ne charge pas sur Windows
Vérifier que les versions de `stable-baselines3` et `torch` sont identiques des deux côtés (`uv sync` le garantit).

---

*Documentation de la version 1.0.0 du kit — 17 novembre 2026*
