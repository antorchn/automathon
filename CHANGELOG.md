# Changelog

## Arène en ligne V0 (24 novembre 2026)

Implémentation de l'arène en ligne Automathon (`Spec — Arène Automathon en ligne.md`), publiée dans le dépôt des participants ([antorchn/automathon](https://github.com/antorchn/automathon)), à côté du kit :
- **Site web (`arene/`)** : API et 7 pages (Classement Elo Bradley-Terry, fiche Agent, Envoyer, Mon compte, lecteur Replay WebGL, Règles, Admin), connexion GitHub OAuth (et `AUTH_DEV_MODE` en local HTTP), base SQLite avec migrations automatiques et couche stockage.
- **Worker & arbitrage (`worker/`)** : exécution des matchs sur Headless 1.0.0 avec deux agents isolés en conteneurs Docker, mesure CPU par cgroups v2, file prioritaire (validation, placement, affinage) et baux atomiques.
- **Hôte isolé (`agent_host/`)** : image `automathon-agent:1.0.0` (Python 3.12, dépendances du kit 1.0.0, conteneur sans réseau en lecture seule).
- **Intégration au site de l'association** : l'arène se sert sous un sous-chemin (`/automathon-rlcs`) derrière le reverse proxy du site ; `scripts/configure_public.sh` passe la VM en public en une commande ; `INTEGRATION_SITE.md` décrit la règle du proxy (nginx, Caddy, Apache). Adresse réelle des visiteurs derrière Docker et les proxys (`TRUSTED_PROXY` avec réseaux, `X-Forwarded-For` lu par la droite).
- **Agents invités** : un administrateur ajoute des agents sans compte GitHub, sous le nom de leur équipe (page Admin, ou `python -m arene.import_agents` pour un dossier de zips), sans cooldown ni limite de file.
- **Déploiement (`docker-compose.yml`, `scripts/`, `INSTALL.md`)** : téléchargement vérifié des sources v1.0.0, préparation VM Debian 13 (`setup_vm.sh`), script de déploiement (`deploy.sh`), sauvegarde à chaud (`backup.sh`) et test de bout en bout (`e2e_test.py`).

## 1.0.0 (17 novembre 2026)

Version des participants du hackathon : les règles du jeu sont figées (variante « v6 », choisie après des
entraînements comparés des meilleurs agents sur plusieurs variantes).

### Règles du jeu
- **Missile armé après 1 unité de vol** (`MissileArmingDistance`) : un missile qui touche quoi que ce soit avant
  disparaît sans aucun effet. Le missile à bout portant, qui touchait avant que la cible puisse réagir et décidait la
  plupart des matchs entre bons agents, ne marche plus à moins d'environ 3 unités.
- **Balles à 60 dégâts** au lieu de 100 (`BulletDamage`).
- **Zone sûre qui se referme** (`ZoneDamagePerSecond`, `ZoneStart`, `ZoneShrinkDuration`, `ZoneFinalScale`) : elle
  couvre toute l'arène jusqu'à 10 s, rétrécit linéairement et se ferme complètement à 40 s ; un vaisseau dont le
  centre est dehors perd 50 PV/s. Un match se termine donc toujours, en un peu plus de 60 s au plus : après 40 s, le
  vaisseau qui a le plus de PV gagne. Pas de limite de temps (`MatchDuration` reste à 0).
- Les règles par défaut du jeu (`GameRules.cs`) sont ces règles ; côté Python, `DEFAULT_RULES` et
  `GameRules.value()` (`bridge/datatypes.py`). Le Headless d'entraînement peut toujours tester des variantes
  (`ResetOptions.Rules`, `env.rules`, `--rules`).

### Ajouté
- État du jeu : demi-taille de la zone sûre (`GameState.ZoneHalfSize`) et état armé des missiles
  (`MissileState.Armed`). Protocole binaire version 2 : en-tête de 12 entiers (zone aux colonnes 10 et 11), missile
  de 8 valeurs (`M_ARMED`). Colonnes de l'en-tête nommées (`H_FRAME`, `H_DAMAGE_DEALT`, `H_ZONE_X`…) et
  `RawState.header`.
- Extracteur de features v3 (112 valeurs au lieu de 106) : l'état armé de chaque missile et un bloc de 4 valeurs pour
  la zone sûre (sa taille, la marge de chaque vaisseau jusqu'à son bord). Il reçoit **tout l'en-tête de l'état**
  (`_features(header, self_ship, …)`) : frame, dégâts du dernier step, zone, sans modifier l'environnement. Les
  modèles entraînés avec la 0.5.0 ne sont plus compatibles.
- `evaluation/match_stats.py` : comment un agent gagne. Matchs joués frame par frame, chaque perte de PV attribuée à
  une arme (balles, missile, zone) avec la distance entre les vaisseaux ; issues, durée, temps à bout portant, usage
  des capacités.
- Jeu graphique :
  - l'extérieur de la zone sûre est teinté de rouge, et son bord dessiné en rouge ;
  - un missile non armé est dessiné petit et gris-bleu, et reprend sa couleur jaune en grossissant au moment où il
    s'arme ; un missile qui touche trop tôt s'éteint dans un petit nuage ;
  - **explosion de lumière en fin de match**, reprise de CStudio (`feature/light-explosion-shader`, pro2p) : ralenti,
    explosion qui part de l'épave et couvre l'écran, puis écran de fin qui met en avant le vaisseau gagnant ; un nul
    a la même fin, partie d'entre les deux vaisseaux, puis « DRAW ! » ;
  - règles lues dans `rules.json` à côté du jeu, ou dans le fichier nommé par `AUTOMATHON_RULES`, pour regarder une
    variante (sans fichier : les règles du jeu).
- Jeu web (WebGL2) pour le site et l'événement du hackathon, qui rejoue les matchs du tournoi et fait jouer un humain
  contre un agent : outil des organisateurs, absent de ce kit. Côté kit : `Headless --replay fichier.json` rejoue un
  match enregistré (graine, règles, actions) et vérifie qu'il finit comme à l'enregistrement, et le jeu de bureau en
  rejoue un avec `AutomathonGame -replay fichier.json`. Le jeu de bureau accepte aussi une adresse d'agent
  `ws://` / `wss://` (WebSocket) à la place d'un port. Le jeu web se pilote depuis la page qui l'héberge
  (`Game/Features/Web/WebBridge.cs`) : événements de match sur `window` (`automathon:ready`, `matchstart`,
  `matchend`, `screencovered`, `sequenceend`, `error`), pause, vitesse, son, fin sans l'écran de fin du jeu, matchs en
  direct ; il refuse un replay d'une autre version que la sienne (`MatchReplay.GameVersion`).
- Dossier `databricks/` : guide des participants (`guide_participants_automathon.html`, à ouvrir dans un navigateur)
  et notebook de démarrage de l'Arène (`automathon_demarrage.ipynb`), en plus de `Automathon_Databricks.ipynb`. Tout
  le dossier est dans les kits tout-en-un.
- Kit `Windows-ARM64` (PC Windows 11 à processeur ARM) : jeu et Headless natifs ARM64 (`tools/build.py game
  --target windows-arm64`, `headless --rid win-arm64`). PyTorch n'étant pas publié sur PyPI pour Windows ARM, le
  kit Python s'y installe avec un Python x64, exécuté en émulation (`uv sync --python cpython-3.12-windows-x86_64-none`).
- Documentation : l'environnement et le script d'entraînement du kit sont présentés comme un exemple, que les
  équipes peuvent modifier ou remplacer ; section « Performance de l'entraînement » (version simple et version
  optimisée de l'environnement, mesures, profil, conseils) et section « Règles du hackathon » (à compléter par les
  organisateurs) dans `PythonAI/README.md`.

### Modifié
- **Les vaisseaux s'appellent des vaisseaux dans l'API** (la version pilote du jeu avait des tanks) :
  `GameState.SelfShip` et `EnemyShip`, `ShipState`, règle `ShipMaxHealth` ; dans le protocole binaire,
  `RawState.self_ship` et `enemy_ship`, colonnes `SHIP_*` et `SHIP_SIZE` ; paramètres `self_ship` et `enemy_ship` de
  l'extracteur et des `fast.py`. Les agents écrits pour la 0.5.0 s'adaptent par un simple renommage. Simulation,
  observations et récompenses inchangées au bit près.
- `neural_expert_bot` devient `my_rl_agent`, le **modèle d'agent RL** des équipes : il joue leur modèle PPO, cherché
  dans `agents/my_rl_agent/model.zip` (le dossier de l'agent se suffit alors à lui-même), puis dans
  `training/rl_model.zip`. Aucun modèle entraîné n'est fourni.
- Durée maximale d'un épisode d'entraînement et d'un match d'évaluation : 4 200 frames (70 s) au lieu de 3 600, car un
  match peut durer un peu plus de 60 s.
- Fin de match du jeu graphique annoncée une fois la frame entièrement simulée : deux vaisseaux détruits dans la même
  frame donnent bien un nul, et un vaisseau détruit après la fin ne change plus le résultat. Simulation inchangée
  (mêmes matchs sur le Headless).
- Notebook Databricks réécrit : choix du kit selon le processeur (`Linux-ARM-databricks` ou `Linux-x86-64`), nombre
  d'environnements selon le nombre de cœurs (2 par cœur, de 8 à 32), caractéristiques de la machine, test rapide,
  mesure du débit, entraînement aux réglages par défaut, évaluation, agent rendu (`mes_agents/my_rl_agent/` avec son
  `model.zip`), reprise.
- Réglages par défaut de l'entraînement mesurés sur ces règles : environ 43 % de victoires contre `rush_bot` en 3 M
  de steps (41 à 44 % selon la graine), contre 65 % sous les règles de la 0.5.0. Ils sont présentés comme un point de
  départ, plus comme le meilleur réglage.
- `tools/smoke_test.py` tolère l'arrondi JSON des dégâts cumulés sur un match.
- Jeu graphique : il n'est plus figé pendant qu'il attend la réponse d'un agent, il continue de s'afficher et ne joue
  la frame qu'une fois l'action reçue (ou après le délai : 500 ms en ZeroMQ, 5 s en WebSocket, l'agent garde alors son
  action). Les agents décident toujours aux frames 0, 4, 8… sur l'état de leur frame de décision : mêmes matchs.
  `AIInputProvider` passe par un `IAgentLink` non bloquant (`NetMQAgentLink`, `WebSocketAgentLink`) ; `TestPing` est
  remplacé par `StartPing` / `PollPing`. Le code ZeroMQ n'est pas compilé dans le jeu web.

### Corrigé
- Fond de l'arène (couche « mud » bleue, `MudGround.shader`) absent sous OpenGL et dans le navigateur : on voyait à
  la place l'ancien sprite gris `Maps/background.png`. Le shader, écrit pour le pipeline intégré (CG), n'était plus
  dessiné dès qu'il lisait une valeur du matériau ; il est réécrit pour URP (HLSL, `UnityPerMaterial`), mêmes calculs,
  rendu inchangé en Direct3D.
- Explosion de lumière de fin de match : elle partait du point symétrique de l'épave par rapport au milieu de
  l'écran (un vaisseau détruit en haut de l'arène l'éclairait depuis le bas). La position à l'écran n'est plus
  inversée verticalement (LightExplosion.WorldToEpicenterUV).

### Retiré
- Sortie d'arène dans l'environnement d'entraînement : fin d'épisode, pénalité `reward.oob_penalty`, infos
  `oob_agent1` et `oob_agent2`, `rollout/out_of_bounds_rate`, colonne « hors-arène » d'`evaluate_model.py`, et
  vainqueur par sortie d'arène dans `run_matchup.py`. Le jeu n'a pas cette règle : un vaisseau sorti de l'arène est
  hors de la zone sûre et perd ses PV comme dans un match.
- Curriculum (`training/conf/curriculum/`, mode `round_robin`) : l'entraînement d'exemple a un seul adversaire,
  `opponent=rush_bot` (anciennement `curriculum.opponent`).

## 0.5.0 (16 novembre 2026)

Première version du kit des participants (privée tant que les règles ne sont pas figées).

### Ajouté
- Dépôt unique `automathon-rl` : jeu (`game/`), kit Python (`PythonAI/`), notebook Databricks (`databricks/`).
- `tools/build.py` : compilation du Headless (.NET) et du jeu visuel (Unity en ligne de commande), et paquets tout-en-un.
- `tools/smoke_test.py` : test rapide du Headless (un match scripté de bout en bout).
- Le kit Python trouve le Headless et le jeu dans `build/` s'ils ne sont pas à la racine.
- Bridge Python du protocole en virgule fixe : `bridge/fixed.py` (nombres Q16.16 identiques au C#) et `bridge/datatypes.py` (`Fixed`, `FixedVector2`), repris de CStudio.
- Protocole d'entraînement (toutes les nouveautés sont facultatives : sans elles, le comportement est inchangé) :
  - `ResetOptions` au reset : graine de la carte (`Seed`), densité de murs (`MapDensity`, 0 = arène vide), départ à droite (`SelfOnRight`) ;
  - répétition d'action : `Frames` joue plusieurs frames avec les mêmes actions, et s'arrête plus tôt si un vaisseau meurt ;
  - dans l'état renvoyé : `Outcome` (`Ongoing`, `Win`, `Loss`, `Draw`), `Frame`, `DamageDealt` et `DamageTaken` du dernier step.
- Environnement Gym : options `frame_skip`, `map_density` et `random_side` (section `env` de `common.yaml`). Cartes et côtés tirés avec le générateur de l'environnement, donc reproductibles avec `reset(seed=...)`.
- `tools/smoke_test.py` vérifie le déterminisme, la répétition d'action et les options de reset.
- `evaluation/benchmark.py` : entraîne un PPO from scratch sur plusieurs graines avec le protocole de référence (options et règles surchargeables), évalue tous les checkpoints et calcule un score (moyenne des 4 derniers checkpoints sur les graines).
- `evaluation/evaluate_model.py` : évalue un modèle ou un dossier de checkpoints contre un bot, sur des cartes fixes jouées des deux côtés, avec intervalle de confiance.
- Règles du jeu paramétrables (`GameRules`, côté C# et Python) : PV, dégâts de la mitrailleuse et du missile, missile guidé ou non, durée de vie et nombre maximal de boucliers, durée d'un match avec victoire aux PV. Les valeurs par défaut sont celles du jeu actuel ; le Headless d'entraînement les change au reset (`ResetOptions.Rules`, option `env.rules`, `evaluate_model.py --rules`), pour mesurer des variantes sans recompiler. `MaxHealth` est ajouté à l'état de chaque vaisseau et l'extracteur de features normalise les PV avec.
- Protocole : équipe de chaque vaisseau (`TankState.Team`, `Green` à gauche, `Red` à droite) et propriétaire des balles, missiles et boucliers (`Owner`).
- Extracteur de features v2 (106 features au lieu de 82) : visée des deux vaisseaux, recharges de l'adversaire, missiles et boucliers « à vous ou non », seules les balles adverses, vitesses normalisées par type, orientation des murs et boucliers. Repère retourné de 180° pour l'agent parti à droite (`env.canonical_side`, activé par défaut), y compris pour ses actions et dans `neural_expert_bot`. Les modèles entraînés avec l'ancien extracteur ne sont plus compatibles.
- Protocole binaire d'entraînement (`Core/AI/BinaryProtocol.cs`, `bridge/binary.py`) : steps et états en entiers bruts au lieu de JSON. Un reset JSON avec `"BinaryState": true` passe le dialogue en binaire ; le JSON reste disponible. `FastAutomathonEnv` (`core/env/fast_env.py`) l'utilise, avec l'extracteur de features appliqué à l'état brut et un état paresseux pour les bots : mêmes observations, récompenses et fins d'épisode qu'`AutomathonEnv`. Activé avec `env.backend=binary` à l'entraînement et `--backend binary` à l'évaluation (le benchmark passe l'option de l'un à l'autre).
- Entraînement : option `torch_threads` (threads PyTorch du processus d'apprentissage).
- Environnement à processus unique (`core/env/vec_env.py`, `env.backend=vec`) : un seul processus Python pilote tous les Headless, sans `SubprocVecEnv`. Mêmes observations, récompenses, fins d'épisode et infos que `SubprocVecEnv` + `FastAutomathonEnv` (vérifié sur 3 000 steps à 4 environnements, avec changements d'adversaire et troncatures). Toutes les réponses d'un step sont traitées en un seul appel Numba (lecture, features, récompense, fin de match), et les adversaires en un appel par bot. Entraînement PPO mesuré à 7 180 steps/s avec 32 environnements (4 070 avec `binary` et 12 environnements), 9 410 avec des minibatchs de 2 048.
- Versions Numba des bots (`agents/<bot>/fast.py`, fonction `get_action_raw`), utilisées par l'environnement `vec` pour l'adversaire : `rush_bot` et `dummy_bot`, identiques à leur `agent.py`. `bridge/fixed_numba.py` : calculs en virgule fixe pour Numba, au bit près.
- `bridge.binary.encode_state` : convertit un `GameState` en état binaire, octet pour octet celui qu'enverrait le Headless ; `swap_sides` donne le même état vu par l'adversaire. Noms des colonnes des entités (`B_*`, `M_*`, `S_*`, `W_*`).
- Entraînement : taux de victoire, défaite, nul, temps écoulé et sortie d'arène sur les 100 derniers épisodes (`rollout/win_rate`…), récompense moyenne des épisodes (`VecMonitor`).
- Bords de l'arène dans le bridge : `ARENA_HALF_WIDTH` (13,70) et `ARENA_HALF_HEIGHT` (7,70) dans `bridge/datatypes.py`, face intérieure des murs invisibles qui ferment l'arène. Ces murs ne sont pas envoyés dans l'état (`WallStates`), et leur position n'était documentée nulle part. Vérifié en poussant un vaisseau contre chaque bord : son centre s'arrête à 13,35 et 7,34 (rayon 0,419 et léger enfoncement). Les commentaires de `RandomMapGenerator.cs` annonçaient à tort 13 500 et 7 500 pour les centres de ces murs (13 750 et 7 750).
- **Kit des participants.** Guide de démarrage pour toutes les plateformes (`kit/DEMARRAGE.md`) et README du dépôt des participants (`kit/README.md`), placés à la racine du kit. `tools/build.py export <dossier>` copie le kit des participants dans leur dépôt, et les zips tout-en-un (`package`) le contiennent avec le guide, le notebook Databricks et le test rapide. Les éléments réservés aux organisateurs (README de travail, `CLAUDE.md`, `ORGANISATION.md`, scripts de tournoi et d'enregistrement, `requirements_admin.txt`) n'y sont pas : liste `PARTICIPANT_EXCLUDED` de `tools/build.py`.
- `ORGANISATION.md` : tournoi, enregistrement OBS et procédure de publication d'une version, sortis du README du kit.
- `tools/smoke_test.py` trouve aussi le Headless d'un kit tout-en-un (`Headless/` à la racine) et `AUTOMATHON_HEADLESS_PATH`, et retire la quarantaine de macOS.

### Modifié
- **Règle de match : les agents IA décident à 15 Hz.** Dans le jeu graphique, un agent n'est plus interrogé qu'une frame sur 4 (frames 0, 4, 8… de chaque match, les mêmes pour les deux vaisseaux) et garde son action entre-temps ; l'affichage et la simulation restent à 60 Hz, et les joueurs humains ne sont pas limités (`GameplayConstants.AI_DECISION_INTERVAL`, `bridge.datatypes.DECISION_INTERVAL`). Les deux agents sont aussi interrogés au début de la frame, sur le même état : avant, l'agent de droite voyait déjà la vitesse et la visée de la frame en cours de celui de gauche. Un match du jeu graphique est désormais identique au même match joué sur le Headless avec `frame_skip=4` (vérifié sur 40 matchs, frame de fin et PV). `frame_skip` vaut 4 par défaut (environnements, `common.yaml`, `evaluate_model.py`), et les duels de `run_matchup.py` se jouent dans ces conditions. Mesuré avant ce changement : passer à 15 Hz fait gagner 8 à 11 points aux agents RL contre `rush_bot`, et `evaluate_model.py` ne surestime plus le niveau réel.
- Jeu : code CStudio au commit `49914ee` (simulation en virgule fixe Q16.16, nouveau protocole).
- Entraînement PPO from scratch. `init.model` permet de reprendre un modèle ou un checkpoint (remplace `init.from_bc` et `init.bc_model`).
- Kit porté vers les unités Unity et les types `Fixed` : environnement Gym (actions, sortie d'arène), extracteur de features (mêmes 82 features qu'avant), reward, `dummy_bot`, `rush_bot`, `neural_expert_bot`. Limites d'arène centralisées dans `core/env/reward_shaping.py`.
- Extracteur de features compilé avec Numba (nouvelle dépendance `numba`) : une seule implémentation dans `feature_extractor.py`, utilisée sur l'état binaire, sur un `GameState` (protocole JSON) et en match ; `raw_features.py` est supprimé. Environ 3 µs par état au lieu de 70 µs (NumPy, état binaire) ou 38 µs (Python, `GameState`). Valeurs identiques au bit près à l'extracteur précédent (vérifié sur 4 000 états réels dans les deux orientations) : les modèles existants restent valables. `neural_expert_bot` le compile au démarrage (`warmup()`).
- Génération de carte : le test de connectivité ne teste plus, pour chaque mur, que les cases proches de lui, avec un seul calcul de cos et sin par mur. Un reset passe de 3,4 à 0,44 ms à la densité par défaut (de 12,3 à 0,53 ms à la densité 1,5). Cartes identiques (vérifié sur 4 200 graines et 5 densités).
- Lecture des états environ 2 fois plus rapide : raccourci pour les floats et entiers dans `Fixed`, et plus de double validation des `FixedVector2` par pydantic.
- Extracteur de features : chaque recharge est normalisée par sa propre durée (mitrailleuse et bouclier 120 frames, missile 90, dash 42), au lieu de 60 frames plafonnées à 1. Avant, l'agent voyait 1 pendant la première seconde de recharge de la mitrailleuse et du bouclier. Les modèles entraînés avec l'ancien extracteur ne sont plus compatibles.
- Jeu : les deux vaisseaux démarrent tournés vers le centre. Avant, ils visaient et « avançaient » tous les deux vers la droite : celui de droite tirait et dashait dos à l'adversaire tant qu'il n'avait pas changé de direction (les capacités utilisent la direction de la frame précédente).
- Moteur physique : pré-filtre par cercles englobants avant les tests de collision exacts (avant, chaque paire d'objets passait le test exact à chaque frame). Simulation inchangée au bit près (vérifié sur plus de 4 000 steps avec jusqu'à 54 balles en jeu).
- Reward : dégâts lus dans `DamageDealt` / `DamageTaken` (le coup fatal est maintenant compté), fin de match lue dans `Outcome`, pénalité de temps proportionnelle aux frames jouées. `env.max_steps` compte des frames de jeu.
- **Réglages par défaut de l'entraînement** : le meilleur réglage mesuré pour le PPO du kit (`env.backend=vec`, `env.random_side=true`, 8 environnements, `n_steps` 1 024, minibatch 1 024, lr 3e-4, `torch_threads` 2, récompenses à l'échelle d'une victoire = 10, curriculum `fixed` contre `rush_bot`, 3 M de steps). Mesuré sur 3 graines avec `evaluation/benchmark.py`, sur le jeu corrigé : 63,6 % de victoires contre `rush_bot` (moyenne des 4 derniers checkpoints, de 54 à 70 % selon la graine ; 69 % avant les corrections du jeu). Avant : backend JSON, minibatch 64, lr 1e-3, récompenses 100 fois plus grandes, curriculum `round_robin`. `evaluation/benchmark.py` passe au même minibatch de 1 024.
- Notebook Databricks réécrit pour cette version : archive `Automathon_0.5.0_Linux-ARM-databricks.zip`, `numba` installé, agent d'exemple au protocole actuel, entraînement aux réglages par défaut, reprise avec `init.model`.
- `PythonAI/README.md` devient la documentation du kit pour les participants (contenu des kits, installation, mécaniques, protocole, entraînement, évaluation, dépannage).

### Corrigé
- **Sorties d'arène.** Un vaisseau coincé contre le bord (par un mur de la carte, par exemple) s'enfonçait dans le mur invisible, épais de 0,1. Quand son centre y entrait, le moteur le repoussait vers l'extérieur ou le long du mur (à 86 u/s dans le cas étudié), et le vaisseau sortait de l'arène. Les vaisseaux sont désormais arrêtés par des murs de bord épais de 2 unités (couche `ArenaBorder`), de même face intérieure ; les missiles et les boucliers gardent les murs fins d'origine. Matchs inchangés au bit près (800 matchs de référence entre agents déterministes), le cas reproductible trouvé par `fable_night` (graine 8109) ne sort plus, et un test de stress (vaisseaux qui foncent et dashent contre les bords et dans les coins, 300 matchs) passe de 2 sorties à 0.
- **Balles hors de l'arène.** Les balles traversent les bords et restaient dans l'état pendant toute leur durée de vie (10 s) : 1,6 en moyenne par état, jusqu'à 9, sans aucun effet sur le match. Elles sont détruites à 2 unités au-delà du bord, d'où elles ne peuvent plus toucher un vaisseau ni un bouclier même en partie dans l'arène : 0,1 balle hors de l'arène par état, le temps de parcourir ces 2 unités. La simulation dans l'arène ne change pas ; 4 matchs de référence sur 800 diffèrent, parce que les agents ne voient plus ces balles dans leurs observations.
- **Fin de match signalée une frame trop tard par le Headless.** Un vaisseau tué n'est retiré qu'au début de la frame suivante : l'état de la frame du coup fatal disait encore `Done = false` et `Outcome = Ongoing`, et l'environnement jouait un step de plus, qui simulait une frame après la fin du match. Un vaisseau tué compte désormais comme absent de l'état dès la frame du coup fatal. Sur 800 matchs de référence : mêmes issues, une frame de moins, et 11 matchs où le gagnant prenait encore une balle pendant cette frame de trop (PV finaux faux de 100).
- **Dégâts du missile comptés plusieurs fois.** Les dégâts de zone (400) étaient appliqués une fois pour chaque frame de la liste d'explosion (la frame courante et la précédente), et une fois de plus quand le missile touchait deux choses dans la même frame (le vaisseau et un mur, une balle ou un bouclier) : un missile pouvait retirer 800 à 1 400 PV, au lieu de 600 au contact et 400 en zone. Chaque entité reçoit désormais au plus une fois le plus fort des deux dégâts. Mesuré sur 60 matchs `fable_night.rl` contre `fable_rl` : avant, 4 explosions sur une centaine dépassaient 600 (700, 800, deux fois 1 000) ; après, aucune. **Change l'équilibre** : 24 issues sur les 800 matchs de référence changent (`fable_rl` contre `rush_bot` : 170 → 180 victoires sur 200, `rush_bot` profitait le plus du double compte).
- **Murs sur le point de départ.** Le générateur dégageait la zone et testait la connexité autour de (±6,75 ; 0), alors que les vaisseaux apparaissent en (±10 ; 0), et ne regardait que le centre des murs, qui mesurent jusqu'à 4,5 de long : sur 1 000 cartes, 19 % plaçaient un mur sur le vaisseau au départ (à moins de 0,42 de son centre), la moitié à moins d'une unité. Désormais aucun mur à moins de 1,5 unité des points d'apparition, mesuré sur la surface du mur (en virgule fixe, identique sur toutes les plateformes), et la connexité est testée entre les vrais points d'apparition. 14,9 murs par carte en moyenne au lieu de 15,6. **Une même graine donne une autre carte qu'avant** : les résultats mesurés sur des graines fixes (benchmarks, `arena.py`) ne se comparent plus aux anciens. Sur les séries de référence, les agents les plus forts gagnent un peu plus (`fable_rl` contre `rush_bot` : 180 → 196 victoires sur 200) : un mur au départ ajoutait de la chance.
- **États du jeu graphique incomplets.** Seul le Headless remplissait `Frame`, `DamageDealt` et `DamageTaken` : dans le jeu graphique, un agent recevait toujours `Frame = 0` et des dégâts nuls. `AIInputProvider` les remplit désormais : frame du match, et dégâts infligés et subis depuis la décision précédente de l'agent, comme un step du Headless de `DECISION_INTERVAL` frames. Vérifié avec un harnais qui joue le chemin du jeu graphique sans Unity : sur 30 matchs `fable_rl` contre `rush_bot`, chaque état reçu par l'agent (frame, dégâts, PV) est identique à celui du Headless, et les matchs finissent à la même frame avec les mêmes PV.

### Retiré
- Behavioral cloning : `bc_model.zip`, `expert_dataset.npz`, `collect_demonstrations.py`, `behavioral_cloning.py` et leurs configurations.
- Fichiers de patch laissés par CStudio (`claude.diff`, `gemini.diff`, `gemini2.diff`).
- `game/PythonAI/` (bridge et exemples de CStudio), intégré dans `PythonAI/`.
- Les 8 bots de l'ancien protocole, qui ne fonctionnaient plus (`expert_master_bot`, `kiter_bot`, `random_bot`, `random_shooter_bot`, `smart_dash_bot`, `smart_shield_bot`, `smart_shooter_bot`, `target_bot`). Les scripts et les curriculums utilisent `rush_bot` et `dummy_bot`.
- `setup.sh` et `setup.ps1`, désactivés (le kit est distribué en zips tout-en-un), et `evaluation/tournament_results.json` (résultat périmé, désormais ignoré par git).

### Problèmes connus
- Le test de connexité du générateur utilise `Math.Cos` / `Math.Sin` en double : une même graine pourrait, rarement, donner une carte différente sur une autre plateforme (x86 / ARM).
- Le notebook Databricks n'a pas encore été exécuté sur Databricks dans cette version.
- Le jeu graphique n'a pas de limite de temps ni d'écran « égalité » (la règle `MatchDuration` n'existe que dans le Headless).
