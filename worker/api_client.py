"""Client HTTP du worker et cache des archives d'agents par sha256."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


class WorkerAPIClient:
    def __init__(self, site_url: str, worker_token: str, cache_dir: Path, transport: httpx.BaseTransport | None = None):
        self.site_url = site_url.rstrip("/")
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            base_url=self.site_url,
            headers={"Authorization": f"Bearer {worker_token}"},
            timeout=60.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def claim(self, worker_id: str) -> dict[str, Any] | None:
        resp = self._client.post("/api/worker/claim", json={"worker_id": worker_id})
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        return resp.json()

    def heartbeat(self, job_id: str, worker_id: str) -> None:
        resp = self._client.post(f"/api/worker/jobs/{job_id}/heartbeat", json={"worker_id": worker_id})
        resp.raise_for_status()

    def upload_replay(self, job_id: str, game_index: int, gzip_bytes: bytes) -> None:
        resp = self._client.post(
            f"/api/worker/jobs/{job_id}/replays/{game_index}",
            content=gzip_bytes,
            headers={"Content-Type": "application/gzip"},
        )
        resp.raise_for_status()

    def submit_result(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(f"/api/worker/jobs/{job_id}/result", json=payload)
        resp.raise_for_status()
        return resp.json()

    def fail_job(self, job_id: str, worker_id: str, reason: str) -> None:
        resp = self._client.post(
            f"/api/worker/jobs/{job_id}/fail",
            json={"worker_id": worker_id, "reason": reason},
        )
        resp.raise_for_status()

    def _relative_to_site(self, url: str) -> str:
        """Chemin à demander au client HTTP, dont base_url contient déjà le sous-chemin du site.

        Le site donne ses URL avec son sous-chemin (ARENE_BASE_PATH, ex. /automathon-rlcs/api/worker/...) : on le
        retire pour ne pas le doubler. Une URL absolue (http://...) est gardée telle quelle.
        """
        if url.startswith(("http://", "https://")):
            return url
        base_path = urlsplit(self.site_url).path.rstrip("/")
        if base_path and (url == base_path or url.startswith(base_path + "/")):
            return url[len(base_path):] or "/"
        return url

    def ensure_agent_zip(self, zip_url: str, expected_sha256: str) -> Path:
        cached = self.cache_dir / f"{expected_sha256}.zip"
        if cached.is_file():
            if hashlib.sha256(cached.read_bytes()).hexdigest() == expected_sha256:
                return cached
            cached.unlink()

        resp = self._client.get(self._relative_to_site(zip_url))
        resp.raise_for_status()
        data = resp.content
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            raise RuntimeError(f"SHA-256 invalide pour {zip_url}: {actual} != {expected_sha256}")

        fd, tmp_name = tempfile.mkstemp(dir=self.cache_dir, prefix=".tmp_")
        tmp_path = Path(tmp_name)
        try:
            import os

            with os.fdopen(fd, "wb") as f:
                f.write(data)
            tmp_path.replace(cached)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return cached


def extract_agent_to_workdir(zip_path: Path, agent_name: str, target_dir: Path) -> Path:
    """Extrait `<agent_name>/*` directement dans `target_dir` (monté en lecture seule dans /agent)."""
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        prefix = f"{agent_name}/"
        for info in zf.infolist():
            norm = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            if norm.startswith(prefix):
                rel = norm[len(prefix):]
            else:
                rel = norm.split("/", 1)[-1] if "/" in norm else norm
            if not rel or ".." in Path(rel).parts:
                continue
            dest = (target_dir / rel).resolve()
            if dest != target_dir.resolve() and target_dir.resolve() not in dest.parents:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, dest.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return target_dir
