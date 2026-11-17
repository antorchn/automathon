"""
Test rapide du Headless compile : il demarre, repond au protocole et joue des matchs jusqu'au bout.

Usage (depuis la racine du depot, apres `python tools/build.py headless`, ou depuis PythonAI/ dans un kit) :
    python tools/smoke_test.py [--headless CHEMIN] [--matches 3]
    uv run python ../tools/smoke_test.py

Le vaisseau de gauche fonce vers l'adversaire en tirant, celui de droite reste immobile.
Le test reussit si les etats recus sont complets, si les vaisseaux bougent, si des degats
sont infliges et si au moins un match se termine. Seul pyzmq est necessaire.
"""

import argparse
import json
import math
import os
import platform
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import zmq

ROOT = Path(__file__).resolve().parent.parent
MAX_FRAMES = 3600 * 2  # 2 minutes de jeu par match au maximum
NO_ACTION = {"MovingDirection": {"X": 0, "Y": 0}, "AimingDirection": {"X": 0, "Y": 0},
             "MachineGun": False, "Missile": False, "Shield": False, "Dash": False}


def host_rid() -> str:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    return f"{ {'win32': 'win', 'darwin': 'osx'}.get(sys.platform, 'linux') }-{arch}"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, process: subprocess.Popen, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            sys.exit(f"ECHEC : le Headless s'est arrete au demarrage (code {process.returncode}).")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.2)
    sys.exit(f"ECHEC : le Headless n'ecoute pas sur le port {port} apres {timeout:.0f} s.")


class Client:
    def __init__(self, port: int):
        self.socket = zmq.Context.instance().socket(zmq.REQ)
        self.socket.setsockopt(zmq.RCVTIMEO, 5000)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.connect(f"tcp://127.0.0.1:{port}")

    def send(self, reset=False, done=False, self_action=None, enemy_action=None, frames=1, options=None) -> dict:
        self.socket.send_string(json.dumps({"Reset": reset, "DoneWithTraining": done,
                                            "SelfAction": self_action, "EnemyAction": enemy_action,
                                            "Frames": frames, "ResetOptions": options}))
        try:
            return json.loads(self.socket.recv_string())
        except zmq.Again:
            sys.exit("ECHEC : pas de reponse du Headless en 5 s.")


def check_state(state: dict):
    for key in ("SelfShip", "EnemyShip"):
        ship = state.get(key)
        if not ship or "Position" not in ship or "Health" not in ship:
            sys.exit(f"ECHEC : etat incomplet, {key} = {ship!r}")


def rush(state: dict, sidestep: int) -> dict:
    """Vise l'adversaire et tire ; avance vers lui, ou de cote (sidestep = +1/-1) pour contourner un mur."""
    me, enemy = state["SelfShip"]["Position"], state["EnemyShip"]["Position"]
    dx, dy = enemy["X"] - me["X"], enemy["Y"] - me["Y"]
    norm = math.hypot(dx, dy) or 1.0
    aim = {"X": round(dx / norm, 4), "Y": round(dy / norm, 4)}
    move = {"X": -aim["Y"] * sidestep, "Y": aim["X"] * sidestep} if sidestep else aim
    return {**NO_ACTION, "MovingDirection": move, "AimingDirection": aim, "MachineGun": True, "Missile": True}


def play_match(client: Client) -> dict:
    state = client.send(reset=True)
    check_state(state)
    start_pos = dict(state["SelfShip"]["Position"])
    start_health = state["EnemyShip"]["Health"]
    moved, frames, dealt = False, 0, 0.0
    last_pos, still, sidestep, sidestep_left, side = start_pos, 0, 0, 0, 1
    while frames < MAX_FRAMES and not state.get("Done"):
        state = client.send(self_action=rush(state, sidestep), enemy_action=NO_ACTION)
        frames += 1
        dealt += state.get("DamageDealt", 0)
        if state.get("Done"):
            break
        check_state(state)
        pos = state["SelfShip"]["Position"]
        moved = moved or (pos["X"], pos["Y"]) != (start_pos["X"], start_pos["Y"])
        # Bloque contre un mur pendant 0,5 s : on se decale sur le cote pendant 1 s, en alternant le cote.
        still = still + 1 if math.hypot(pos["X"] - last_pos["X"], pos["Y"] - last_pos["Y"]) < 1e-3 else 0
        last_pos = pos
        if sidestep_left:
            sidestep_left -= 1
            sidestep = sidestep if sidestep_left else 0
        elif still >= 30:
            sidestep, sidestep_left, side, still = side, 60, -side, 0
    enemy = state.get("EnemyShip")
    enemy_health = enemy["Health"] if enemy else 0
    # Le JSON arrondit chaque Fixed à quelques décimales : avec la zone sûre (0,83 PV par frame), les arrondis
    # s'accumulent d'un step à l'autre
    if abs(dealt - (start_health - enemy_health)) > 1e-6 + 1e-5 * frames:
        sys.exit(f"ECHEC : somme des DamageDealt ({dealt}) differente des PV perdus ({start_health - enemy_health})")
    if not enemy and state.get("SelfShip") and state.get("Outcome") != "Win":
        sys.exit(f"ECHEC : adversaire detruit mais Outcome = {state.get('Outcome')!r}")
    return {"frames": frames, "done": bool(state.get("Done")), "moved": moved,
            "damage": start_health - enemy_health}


def without_step_events(state: dict) -> dict:
    """Etat sans les champs propres au dernier step (pour comparer deux deroulements)."""
    return {k: v for k, v in state.items() if k not in ("DamageDealt", "DamageTaken")}


def scripted_run(client: Client, seed: int, decisions: int, frames_per_decision: int, split_frames: bool) -> dict:
    """Joue `decisions` decisions scriptees sur la carte `seed`, chacune pendant `frames_per_decision` frames,
    en un seul message (Frames=k) ou en k messages d'une frame (split_frames)."""
    state = client.send(reset=True, options={"Seed": seed})
    for _ in range(decisions):
        if state.get("Done"):
            break
        action = rush(state, 0)
        if split_frames:
            for _ in range(frames_per_decision):
                state = client.send(self_action=action, enemy_action=NO_ACTION)
                if state.get("Done"):
                    break
        else:
            state = client.send(self_action=action, enemy_action=NO_ACTION, frames=frames_per_decision)
    return without_step_events(state)


def check_protocol(client: Client):
    """Options de reset, repetition d'action et determinisme."""
    first = client.send(reset=True, options={"Seed": 1234})
    again = client.send(reset=True, options={"Seed": 1234})
    other = client.send(reset=True, options={"Seed": 1235})
    if first["WallStates"] != again["WallStates"]:
        sys.exit("ECHEC : deux resets avec la meme graine donnent des cartes differentes")
    if first["WallStates"] == other["WallStates"]:
        sys.exit("ECHEC : deux graines differentes donnent la meme carte")

    run_a = scripted_run(client, 1234, 150, 1, split_frames=False)
    run_b = scripted_run(client, 1234, 150, 1, split_frames=False)
    if run_a != run_b:
        sys.exit("ECHEC : meme graine et memes actions, mais deroulements differents (simulation non deterministe)")
    repeated = scripted_run(client, 77, 60, 4, split_frames=False)
    split = scripted_run(client, 77, 60, 4, split_frames=True)
    if repeated != split:
        sys.exit("ECHEC : Frames=4 ne donne pas le meme resultat que 4 messages d'une frame")
    if repeated["Frame"] != split["Frame"] or repeated["Frame"] == 0:
        sys.exit(f"ECHEC : compteur Frame incoherent ({repeated['Frame']} / {split['Frame']})")

    state = client.send(reset=True, options={"Seed": 5, "SelfOnRight": True})
    if not state["SelfShip"]["Position"]["X"] > 0 > state["EnemyShip"]["Position"]["X"]:
        sys.exit("ECHEC : SelfOnRight ne place pas le vaisseau a droite")
    state = client.send(reset=True, options={"Seed": 5, "MapDensity": 0})
    if state["WallStates"]:
        sys.exit(f"ECHEC : MapDensity=0 laisse {len(state['WallStates'])} murs")
    state = client.send(self_action=NO_ACTION, enemy_action=NO_ACTION, frames=30)
    if state["Frame"] != 30:
        sys.exit(f"ECHEC : Frames=30 donne Frame={state['Frame']}")
    print(f"Protocole OK : graine de carte, determinisme, Frames=4 identique a 4 frames separees "
          f"({repeated['Frame']} frames jouees), cote de depart, arene vide")


def action(aim=(1, 0), **buttons) -> dict:
    return {**NO_ACTION, "AimingDirection": {"X": aim[0], "Y": aim[1]}, **buttons}


def check_rules(client: Client):
    """Regles parametrables (GameRules), sur une arene vide."""
    def reset(**rules):
        return client.send(reset=True, options={"Seed": 9, "MapDensity": 0, "Rules": rules})

    state = reset(ShipMaxHealth=2000)
    if state["SelfShip"]["Health"] != 2000 or state["SelfShip"]["MaxHealth"] != 2000:
        sys.exit(f"ECHEC : ShipMaxHealth=2000 donne {state['SelfShip']['Health']} / {state['SelfShip']['MaxHealth']} PV")

    # Limite de temps : 1 s a PV egaux = nul ; 3 s apres une rafale qui touche = victoire aux PV
    reset(MatchDuration=1)
    state = client.send(self_action=NO_ACTION, enemy_action=NO_ACTION, frames=200)
    if not (state["Done"] and state["Frame"] == 60 and state["Outcome"] == "Draw"):
        sys.exit(f"ECHEC : MatchDuration=1 donne Done={state['Done']}, Frame={state['Frame']}, {state['Outcome']}")
    reset(MatchDuration=3, ShipMaxHealth=2000)
    client.send(self_action=action(MachineGun=True), enemy_action=NO_ACTION)
    state = client.send(self_action=NO_ACTION, enemy_action=NO_ACTION, frames=400)
    if not (state["Done"] and state["Outcome"] == "Win" and state["Frame"] == 180):
        sys.exit(f"ECHEC : victoire aux PV attendue, obtenu {state['Outcome']} (Frame={state['Frame']})")

    # Boucliers : 2 en jeu par defaut apres deux deploiements, 1 seul avec MaxActiveShields=1
    for limit, expected in ((None, 2), (1, 1)):
        reset(**({} if limit is None else {"MaxActiveShields": limit}))
        client.send(self_action=action(Shield=True), enemy_action=NO_ACTION)
        client.send(self_action=NO_ACTION, enemy_action=NO_ACTION, frames=125)
        state = client.send(self_action=action(Shield=True), enemy_action=NO_ACTION)
        if len(state["ShieldStates"]) != expected:
            sys.exit(f"ECHEC : MaxActiveShields={limit} laisse {len(state['ShieldStates'])} boucliers (attendu {expected})")

    # Missile : tire vers le haut, puis vise a droite. Guide, il tourne ; non guide, il reste vertical.
    drift = {}
    for guided in (True, False):
        reset(MissileGuided=guided)
        client.send(self_action=action(aim=(0, 1)), enemy_action=NO_ACTION)  # le missile part selon la visee precedente
        client.send(self_action=action(aim=(0, 1), Missile=True), enemy_action=NO_ACTION)
        state = client.send(self_action=action(aim=(1, 0)), enemy_action=NO_ACTION, frames=20)
        drift[guided] = abs(state["MissileStates"][0]["Velocity"]["X"])
    if not (drift[True] > 1 and drift[False] < 1e-3):
        sys.exit(f"ECHEC : deviation horizontale du missile guide {drift[True]:.3f}, non guide {drift[False]:.3f}")

    print("Regles OK : ShipMaxHealth, MatchDuration (nul et victoire aux PV), MaxActiveShields, MissileGuided")


def check_ownership(client: Client):
    """Equipe des vaisseaux (Green a gauche, Red a droite) et proprietaire des balles, missiles et boucliers."""
    for self_on_right, team in ((False, "Green"), (True, "Red")):
        state = client.send(reset=True, options={"Seed": 9, "MapDensity": 0, "SelfOnRight": self_on_right})
        enemy_team = state["EnemyShip"]["Team"]
        if state["SelfShip"]["Team"] != team or enemy_team == team:
            sys.exit(f"ECHEC : SelfOnRight={self_on_right} donne les equipes {state['SelfShip']['Team']} / {enemy_team}")
        # Balles et missile vers le haut, bouclier vers le bas (un missile qui touche un bouclier le detruit)
        client.send(self_action=action(aim=(0, 1)), enemy_action=NO_ACTION)
        client.send(self_action=action(aim=(0, 1), MachineGun=True, Missile=True), enemy_action=NO_ACTION)
        client.send(self_action=action(aim=(0, -1)), enemy_action=NO_ACTION)
        state = client.send(self_action=action(aim=(0, -1), Shield=True), enemy_action=NO_ACTION, frames=5)
        owners = {e["Owner"] for key in ("BulletStates", "MissileStates", "ShieldStates") for e in state[key]}
        counts = [len(state[key]) for key in ("BulletStates", "MissileStates", "ShieldStates")]
        if owners != {team} or 0 in counts:
            sys.exit(f"ECHEC : proprietaires {owners} (attendu {team}), balles/missiles/boucliers = {counts}")
    print("Equipes et proprietaires OK : Green a gauche, Red a droite, balles, missiles et boucliers attribues")


def check_binary(client: Client):
    """Protocole binaire : reset JSON avec BinaryState, steps binaires, puis retour au JSON."""
    def header(data: bytes):
        return struct.unpack_from("<12i", data)  # version, done, outcome, frame, degats x2, 4 effectifs, zone x2

    client.socket.send_string(json.dumps({"Reset": True, "DoneWithTraining": False, "BinaryState": True,
                                          "ResetOptions": {"Seed": 9}}))
    reset = client.socket.recv()
    step = struct.pack("<12i", 0x31504554, 3, 0, 0, 1 << 16, 0, 1, 0, 0, 0, 0, 0)  # 3 frames, tire vers la droite
    client.socket.send(step)
    data = client.socket.recv()
    h0, h1 = header(reset), header(data)
    expected = 12 + 2 * 16 + 6 * h1[6] + 8 * h1[7] + 9 * h1[8] + 5 * h1[9]
    if h0[0] != 2 or h0[3] != 0 or h1[3] != 3 or len(data) != 4 * expected or h1[9] == 0:
        sys.exit(f"ECHEC : etat binaire inattendu (en-tetes {h0} / {h1}, {len(data)} octets)")
    state = client.send(self_action=NO_ACTION, enemy_action=NO_ACTION)  # le JSON marche toujours apres
    if state["Frame"] != 4:
        sys.exit(f"ECHEC : step JSON apres steps binaires, Frame={state['Frame']} (attendu 4)")
    print(f"Protocole binaire OK : reset, step de 3 frames ({len(data)} octets, {h1[9]} murs), retour au JSON")


def check_replay(client: Client, headless: Path, env: dict):
    """
    Un match enregistre (graine, actions des deux vaisseaux, une decision sur 4 frames) puis rejoue par un Headless
    neuf (`Headless --replay`) finit a la meme frame avec les memes PV : ce que le jeu web rejoue est bien ce match.
    Format : Core/AI/MatchReplay.cs, ecrit par PythonAI/webplay/replay.py.
    """
    seed, steps = 1234, []
    state = client.send(reset=True, options={"Seed": seed})
    while not state.get("Done") and state.get("Frame", 0) < 1200:
        if not state.get("SelfShip") or not state.get("EnemyShip"):
            break
        left = rush(state, 0)
        right = {**rush({"SelfShip": state["EnemyShip"], "EnemyShip": state["SelfShip"]}, 1), "Missile": False}
        steps.append({"Frames": 4, "Left": left, "Right": right})
        state = client.send(self_action=left, enemy_action=right, frames=4)
    ships = (state.get("SelfShip"), state.get("EnemyShip"))
    result = {"Frame": state["Frame"], "Outcome": state["Outcome"],
              "LeftPosition": ships[0]["Position"] if ships[0] else None,
              "RightPosition": ships[1]["Position"] if ships[1] else None,
              "LeftHealth": ships[0]["Health"] if ships[0] else 0, "RightHealth": ships[1]["Health"] if ships[1] else 0}
    replay = {"Version": 1, "Seed": seed, "Steps": steps, "Result": result}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "replay.json"
        path.write_text(json.dumps(replay), encoding="utf-8")
        run = subprocess.run([str(headless), "--replay", str(path)], cwd=headless.parent, env=env,
                             capture_output=True, text=True, timeout=60)
    if run.returncode != 0:
        sys.exit(f"ECHEC : le replay ne redonne pas le meme match ({run.stdout.strip()} {run.stderr.strip()})")
    print(f"Replay OK : {len(steps)} decisions rejouees sur un Headless neuf, fin a la frame {result['Frame']} "
          f"({result['Outcome']}, PV {result['LeftHealth']} / {result['RightHealth']})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    rid = host_rid()
    exe = "Headless.exe" if rid.startswith("win") else "Headless"
    # AUTOMATHON_HEADLESS_PATH, puis le Headless d'un kit tout-en-un (Headless/ a la racine), puis la sortie de
    # tools/build.py
    candidates = [Path(p) for p in [os.environ.get("AUTOMATHON_HEADLESS_PATH")] if p]
    candidates += [ROOT / "Headless" / exe, ROOT / "build" / "headless" / rid / exe]
    default = next((c for c in candidates if c.exists()), candidates[-1])
    parser.add_argument("--headless", type=Path, default=default)
    parser.add_argument("--matches", type=int, default=3)
    args = parser.parse_args()

    if not args.headless.exists():
        sys.exit(f"ECHEC : {args.headless} introuvable. Lancez d'abord : python tools/build.py headless")
    if sys.platform != "win32":
        os.chmod(args.headless, 0o755)  # droit d'execution parfois perdu a la decompression
    if sys.platform == "darwin":  # dossier telecharge : quarantaine de Gatekeeper
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(args.headless.parent)], capture_output=True)

    port = free_port()
    env = {**os.environ, "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1"}
    process = subprocess.Popen([str(args.headless), f"tcp://127.0.0.1:{port}"], cwd=args.headless.parent, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        wait_port(port, process)
        client = Client(port)
        check_protocol(client)
        check_rules(client)
        check_ownership(client)
        check_binary(client)
        check_replay(client, args.headless, env)
        results, start = [], time.monotonic()
        for i in range(args.matches):
            r = play_match(client)
            results.append(r)
            print(f"Match {i + 1} : {r['frames']} frames, termine={r['done']}, "
                  f"degats infliges={r['damage']}, deplacement={r['moved']}")
        total = sum(r["frames"] for r in results)
        print(f"Vitesse : {total / (time.monotonic() - start):.0f} frames/s (un seul Headless)")
        client.send(done=True)
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()

    failures = []
    if not all(r["moved"] for r in results):
        failures.append("un vaisseau n'a pas bouge")
    if not any(r["damage"] > 0 for r in results):
        failures.append("aucun degat inflige")
    if not any(r["done"] for r in results):
        failures.append("aucun match termine")
    if failures:
        sys.exit("ECHEC : " + ", ".join(failures))
    print("OK")


if __name__ == "__main__":
    main()
