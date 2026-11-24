"""Boucle principale du worker Automathon."""

from __future__ import annotations

import os
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from worker.api_client import WorkerAPIClient, extract_agent_to_workdir
from worker.config import WorkerConfig
from worker.referee import MatchLimits, MatchOutcome, play_confrontation, resolve_headless_binary
from worker.sandbox import AgentProcess, DockerAgentProcess


class WorkerDaemon:
    def __init__(
        self,
        config: WorkerConfig,
        api_client: WorkerAPIClient,
        *,
        headless_binary: Path | None = None,
        agent_process_factory: Callable[[Path, str, int], AgentProcess] | None = None,
    ):
        self.config = config
        self.api = api_client
        self.headless_binary = headless_binary or resolve_headless_binary(config.headless_bin)
        self._custom_factory = agent_process_factory
        self._stop_event = threading.Event()
        self.config.agent_work_dir.mkdir(parents=True, exist_ok=True)

    def stop(self) -> None:
        self._stop_event.set()

    def _make_agent_process(self, agent_dir: Path, agent_name: str, memory_mb: int) -> AgentProcess:
        if self._custom_factory is not None:
            return self._custom_factory(agent_dir, agent_name, memory_mb)
        return DockerAgentProcess(
            agent_dir,
            agent_name,
            image=self.config.agent_image,
            work_volume=self.config.agent_work_volume,
            work_dir=self.config.agent_work_dir,
            cgroup_root=self.config.cgroup_root,
            memory_mb=memory_mb,
        )

    def _heartbeat_loop(self, job_id: str, stop_hb: threading.Event) -> None:
        while not stop_hb.wait(timeout=30.0):
            try:
                self.api.heartbeat(job_id, self.config.worker_id)
            except Exception as exc:
                print(f"[worker] heartbeat échoué pour {job_id}: {exc}", file=sys.stderr)

    def _run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        job_type = job["type"]
        limits_raw = job["limits"]
        limits = MatchLimits(
            decision_mean_ms_limit=float(limits_raw["decision_mean_ms_limit"]),
            decision_timeout_ms=int(limits_raw["decision_timeout_ms"]),
            max_exceptions_per_game=int(limits_raw["max_exceptions_per_game"]),
            agent_load_timeout_s=int(limits_raw["agent_load_timeout_s"]),
            agent_memory_mb=int(limits_raw["agent_memory_mb"]),
            max_frames=int(limits_raw["max_frames"]),
        )
        game_version = str(job.get("game_version") or "1.0.0")
        agents_meta: dict[str, dict[str, str]] = job["agents"]
        games: list[dict[str, Any]] = job["games"]

        job_work_dir = self.config.agent_work_dir / job_id
        stop_hb = threading.Event()
        hb_thread = threading.Thread(target=self._heartbeat_loop, args=(job_id, stop_hb), daemon=True)
        hb_thread.start()

        try:
            # 1. Télécharger / mettre en cache les zips par sha256
            cached_zips: dict[str, Path] = {}
            for aid, meta in agents_meta.items():
                cached_zips[aid] = self.api.ensure_agent_zip(meta["zip_url"], meta["sha256"])

            # 2. Validation explicite du chargement pour un job de validation
            if job_type == "validation":
                focus_id = next(iter(g["left_agent_id"] for g in games if agents_meta[g["left_agent_id"]]["name"] != "dummy_bot"), None)
                if focus_id is None:
                    focus_id = games[0]["left_agent_id"]
                load_dir = job_work_dir / "load_check" / focus_id
                extract_agent_to_workdir(cached_zips[focus_id], agents_meta[focus_id]["name"], load_dir)
                proc = self._make_agent_process(load_dir, agents_meta[focus_id]["name"], limits.agent_memory_mb)
                try:
                    ok, load_err = proc.start(limits.agent_load_timeout_s)
                finally:
                    proc.stop()
                if not ok:
                    self.api.submit_result(
                        job_id,
                        {
                            "worker_id": self.config.worker_id,
                            "validation_ok": False,
                            "validation_error": f"Échec de chargement : {load_err}",
                            "games": [],
                        },
                    )
                    return

            # 3. Jouer les parties en parallèle (2 par défaut)
            def _slot_factory(agent_id: str, slot_idx: int) -> AgentProcess:
                ag_dir = job_work_dir / f"slot_{slot_idx}" / agent_id
                extract_agent_to_workdir(cached_zips[agent_id], agents_meta[agent_id]["name"], ag_dir)
                return self._make_agent_process(ag_dir, agents_meta[agent_id]["name"], limits.agent_memory_mb)

            outcomes: list[MatchOutcome] = play_confrontation(
                games,
                {aid: meta["name"] for aid, meta in agents_meta.items()},
                _slot_factory,
                headless_binary=self.headless_binary,
                limits=limits,
                parallel_matches=self.config.parallel_matches,
                game_version=game_version,
            )

            # 4. Envoyer les replays (pour placement et affinage) et soumettre les résultats
            if job_type != "validation":
                for res in outcomes:
                    if res.replay_gzip:
                        self.api.upload_replay(job_id, res.index, res.replay_gzip)

            validation_ok = True
            validation_error = ""
            if job_type == "validation":
                for res in outcomes:
                    spec_g = next(g for g in games if g["index"] == res.index)
                    cand_side = "left" if agents_meta[spec_g["left_agent_id"]]["name"] != "dummy_bot" else "right"
                    if res.failure_side in (cand_side, "both"):
                        validation_ok = False
                        validation_error = f"Échec en partie de validation : {res.failure_reason}"
                        break

            self.api.submit_result(
                job_id,
                {
                    "worker_id": self.config.worker_id,
                    "validation_ok": validation_ok if job_type == "validation" else None,
                    "validation_error": validation_error if (job_type == "validation" and not validation_ok) else None,
                    "games": [
                        {
                            "index": r.index,
                            "outcome": r.outcome,
                            "end_frame": r.end_frame,
                            "left_health": r.left_health,
                            "right_health": r.right_health,
                            "left_mean_ms": r.left_mean_ms,
                            "left_max_ms": r.left_max_ms,
                            "right_mean_ms": r.right_mean_ms,
                            "right_max_ms": r.right_max_ms,
                            "left_exceptions": r.left_exceptions,
                            "right_exceptions": r.right_exceptions,
                            "failure_side": r.failure_side,
                            "failure_reason": r.failure_reason,
                        }
                        for r in outcomes
                    ],
                },
            )
        except Exception as exc:
            try:
                self.api.fail_job(job_id, self.config.worker_id, str(exc))
            except Exception as fail_exc:
                print(f"[worker] impossible de signaler l'échec du job {job_id}: {fail_exc}", file=sys.stderr)
            raise
        finally:
            stop_hb.set()
            hb_thread.join(timeout=2.0)
            shutil.rmtree(job_work_dir, ignore_errors=True)

    def run_once(self) -> bool:
        """Tente de réserver et traiter un job. Retourne True si un job a été traité."""
        job = self.api.claim(self.config.worker_id)
        if job is None:
            return False
        self._run_job(job)
        return True

    def run_forever(self) -> None:
        if hasattr(os, "nice") and self.config.nice != 0:
            try:
                os.nice(self.config.nice)
            except OSError as exc:
                print(f"[worker] nice({self.config.nice}) ignoré : {exc}", file=sys.stderr)

        print(f"[worker] démarré ({self.config.worker_id}) -> {self.config.site_url}", flush=True)
        while not self._stop_event.is_set():
            try:
                worked = self.run_once()
                if not worked:
                    self._stop_event.wait(self.config.poll_interval_s)
            except Exception as exc:
                print(f"[worker] erreur boucle : {exc}", file=sys.stderr)
                self._stop_event.wait(self.config.poll_interval_s)


def main() -> int:
    config = WorkerConfig.from_env()
    client = WorkerAPIClient(config.site_url, config.worker_token, config.cache_dir)
    daemon = WorkerDaemon(config, client)

    def _handle_sig(_signum, _frame):
        daemon.stop()

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    try:
        daemon.run_forever()
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
