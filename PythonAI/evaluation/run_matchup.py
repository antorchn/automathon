import os
import sys
import argparse

# Ajouter le dossier parent au sys.path pour pouvoir importer `core` et `my_agent`
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.datatypes import DECISION_INTERVAL
from core.env.automathon_env import AutomathonEnv
from training.utils import get_unity_headless_path, launch_headless, get_port_base
import importlib

def load_agent_class(folder_name):
    module = importlib.import_module(f"agents.{folder_name}.agent")
    return module.ParticipantAgent

def play_matchup(env: AutomathonEnv, agent1, agent2, num_games: int = 301, verbose: bool = True, name1: str = None, name2: str = None):
    env.set_opponent(agent2)
    wins_agent1 = 0
    wins_agent2 = 0
    draws = 0
    
    name1 = name1 or agent1.__class__.__name__
    name2 = name2 or agent2.__class__.__name__
    
    if verbose:
        print(f"\n--- Matchup: {name1} vs {name2} ({num_games} matchs) ---")
    
    match_idx = 0
    interrupted = 0
    while match_idx < num_games:
        obs, _ = env.reset()
        done = False

        while not done:
            action = agent1.get_action(env._last_state)
            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated

        # Partie interrompue par un délai dépassé du Headless : non comptée, rejouée
        if info.get("tcp_timeout"):
            interrupted += 1
            if interrupted > max(10, num_games):
                raise RuntimeError(f"{interrupted} parties interrompues : le Headless ne répond plus correctement.")
            if verbose:
                print("Partie interrompue (le Headless n'a pas répondu à temps), rejouée.")
            continue
        match_idx += 1

        # Vérifier le gagnant
        last_state = env._last_state
        if last_state:
            hp1 = last_state.SelfShip.Health if last_state.SelfShip else 0
            hp2 = last_state.EnemyShip.Health if last_state.EnemyShip else 0

            if hp1 <= 0 and hp2 > 0:
                wins_agent2 += 1
                winner = name2
            elif hp2 <= 0 and hp1 > 0:
                wins_agent1 += 1
                winner = name1
            else:
                draws += 1
                winner = "Égalité"
                    
        if verbose:
            print(f"Match {match_idx}/{num_games} terminé. Vainqueur : {winner}")
            
    if wins_agent1 > wins_agent2:
        overall_winner = agent1
    elif wins_agent2 > wins_agent1:
        overall_winner = agent2
    else:
        overall_winner = agent1 # Avantage arbitraire au joueur 1
        
    if verbose:
        print(f"\nRÉSULTAT {name1} vs {name2} :")
        print(f"{name1} : {wins_agent1} victoires")
        print(f"{name2} : {wins_agent2} victoires")
        print(f"Égalités : {draws}")
        print(f"VAINQUEUR DU MATCHUP : {name1 if wins_agent1 >= wins_agent2 else name2}")
        
    return overall_winner, wins_agent1, wins_agent2, draws


def run_matchup(num_games: int = 301, agent1_folder: str = "rush_bot", agent2_folder: str = "dummy_bot",
                port: int | None = None):
    # Port par défaut hors de la plage de rl_training (port_base+) et de run_my_agent.py (5555)
    port = port or get_port_base() + 200
    unity_exe_path = get_unity_headless_path()

    print(f"Lancement de Unity en Headless Mode : {unity_exe_path} sur le port {port}")
    process = launch_headless(port)
    # Conditions d'un match : les deux agents decident aux memes frames, une sur DECISION_INTERVAL (15 Hz)
    env = AutomathonEnv(tcp_port=str(port), frame_skip=DECISION_INTERVAL)

    # Les dossiers des deux bots doivent exister dans agents/
    agent1 = load_agent_class(agent1_folder)()
    agent2 = load_agent_class(agent2_folder)()
    
    try:
        play_matchup(env, agent1, agent2, num_games, verbose=True, name1=agent1_folder, name2=agent2_folder)
    finally:
        print("Fermeture de l'environnement Unity...")
        env.game.end_training()
        process.kill()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Duel entre deux agents sur le Headless, dans les conditions d'un match "
                                                 "(les deux agents décident une frame sur 4, 15 Hz).")
    parser.add_argument("agent1", nargs="?", default="rush_bot", help="Dossier du 1er agent dans agents/")
    parser.add_argument("agent2", nargs="?", default="dummy_bot", help="Dossier du 2e agent dans agents/")
    parser.add_argument("--games", type=int, default=21, help="Nombre de matchs (défaut : 21)")
    parser.add_argument("--port", type=int, default=None, help="Port du Headless (défaut : AUTOMATHON_PORT_BASE + 200)")
    args = parser.parse_args()
    run_matchup(args.games, args.agent1, args.agent2, args.port)
