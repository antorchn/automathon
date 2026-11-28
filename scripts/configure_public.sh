#!/usr/bin/env bash
# Ouvre l'arène au public, derrière le reverse proxy du site de l'association (ou sur un sous-domaine).
#
# Usage (sur la VM de l'arène, dans /opt/automathon-arene) :
#   ./scripts/configure_public.sh --url https://automatants.cs-campus.fr/automathon-rlcs \
#       --proxy-ip <IP du reverse proxy du site> --github-client-id <Client ID> --admins <login1[,login2]>
# Le proxy du site joint la VM en HTTP sur le port 80 (--proxy-port pour un autre port), ouvert à sa seule adresse.
# Variante sous-domaine, sans proxy du site (Caddy gère HTTPS sur la VM) :
#   ./scripts/configure_public.sh --url https://arene.exemple.fr --subdomain --github-client-id <ID> --admins <login>
#
# Le secret de l'OAuth App GitHub est demandé au clavier (ou lu dans la variable GITHUB_CLIENT_SECRET) : il ne passe
# ni par la ligne de commande ni par l'historique du shell. Le script sauvegarde .env, le met à jour, prépare Caddy et
# le pare-feu (scripts/setup_vm.sh, avec sudo), redéploie, puis vérifie que l'arène répond.
set -euo pipefail

URL=""
PROXY_IP=""
PROXY_PORT=80
CLIENT_ID=""
ADMINS=""
SUBDOMAIN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --proxy-ip) PROXY_IP="$2"; shift 2 ;;
    --proxy-port) PROXY_PORT="$2"; shift 2 ;;
    --github-client-id) CLIENT_ID="$2"; shift 2 ;;
    --admins) ADMINS="$2"; shift 2 ;;
    --subdomain) SUBDOMAIN=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done

fail() { echo "ERREUR : $*" >&2; exit 2; }

[[ "${URL}" == https://* ]] || fail "--url doit être l'adresse publique en https:// (ex. https://automatants.cs-campus.fr/automathon-rlcs)."
[[ -n "${CLIENT_ID}" ]] || fail "--github-client-id est requis (OAuth App GitHub, voir INSTALL.md section 11)."
[[ -n "${ADMINS}" ]] || fail "--admins est requis (vos logins GitHub, séparés par des virgules)."

read -r HOST BASE_PATH < <(python3 - "${URL}" <<'EOF'
import sys
from urllib.parse import urlsplit
parts = urlsplit(sys.argv[1])
print(parts.netloc, parts.path.rstrip("/") or "/")
EOF
)
BASE_PATH="${BASE_PATH%$'\r'}"

if [[ "${SUBDOMAIN}" -eq 0 ]]; then
  [[ -n "${PROXY_IP}" ]] || fail "--proxy-ip est requis derrière le site de l'association (ou --subdomain)."
  python3 -c 'import ipaddress, sys; ipaddress.ip_address(sys.argv[1])' "${PROXY_IP}" 2>/dev/null \
    || fail "--proxy-ip n'est pas une adresse IP : ${PROXY_IP}"
  [[ "${BASE_PATH}" != "/" ]] || fail "derrière le site de l'association, l'URL doit avoir un sous-chemin (ex. /automathon-rlcs)."
  TRUSTED="127.0.0.1,172.16.0.0/12,${PROXY_IP}"
else
  [[ "${BASE_PATH}" == "/" ]] || fail "en sous-domaine, l'URL ne doit pas avoir de sous-chemin (ex. https://arene.exemple.fr)."
  TRUSTED="127.0.0.1,172.16.0.0/12"
fi

SECRET="${GITHUB_CLIENT_SECRET:-}"
if [[ -z "${SECRET}" ]]; then
  read -rsp "Secret de l'OAuth App GitHub (Client secret) : " SECRET
  echo
fi
[[ -n "${SECRET}" ]] || fail "secret GitHub vide."

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -f .env ]]; then
  cp .env.example .env
  sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$(openssl rand -hex 32)/; s/^WORKER_TOKEN=.*/WORKER_TOKEN=$(openssl rand -hex 32)/" .env
fi
BACKUP=".env.bak-$(date +%Y%m%d-%H%M%S)"
cp .env "${BACKUP}"
chmod 600 .env "${BACKUP}"
echo "=== .env sauvegardé dans ${BACKUP}"

# Mise à jour de .env (en Python : aucun souci d'échappement des valeurs). SITE_URL est retiré : sur cette VM, le
# worker joint le site par le réseau interne, sous ARENE_BASE_PATH.
GITHUB_CLIENT_SECRET="${SECRET}" python3 - .env "${URL}" "${BASE_PATH}" "${CLIENT_ID}" "${ADMINS}" "${TRUSTED}" <<'EOF'
import os
import sys
from pathlib import Path

path, url, base_path, client_id, admins, trusted = sys.argv[1:]
values = {
    "PUBLIC_URL": url.rstrip("/"),
    "ARENE_BASE_PATH": base_path,
    "AUTH_DEV_MODE": "false",
    "GITHUB_CLIENT_ID": client_id,
    "GITHUB_CLIENT_SECRET": os.environ["GITHUB_CLIENT_SECRET"],
    "ADMIN_GITHUB_LOGINS": admins,
    "TRUSTED_PROXY": trusted,
}
lines, seen = [], set()
for line in Path(path).read_text(encoding="utf-8").splitlines():
    key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
    if key == "SITE_URL":
        lines.append("# " + line + "  # retiré par configure_public.sh : déduit de ARENE_BASE_PATH")
        continue
    if key in values:
        lines.append(f"{key}={values[key]}")
        seen.add(key)
    else:
        lines.append(line)
lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
EOF
unset SECRET
echo "=== .env mis à jour : PUBLIC_URL=${URL%/}, ARENE_BASE_PATH=${BASE_PATH}, connexion GitHub, TRUSTED_PROXY=${TRUSTED}"

echo "=== Caddy et pare-feu (sudo)"
if [[ "${SUBDOMAIN}" -eq 0 ]]; then
  sudo ./scripts/setup_vm.sh --mode proxy --proxy-ip "${PROXY_IP}" --proxy-port "${PROXY_PORT}"
else
  sudo ./scripts/setup_vm.sh --mode subdomain --domain "${HOST}"
fi

echo "=== Redéploiement"
./scripts/deploy.sh --local

HEALTH_PATH="$([[ "${BASE_PATH}" == "/" ]] && echo "" || echo "${BASE_PATH}")/health"
echo "=== Vérifications"
curl -fsS "http://127.0.0.1:${ARENE_PORT:-8001}${HEALTH_PATH}" && echo "  <- l'arène répond sur la VM"
if [[ "${SUBDOMAIN}" -eq 0 ]]; then
  curl -fsS "http://127.0.0.1:${PROXY_PORT}${HEALTH_PATH}" && echo "  <- Caddy la transmet sur le port ${PROXY_PORT}"
  VM_IP="$(hostname -I | awk '{print $1}')"
  PORT_SUFFIX="$([[ "${PROXY_PORT}" == "80" ]] && echo "" || echo ":${PROXY_PORT}")"
  cat <<MSG

Prochaine étape, côté site de l'association (INTEGRATION_SITE.md) :
  ${URL%/}/  ->  http://${VM_IP}${PORT_SUFFIX}${BASE_PATH}/  (sans retirer le préfixe ${BASE_PATH})
Test depuis le serveur du site : curl -sS http://${VM_IP}${PORT_SUFFIX}${HEALTH_PATH}
Puis vérifier : curl -sS ${URL%/}/health
MSG
else
  echo "Vérifier depuis l'extérieur : curl -sS ${URL%/}/health (le DNS de ${HOST} doit pointer sur cette VM)."
fi
