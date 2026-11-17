# Importer votre agent sur votre PC

Ce fichier accompagne l'archive produite par `training/export_agent.py` (par exemple sur Databricks).

## Prérequis : installer le kit (une seule fois)

1. Décompresser le kit tout-en-un de votre plateforme (jeu graphique, `Headless/` et `PythonAI/`).
2. Installer les dépendances Python depuis `PythonAI/` :

```bash
cd PythonAI
uv sync                                        # recommandé (https://docs.astral.sh/uv/)
# ou : pip install -r requirements_participant.txt
```

## Importer le modèle entraîné

Copier `training/rl_model.zip` (contenu de cet export) vers :
```
PythonAI/training/rl_model.zip
```
Si vous avez modifié le code de votre agent, copiez aussi le dossier `agents/<votre_agent>/` de l'export.

## Lancer votre agent et le jeu

```bash
cd PythonAI
uv run python agents/run_my_agent.py my_rl_agent --launch-game
```

Dans le menu du jeu : cliquer sur **PLAYER** au-dessus d'un vaisseau pour le passer en **AI**, saisir `5555`
dans **AI TCP PORT** puis **Entrée** (« CONNECTION SUCCESSFUL ! »), puis lancer la partie (**PLAY**).

Pour faire combattre votre agent contre un autre, lancez le second dans un autre terminal sur un autre
port (`uv run python agents/run_my_agent.py rush_bot --port 5556`) et passez l'autre vaisseau en AI sur `5556`.

Sans `--launch-game`, lancez le jeu vous-même :
- **Windows** : double-cliquer sur `AutomathonGame.exe`
- **Linux** : `./AutomathonGame.x86_64` (après `chmod +x AutomathonGame.x86_64` si besoin)
- **macOS** : `xattr -dr com.apple.quarantine AutomathonGame.app` puis `open AutomathonGame.app`

## Questions fréquentes

**Mon modèle entraîné sur Linux fonctionne-t-il sur Windows ou macOS ?**
Oui. Les modèles SB3 (.zip) sont 100 % cross-platform, à condition d'avoir les mêmes
versions de `stable-baselines3` et `torch` (garanti par `uv sync`).

**Et sur un Mac Intel ?**
`torch` n'y est plus disponible : le jeu et les agents heuristiques fonctionnent, mais pas
`my_rl_agent`. Testez votre agent entraîné sur un autre poste ou sur Databricks.

**Quel extracteur de features est utilisé ?**
`agents/my_rl_agent/feature_extractor.py` (112 valeurs), le même à l'entraînement et en match.
Si vous l'avez modifié, copiez-le avec votre dossier d'agent.
