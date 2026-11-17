import os
import sys
import argparse
import importlib

import zmq

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.play import Play
from training.utils import launch_game

def get_available_agents():
    agents_dir = os.path.dirname(os.path.abspath(__file__))
    available = []

    for item in sorted(os.listdir(agents_dir)):
        item_path = os.path.join(agents_dir, item)
        if os.path.isdir(item_path):
            agent_file = os.path.join(item_path, "agent.py")
            if os.path.exists(agent_file):
                available.append(item)
    return available

def choose_agent_interactively(available_agents):
    print("Sélectionnez l'agent à lancer :")
    for i, agent_name in enumerate(available_agents):
        print(f"{i} - {agent_name}")

    choice = input(f"Votre choix [0-{len(available_agents)-1}] (Défaut: 0) : ").strip()
    if not choice or not choice.isdigit() or int(choice) >= len(available_agents):
        choice = "0"
    return available_agents[int(choice)]

def main():
    parser = argparse.ArgumentParser(
        description="Lance un agent en mode Play : le jeu graphique s'y connecte en saisissant son port "
                    "dans le menu (joueur passé en AI)."
    )
    parser.add_argument("agent", nargs="?", help="Dossier de l'agent dans agents/ (sans argument : menu interactif)")
    parser.add_argument("--port", type=int,
                        help="Port TCP (défaut : 5555). Pour faire combattre deux agents, lancer le second "
                             "dans un autre terminal avec un autre port, ex. --port 5556")
    parser.add_argument("--launch-game", action="store_true", help="Lance aussi le jeu graphique (Windows, Linux, macOS)")
    args = parser.parse_args()

    available_agents = get_available_agents()

    print("=== Lanceur d'Agent Automathon ===")
    if not available_agents:
        print("Aucun agent trouvé dans le dossier 'agents/'.")
        return

    if args.agent:
        if args.agent not in available_agents:
            print(f"Agent inconnu : {args.agent}. Agents disponibles : {', '.join(available_agents)}")
            sys.exit(1)
        selected_agent_folder = args.agent
    else:
        selected_agent_folder = choose_agent_interactively(available_agents)

    print(f"\nChargement de l'agent : {selected_agent_folder}...")

    module_name = f"agents.{selected_agent_folder}.agent"
    module = importlib.import_module(module_name)

    if not hasattr(module, "ParticipantAgent"):
        print(f"Erreur : La classe ParticipantAgent est introuvable dans {module_name}.")
        return

    agent = module.ParticipantAgent()

    port = str(args.port) if args.port is not None else "5555"
    if args.port is None and not args.agent:
        port_input = input("Entrez le port TCP à utiliser (Défaut: 5555) : ").strip()
        port = port_input if port_input else "5555"

    print(f"Démarrage du serveur sur le port {port}...")

    try:
        play = Play(agent.get_action, tcp_port=port)
    except zmq.ZMQError as e:
        print(f"Impossible d'ouvrir le port {port} ({e}). Un autre agent l'utilise peut-être déjà : "
              f"choisissez un autre port, ex. --port {int(port) + 1 if port.isdigit() else 5556}")
        sys.exit(1)

    if args.launch_game:
        launch_game()
    else:
        print("Lancez le jeu graphique si ce n'est pas déjà fait.")
    print(f"Dans le menu du jeu : cliquer sur PLAYER au-dessus d'un vaisseau pour le passer en AI, "
          f"saisir {port} dans 'AI TCP PORT' puis Entrée ('CONNECTION SUCCESSFUL !'), puis lancer la partie.")

    try:
        while True:
            play.respond()
    except KeyboardInterrupt:
        print("\nServeur arrêté par l'utilisateur.")

if __name__ == "__main__":
    main()
