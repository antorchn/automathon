"""Test de bout en bout contre une instance de l'arène en fonctionnement.

Pré-requis (développement / test) :
  - `docker compose up -d` avec `AUTH_DEV_MODE=true` dans `.env`
  - Ou serveur local lancé avec `uvicorn arene.app:app`

Usage :
  python scripts/e2e_test.py --url http://127.0.0.1:8001/arene [--include-hostile]
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def _build_renamed_bot_zip(src_dir: Path, new_name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src_dir.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            zf.write(p, f"{new_name}/{p.relative_to(src_dir).as_posix()}")
    return buf.getvalue()


def _build_inline_zip(name: str, code: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{name}/agent.py", code)
    return buf.getvalue()


def _login_and_submit(base_url: str, login: str, zip_bytes: bytes, filename: str) -> tuple[httpx.Client, str]:
    client = httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=True)
    r = client.post("/auth/dev/login", data={"login": login})
    r.raise_for_status()
    page = client.get("/submit")
    page.raise_for_status()
    m = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    if not m:
        raise RuntimeError(f"Jeton CSRF introuvable sur /submit pour {login}")
    csrf = m.group(1)

    sub = client.post(
        "/submit",
        data={"csrf_token": csrf},
        files={"agent_zip": (filename, zip_bytes, "application/zip")},
    )
    if sub.status_code >= 400:
        raise RuntimeError(f"Échec soumission {filename} ({sub.status_code}): {sub.text[:400]}")
    st = client.get("/api/my-status").json()
    agent_id = st["submissions"][0]["id"]
    return client, agent_id


def _wait_terminal(client: httpx.Client, agent_id: str, timeout_s: int = 600) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = client.get("/api/my-status").json()
        sub = next((s for s in st["submissions"] if s["id"] == agent_id), None)
        if sub and sub["status"] in ("ranked", "rejected"):
            return sub
        time.sleep(2.0)
    raise TimeoutError(f"Agent {agent_id} toujours non terminé après {timeout_s}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test de bout en bout de l'arène Automathon.")
    parser.add_argument("--url", default="http://127.0.0.1:8001/arene", help="URL de base de l'arène")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout attente placement (s)")
    parser.add_argument("--include-hostile", action="store_true", help="Teste aussi les 5 agents hostiles de la spec")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    health = httpx.get(f"{base_url}/health", timeout=10.0)
    health.raise_for_status()
    print(f"[OK] Santé arène : {health.json()}")

    kit_agents = ROOT / "sources" / "extracted" / "kit" / "PythonAI" / "agents"
    if not kit_agents.is_dir():
        kit_agents = ROOT / "PythonAI" / "agents"

    rush_zip = _build_renamed_bot_zip(kit_agents / "rush_bot", "e2e_rush_copy")
    dummy_zip = _build_renamed_bot_zip(kit_agents / "dummy_bot", "e2e_dummy_copy")

    c1, ag1 = _login_and_submit(base_url, "e2e_user_1", rush_zip, "e2e_rush_copy.zip")
    c2, ag2 = _login_and_submit(base_url, "e2e_user_2", dummy_zip, "e2e_dummy_copy.zip")
    print(f"[OK] Agents envoyés : {ag1}, {ag2}")

    res1 = _wait_terminal(c1, ag1, timeout_s=args.timeout)
    res2 = _wait_terminal(c2, ag2, timeout_s=args.timeout)
    assert res1["status"] == "ranked", f"e2e_rush_copy non classé : {res1}"
    assert res2["status"] == "ranked", f"e2e_dummy_copy non classé : {res2}"
    print(f"[OK] Placement terminé : {res1['name']}={res1['elo']}, {res2['name']}={res2['elo']}")

    lb = httpx.get(f"{base_url}/api/leaderboard", timeout=10.0).json()
    anchor = next(e for e in lb["entries"] if e["name"] == "rush_bot")
    assert anchor["elo"] == 1000.0, f"rush_bot a dérivé : {anchor}"
    print(f"[OK] Classement vérifié ({len(lb['entries'])} agents actifs, rush_bot=1000.0)")

    # Vérifier téléchargement d'un replay
    ag_detail = httpx.get(f"{base_url}/api/agents/{ag1}", timeout=10.0).json()
    match_with_replay = next((m for m in ag_detail["matches"] if m["has_replay"]), None)
    assert match_with_replay is not None, "Aucun replay trouvé pour l'agent classé"
    rep_resp = httpx.get(f"{base_url}/replays/{match_with_replay['id']}.json", timeout=15.0)
    rep_resp.raise_for_status()
    assert rep_resp.headers.get("content-encoding") == "gzip"
    replay_json = rep_resp.json()
    assert replay_json["Version"] == 1 and len(replay_json["Steps"]) > 0
    print(f"[OK] Replay {match_with_replay['id']} téléchargé et valide ({len(replay_json['Steps'])} décisions)")

    if args.include_hostile:
        hostiles = {
            "hostile_net": (
                "import socket\nfrom bridge.datatypes import AIAction\n"
                "class ParticipantAgent:\n"
                "    def get_action(self, s):\n"
                "        socket.create_connection(('1.1.1.1', 80), timeout=1)\n"
                "        return AIAction()\n"
            ),
            "hostile_fs": (
                "from bridge.datatypes import AIAction\n"
                "class ParticipantAgent:\n"
                "    def get_action(self, s):\n"
                "        open('/etc/automathon_pwn', 'w').write('x')\n"
                "        return AIAction()\n"
            ),
            "hostile_oom": (
                "from bridge.datatypes import AIAction\n"
                "class ParticipantAgent:\n"
                "    def __init__(self):\n"
                "        self.leak = bytearray(900 * 1024 * 1024)\n"
                "    def get_action(self, s):\n"
                "        return AIAction()\n"
            ),
            "hostile_loop": (
                "from bridge.datatypes import AIAction\n"
                "class ParticipantAgent:\n"
                "    def get_action(self, s):\n"
                "        while True: pass\n"
            ),
            "hostile_exc": (
                "class ParticipantAgent:\n"
                "    def get_action(self, s):\n"
                "        raise RuntimeError('hostile exception')\n"
            ),
        }
        for idx, (hname, code) in enumerate(hostiles.items(), 10):
            hc, hag = _login_and_submit(base_url, f"hostile_u_{idx}", _build_inline_zip(hname, code), f"{hname}.zip")
            hres = _wait_terminal(hc, hag, timeout_s=args.timeout)
            assert hres["status"] == "rejected" or hres["elo"] < 1000.0, f"{hname} aurait dû être refusé ou perdre : {hres}"
            print(f"[OK] Agent hostile {hname} neutralisé : status={hres['status']} reason={hres.get('reject_reason', '')[:80]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
