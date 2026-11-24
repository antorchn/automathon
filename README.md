# Automathon

Automathon est un duel 2D de vaisseaux spatiaux, conçu comme support d'un hackathon d'intelligence artificielle :
chaque équipe écrit en Python l'agent qui pilote son vaisseau, avec des règles écrites à la main, de
l'apprentissage par renforcement, ou les deux.

**Pour commencer : [DEMARRAGE.md](DEMARRAGE.md).**

## Récupérer le kit

Chaque version publiée a, dans les releases de ce dépôt, un kit tout-en-un par plateforme : le jeu graphique et le
Headless (simulateur sans affichage) déjà compilés, et le kit Python. Rien d'autre à installer que
[uv](https://docs.astral.sh/uv/).

| Kit | Pour |
|---|---|
| `Automathon_<version>_Windows.zip` | PC Windows 10 ou 11, 64 bits |
| `Automathon_<version>_Windows-ARM64.zip` | PC Windows 11 sur processeur ARM (Snapdragon) : jeu et Headless natifs ARM64 (voir l'installation dans `DEMARRAGE.md`) |
| `Automathon_<version>_Linux-x86-64.zip` | PC Linux x86-64 |
| `Automathon_<version>_MacOS-arm64.zip` / `…_MacOS-x64.zip` | Mac Apple Silicon / Mac Intel |
| `Automathon_<version>_Linux-ARM-databricks.zip` | Databricks sur processeurs ARM : Headless et kit Python, sans jeu graphique (sur une machine x86, le kit `Linux-x86-64`) |

## Contenu du dépôt

| Dossier | Contenu |
|---|---|
| `PythonAI/` | Kit Python : communication avec le jeu, environnement Gymnasium, agents d'exemple, entraînement PPO, évaluation. Documentation complète : [PythonAI/README.md](PythonAI/README.md) |
| `databricks/` | Guide des participants (`guide_participants_automathon.html`, à ouvrir dans un navigateur) et notebooks Databricks : `automathon_demarrage.ipynb` (premier agent et envoi dans l'Arène), `Automathon_Databricks.ipynb` (entraînement détaillé dans un Volume personnel) |
| `game/` | Code source du jeu : règles en C# (`game/AutomathonUnity/Assets/Core/`), projet Unity, simulateur Headless |
| `tools/` | `smoke_test.py` (test rapide du Headless) et `build.py` (compilation) |
| `arene/`, `worker/`, `agent_host/`, `scripts/`, `tests/` | Code de l'arène en ligne, le site où l'on envoie son agent pour le classer : site web, worker qui joue les parties, hôte isolé des agents, déploiement et tests. Installation : [INSTALL.md](INSTALL.md) ; intégration au site de l'association : [INTEGRATION_SITE.md](INTEGRATION_SITE.md). Inutile pour participer : ce n'est pas le kit. |

Le code du jeu fait foi pour les règles exactes : vitesses, dégâts, collisions, génération des cartes.

## Compiler soi-même (facultatif)

Les kits de la release suffisent. Pour recompiler depuis ce dépôt :

```bash
python tools/build.py headless     # Headless pour la plateforme courante (.NET SDK 10)
python tools/build.py game         # jeu graphique (Unity 6000.0.66f2 exactement, éditeur fermé)
```

Les sorties vont dans `build/`, où le kit Python les trouve automatiquement.

## Crédits

Jeu : CStudio, sous licence MIT (`game/LICENSE.md`), dont l'explosion de lumière de fin de match. Kit Python et
outillage : Automatants.
