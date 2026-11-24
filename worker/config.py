"""Configuration du worker (les 4 variables d'environnement de la spec + chemins internes)."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkerConfig:
    site_url: str
    worker_token: str
    parallel_matches: int
    nice: int
    worker_id: str
    agent_image: str
    agent_work_volume: str
    agent_work_dir: Path
    cgroup_root: Path
    headless_bin: str | None
    cache_dir: Path
    poll_interval_s: float = 2.0

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        # Sur la VM de l'arène, le site est joint par le réseau interne de Compose, sous le même sous-chemin ;
        # SITE_URL ne sert qu'à un worker installé sur une autre machine (adresse publique de l'arène).
        base_path = os.environ.get("ARENE_BASE_PATH", "/arene").strip().strip("/")
        default_site = "http://arene-web:8000" + (f"/{base_path}" if base_path else "")
        site_url = (os.environ.get("SITE_URL") or default_site).strip().rstrip("/")
        token = os.environ.get("WORKER_TOKEN", "").strip()
        if not token:
            raise RuntimeError("WORKER_TOKEN est obligatoire pour démarrer le worker.")
        parallel = max(1, int(os.environ.get("WORKER_PARALLEL_MATCHES", "2")))
        nice = int(os.environ.get("WORKER_NICE", "10"))
        worker_id = os.environ.get("WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
        agent_image = os.environ.get("AGENT_IMAGE", "automathon-agent:1.0.0")
        agent_work_volume = os.environ.get("AGENT_WORK_VOLUME", "arene_agent_work")
        agent_work_dir = Path(os.environ.get("AGENT_WORK_DIR", "/agent_work"))
        cgroup_root = Path(os.environ.get("CGROUP_ROOT", "/sys/fs/cgroup"))
        headless_bin = os.environ.get("AUTOMATHON_HEADLESS_PATH") or os.environ.get("HEADLESS_BIN")
        cache_dir = Path(os.environ.get("CACHE_DIR", "/var/cache/arene_worker"))
        poll_interval_s = float(os.environ.get("WORKER_POLL_INTERVAL_S", "2.0"))
        return cls(
            site_url=site_url,
            worker_token=token,
            parallel_matches=parallel,
            nice=nice,
            worker_id=worker_id,
            agent_image=agent_image,
            agent_work_volume=agent_work_volume,
            agent_work_dir=agent_work_dir,
            cgroup_root=cgroup_root,
            headless_bin=headless_bin,
            cache_dir=cache_dir,
            poll_interval_s=poll_interval_s,
        )
