#!/usr/bin/env bash
# Sauvegarde à chaud de la base SQLite et du dossier data/ (replays + zips).
# Usage : ./scripts/backup.sh [dossier_sauvegarde]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${ROOT_DIR}/data"
BACKUP_ROOT="${1:-${ROOT_DIR}/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_ROOT}/${STAMP}"

mkdir -p "${DEST}"

if [[ -f "${DATA_DIR}/arene.db" ]]; then
  echo "Sauvegarde à chaud de arene.db..."
  sqlite3 "${DATA_DIR}/arene.db" ".backup '${DEST}/arene.db'"
fi

echo "Archivage des fichiers data/ (hors base en direct)..."
tar -czf "${DEST}/data_files.tar.gz" -C "${DATA_DIR}" --exclude='arene.db*' .

echo "Sauvegarde prête dans ${DEST}"
# Rétention locale : garder les 14 dernières sauvegardes
ls -1dt "${BACKUP_ROOT}"/*/ 2>/dev/null | tail -n +15 | xargs -r rm -rf
