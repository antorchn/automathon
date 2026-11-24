#!/usr/bin/env bash
# Déploie ou met à jour l'Arène Automathon sur la VM de production.
#
# Usage (depuis votre poste, via SSH) :
#   ./scripts/deploy.sh <utilisateur@hote-vm> [--ref main] [--dir /opt/automathon-arene] [--repo <url>]
#
# Usage (directement sur la VM) :
#   ./scripts/deploy.sh --local [--ref main]
set -euo pipefail

LOCAL_MODE=0
REMOTE_HOST=""
REF="main"
REMOTE_DIR="/opt/automathon-arene"
REPO_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) LOCAL_MODE=1; shift ;;
    --ref) REF="$2"; shift 2 ;;
    --dir) REMOTE_DIR="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    -*) echo "Option inconnue : $1" >&2; exit 2 ;;
    *)
      if [[ -z "${REMOTE_HOST}" ]]; then
        REMOTE_HOST="$1"
        shift
      else
        echo "Argument inattendu : $1" >&2
        exit 2
      fi
      ;;
  esac
done

deploy_local() {
  local workdir="$1"
  cd "${workdir}"

  if [[ -d .git ]]; then
    echo "=== Mise à jour git (${REF}) ==="
    git fetch origin --tags
    if git show-ref --verify --quiet "refs/remotes/origin/${REF}"; then
      git checkout -B "${REF}" "origin/${REF}"
    else
      git checkout "${REF}"
    fi
  fi

  mkdir -p data sources
  if [[ ! -f .env ]]; then
    echo "=== Création initiale de .env depuis .env.example ==="
    cp .env.example .env
    local sk wt
    sk="$(openssl rand -hex 32)"
    wt="$(openssl rand -hex 32)"
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${sk}/" .env
    sed -i "s/^WORKER_TOKEN=.*/WORKER_TOKEN=${wt}/" .env
    chmod 600 .env
    echo "[IMPORTANT] .env créé avec des secrets générés. Pour tester sans GitHub : AUTH_DEV_MODE=true. En production : PUBLIC_URL, GITHUB_CLIENT_ID/SECRET et ADMIN_GITHUB_LOGINS (voir INSTALL.md)."
  fi

  echo "=== Téléchargement et vérification des sources v1.0.0 ==="
  python3 scripts/fetch_sources.py --extract

  echo "=== Construction et démarrage des conteneurs Docker ==="
  docker compose build
  docker compose up -d --remove-orphans

  # Lire ARENE_PORT et ARENE_BASE_PATH depuis .env
  local port base_path health_url
  port="$(grep -E '^ARENE_PORT=' .env | cut -d= -f2- || true)"
  port="${port:-8001}"
  base_path="$(grep -E '^ARENE_BASE_PATH=' .env | cut -d= -f2- || true)"
  base_path="${base_path:-/arene}"
  if [[ "${base_path}" == "/" ]]; then
    health_url="http://127.0.0.1:${port}/health"
  else
    health_url="http://127.0.0.1:${port}${base_path%/}/health"
  fi

  echo "=== Attente de disponibilité (${health_url}) ==="
  for _ in $(seq 1 30); do
    if curl -fsS "${health_url}" >/dev/null 2>&1; then
      echo "OK : $(curl -fsS "${health_url}")"
      break
    fi
    sleep 2
  done

  echo "=== État des services et ports écoutés ==="
  docker compose ps
  ss -tlnp || true
}

if [[ "${LOCAL_MODE}" -eq 1 ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  deploy_local "$(dirname "${SCRIPT_DIR}")"
  exit 0
fi

if [[ -z "${REMOTE_HOST}" ]]; then
  echo "Usage : $0 <utilisateur@hote-vm> [--ref <branche-ou-tag>] [--dir /opt/automathon-arene]" >&2
  echo "   ou : $0 --local" >&2
  exit 2
fi

if [[ -z "${REPO_URL}" ]]; then
  REPO_URL="$(git config --get remote.origin.url || echo 'https://github.com/antorchn/automathon.git')"
fi

echo "=== Déploiement distant sur ${REMOTE_HOST}:${REMOTE_DIR} (ref=${REF}) ==="
ssh -o StrictHostKeyChecking=accept-new "${REMOTE_HOST}" bash -s -- "${REPO_URL}" "${REMOTE_DIR}" "${REF}" <<'REMOTE_SCRIPT'
set -euo pipefail
REPO_URL="$1"
REMOTE_DIR="$2"
REF="$3"

if [[ ! -d "${REMOTE_DIR}/.git" ]]; then
  sudo mkdir -p "$(dirname "${REMOTE_DIR}")"
  if [[ ! -w "$(dirname "${REMOTE_DIR}")" ]]; then
    sudo mkdir -p "${REMOTE_DIR}"
    sudo chown -R "$(id -u):$(id -g)" "${REMOTE_DIR}"
  fi
  git clone "${REPO_URL}" "${REMOTE_DIR}"
fi

cd "${REMOTE_DIR}"
git fetch origin --tags
if git show-ref --verify --quiet "refs/remotes/origin/${REF}"; then
  git checkout -B "${REF}" "origin/${REF}"
else
  git checkout "${REF}"
fi

./scripts/deploy.sh --local --ref "${REF}"
REMOTE_SCRIPT

echo ""
echo "Déploiement terminé. Pour accéder pendant le dev via tunnel SSH :"
echo "  ssh -L 8000:localhost:8001 ${REMOTE_HOST}"
echo "Puis ouvrir : http://localhost:8000/arene/"
