#!/usr/bin/env bash
# Prépare la VM Debian 13 (vr-4v-4g) selon la section « Préparer et exposer la VM » de la spec.
# Usage (sur la VM avec sudo) :
#   sudo ./scripts/setup_vm.sh [--ssh-port 22] [--mode dev|subdomain|proxy] [--domain arene.exemple.fr] [--proxy-ip 10.0.0.2]
set -euo pipefail

SSH_PORT=22
MODE="dev"
DOMAIN=""
PROXY_IP=""
ARENE_PORT="${ARENE_PORT:-8001}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh-port) SSH_PORT="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --proxy-ip) PROXY_IP="$2"; shift 2 ;;
    --arene-port) ARENE_PORT="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,5p' "$0"
      exit 0
      ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Ce script doit être lancé en root (sudo)." >&2
  exit 1
fi

echo "=== 1. Installation de Docker Engine et Compose v2 (dépôt officiel Docker) ==="
apt-get update
apt-get install -y ca-certificates curl gnupg git python3 python3-httpx sqlite3 ufw openssl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
# shellcheck source=/dev/null
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
  usermod -aG docker "${SUDO_USER}"
  echo "${SUDO_USER} ajouté au groupe docker : se déconnecter puis se reconnecter pour lancer deploy.sh sans sudo."
fi

echo "=== 2. Swap de 2 Go (les conteneurs d'agents n'y ont pas accès) ==="
if ! swapon --show | grep -q '/swapfile'; then
  if [[ ! -f /swapfile ]]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
fi
if ! grep -q '^/swapfile ' /etc/fstab; then
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "=== 3. Vérifications système (cgroup v2, Compose v2, RAM/swap) ==="
CGROUP_TYPE="$(stat -fc %T /sys/fs/cgroup)"
echo "cgroupfs = ${CGROUP_TYPE}"
if [[ "${CGROUP_TYPE}" != "cgroup2fs" ]]; then
  echo "ERREUR : cgroups v2 requis (trouvé : ${CGROUP_TYPE})." >&2
  exit 1
fi
docker compose version
free -h

echo "=== 4. Test d'isolation des agents ==="
OUT="$(docker run --rm --network none --read-only --tmpfs /tmp:size=100m --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --pids-limit 64 --memory 768m --memory-swap 768m --cpus 1 python:3.12-slim python -c "print('ok')")"
echo "Isolation test : ${OUT}"
if [[ "${OUT}" != "ok" ]]; then
  echo "ERREUR : le test d'isolation n'a pas renvoyé 'ok'." >&2
  exit 1
fi

echo "=== 5. Pare-feu UFW (SSH autorisé avant activation) ==="
ufw default deny incoming
ufw default allow outgoing
ufw allow "${SSH_PORT}/tcp"

if [[ "${MODE}" == "subdomain" ]]; then
  if [[ -z "${DOMAIN}" ]]; then
    echo "ERREUR : --domain requis pour --mode subdomain" >&2
    exit 2
  fi
  apt-get install -y caddy
  cat > /etc/caddy/Caddyfile <<EOF
${DOMAIN} {
    request_body {
        max_size 60MB
    }
    reverse_proxy 127.0.0.1:${ARENE_PORT}
}
EOF
  ufw allow 80,443/tcp
  systemctl enable --now caddy
  systemctl reload caddy
elif [[ "${MODE}" == "proxy" ]]; then
  if [[ -z "${PROXY_IP}" ]]; then
    echo "ERREUR : --proxy-ip requis pour --mode proxy" >&2
    exit 2
  fi
  apt-get install -y caddy
  cat > /etc/caddy/Caddyfile <<EOF
http://:8000 {
    request_body {
        max_size 60MB
    }
    reverse_proxy 127.0.0.1:${ARENE_PORT} {
        trusted_proxies ${PROXY_IP}
    }
}
EOF
  ufw allow from "${PROXY_IP}" to any port 8000 proto tcp
  systemctl enable --now caddy
  systemctl reload caddy
fi

ufw --force enable
ufw status verbose
echo "=== VM prête (mode=${MODE}) ==="
