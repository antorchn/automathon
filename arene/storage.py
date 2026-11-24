"""Couche d'abstraction de stockage de fichiers (système local en V0, S3 ensuite)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol


class FileStore(Protocol):
    def put_bytes(self, key: str, data: bytes) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def get_path(self, key: str) -> Path | None: ...


class LocalFileStore:
    """Stockage atomique dans DATA_DIR avec protection contre la traversée de chemin."""

    def __init__(self, root_dir: Path | str):
        self.root = Path(root_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_key(self, key: str) -> Path:
        clean = key.lstrip("/").replace("\\", "/")
        if not clean or ".." in Path(clean).parts:
            raise ValueError(f"Clé de stockage invalide : {key!r}")
        target = (self.root / clean).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"Clé hors du dossier de stockage : {key!r}")
        return target

    def put_bytes(self, key: str, data: bytes) -> None:
        target = self._resolve_key(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp_")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            tmp_path.replace(target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def get_bytes(self, key: str) -> bytes:
        target = self._resolve_key(key)
        if not target.is_file():
            raise FileNotFoundError(key)
        return target.read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self._resolve_key(key).is_file()
        except ValueError:
            return False

    def delete(self, key: str) -> None:
        try:
            target = self._resolve_key(key)
        except ValueError:
            return
        target.unlink(missing_ok=True)

    def get_path(self, key: str) -> Path | None:
        target = self._resolve_key(key)
        return target if target.is_file() else None


class S3FileStore:
    """Point d'extension prévu après la V0 pour basculer vers un stockage S3."""

    def __init__(self, bucket: str, prefix: str = ""):
        self.bucket = bucket
        self.prefix = prefix
        raise NotImplementedError("S3FileStore n'est pas activé en V0 (utiliser LocalFileStore).")
