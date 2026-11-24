"""Contrôles de réception d'un zip d'agent, sans jamais exécuter son code."""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import stat
import zipfile

_VALID_AGENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


class ZipValidationError(ValueError):
    """Archive non conforme aux règles de réception."""


def validate_agent_zip(data: bytes, max_mb: int = 50) -> tuple[str, str]:
    """Vérifie le zip d'un participant et retourne (nom_agent, sha256_hex).

    Règles de la spec :
    - zip lisible, taille <= SUBMISSION_MAX_MB ;
    - aucun chemin absolu ni '..' ni lien symbolique ;
    - un seul dossier à la racine ;
    - `<nom>/agent.py` présent.
    """
    if not data:
        raise ZipValidationError("Le fichier zip envoyé est vide.")
    max_bytes = max_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise ZipValidationError(f"L'archive dépasse la limite de {max_mb} Mo.")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise ZipValidationError("Archive zip illisible ou corrompue.") from exc

    with zf:
        infos = zf.infolist()
        if not infos:
            raise ZipValidationError("L'archive zip ne contient aucun fichier.")

        top_dirs: set[str] = set()
        has_agent_py = False
        total_uncompressed = 0

        for info in infos:
            raw_name = info.filename.replace("\\", "/")
            if not raw_name:
                continue
            if raw_name.startswith("/") or (len(raw_name) >= 2 and raw_name[1] == ":"):
                raise ZipValidationError(f"Chemin absolu interdit dans le zip : {info.filename}")

            parts = [p for p in raw_name.split("/") if p not in ("", ".")]
            if not parts:
                continue
            if ".." in parts:
                raise ZipValidationError(f"Traversée de chemin ('..') interdite : {info.filename}")

            # Lien symbolique Unix (S_IFLNK = 0o120000)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ZipValidationError(f"Lien symbolique interdit dans le zip : {info.filename}")

            norm = posixpath.normpath(raw_name)
            if norm.startswith("..") or norm.startswith("/"):
                raise ZipValidationError(f"Chemin invalide dans le zip : {info.filename}")

            # Ignorer les métadonnées macOS si présentes à l'intérieur d'un dossier, mais interdire un deuxième dossier racine
            top = parts[0]
            top_dirs.add(top)

            if len(parts) == 1 and not info.is_dir():
                raise ZipValidationError(
                    f"Fichier à la racine interdit ({info.filename}) : tout doit être dans un unique dossier <nom>/."
                )

            if len(parts) == 2 and parts[1] == "agent.py" and not info.is_dir():
                has_agent_py = True

            total_uncompressed += info.file_size
            if total_uncompressed > max_bytes * 4:
                raise ZipValidationError("Taille décompressée excessive (bombe zip suspectée).")

        if len(top_dirs) != 1:
            raise ZipValidationError(
                f"Le zip doit contenir exactement un dossier à la racine (trouvé : {', '.join(sorted(top_dirs))})."
            )

        agent_name = next(iter(top_dirs))
        if not _VALID_AGENT_NAME.match(agent_name):
            raise ZipValidationError(
                f"Nom de dossier agent invalide ({agent_name!r}) : lettres, chiffres, '-' et '_' uniquement."
            )
        if not has_agent_py:
            raise ZipValidationError(f"Fichier obligatoire '{agent_name}/agent.py' introuvable dans le zip.")

    sha256_hex = hashlib.sha256(data).hexdigest()
    return agent_name, sha256_hex
