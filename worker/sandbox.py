"""Exécution isolée des agents dans Docker avec mesure du temps CPU par cgroup v2."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_PYTHON = REPO_ROOT / "PythonAI"
if str(KIT_PYTHON) not in sys.path:
    sys.path.insert(0, str(KIT_PYTHON))

from bridge.datatypes import AIAction  # noqa: E402


@dataclass
class DecisionResult:
    action: AIAction | None
    timed_out: bool = False
    error: str | None = None
    cpu_ms: float = 0.0
    fatal: bool = False
    fatal_reason: str = ""


class AgentProcess(Protocol):
    def start(self, timeout_s: float) -> tuple[bool, str]: ...
    def reset(self, timeout_s: float) -> tuple[bool, str]: ...
    def decide(self, decision_id: int, state_dict: dict[str, Any], timeout_ms: int) -> DecisionResult: ...
    def stop(self) -> None: ...
    def is_alive(self) -> bool: ...
    def was_oom_killed(self) -> bool: ...


def parse_cpu_stat_usec(text: str) -> int | None:
    """Extrait usage_usec depuis le contenu d'un fichier cgroup v2 cpu.stat."""
    for line in text.splitlines():
        if line.startswith("usage_usec "):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def find_container_cpu_stat(cgroup_root: Path, container_id: str) -> Path | None:
    """Localise le fichier cpu.stat cgroup v2 d'un conteneur Docker."""
    if not container_id or not cgroup_root.is_dir():
        return None
    candidates = [
        cgroup_root / "system.slice" / f"docker-{container_id}.scope" / "cpu.stat",
        cgroup_root / "docker" / container_id / "cpu.stat",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    # Recherche générique (ex: driver cgroupfs ou systemd imbriqué)
    short_id = container_id[:12]
    for path in cgroup_root.rglob("cpu.stat"):
        if short_id in path.parent.name:
            return path
    return None


class _PipeAgentBase:
    """Logique partagée du protocole JSON-line sur stdin/stdout avec suivi non bloquant."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.proc: subprocess.Popen | None = None
        self._msg_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._in_flight_id: int | None = None
        self._last_cpu_usec: int | None = None

    def _read_cpu_usec(self) -> int | None:
        return None

    def _spawn_process(self) -> subprocess.Popen:
        raise NotImplementedError

    def _start_readers(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None and proc.stderr is not None

        def _read_out():
            try:
                for line in proc.stdout:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        self._msg_queue.put(json.loads(s))
                    except json.JSONDecodeError:
                        continue
            finally:
                self._msg_queue.put(None)

        def _read_err():
            for line in proc.stderr:
                if len(self._stderr_lines) < 50:
                    self._stderr_lines.append(line.rstrip("\r\n"))

        self._reader_thread = threading.Thread(target=_read_out, daemon=True)
        self._stderr_thread = threading.Thread(target=_read_err, daemon=True)
        self._reader_thread.start()
        self._stderr_thread.start()

    def _send_line(self, payload: dict[str, Any]) -> bool:
        if self.proc is None or self.proc.stdin is None or self.proc.poll() is not None:
            return False
        try:
            self.proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            self.proc.stdin.flush()
            return True
        except OSError:
            return False

    def _wait_cmd(self, expected_cmd: str, timeout_s: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_s
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                return None
            try:
                msg = self._msg_queue.get(timeout=rem)
            except queue.Empty:
                return None
            if msg is None:
                return None
            if msg.get("cmd") == expected_cmd:
                return msg

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def was_oom_killed(self) -> bool:
        return False

    def _fatal_reason(self, default: str) -> str:
        if self.was_oom_killed():
            return "oom"
        tail = " | ".join(self._stderr_lines[-5:])
        return f"{default}: {tail[:300]}" if tail else default

    def start(self, timeout_s: float) -> tuple[bool, str]:
        self.proc = self._spawn_process()
        self._start_readers(self.proc)
        self._on_process_started()
        if not self._send_line({"cmd": "init"}):
            return False, self._fatal_reason("crash au démarrage")
        resp = self._wait_cmd("init", timeout_s)
        if resp is None:
            if not self.is_alive():
                return False, self._fatal_reason("crash au chargement")
            return False, f"délai dépassé au chargement (> {timeout_s:.0f}s)"
        if not resp.get("ok"):
            return False, str(resp.get("error") or "échec init")
        return True, ""

    def _on_process_started(self) -> None:
        pass

    def reset(self, timeout_s: float) -> tuple[bool, str]:
        self._in_flight_id = None
        while not self._msg_queue.empty():
            try:
                self._msg_queue.get_nowait()
            except queue.Empty:
                break
        if not self.is_alive():
            return False, self._fatal_reason("conteneur mort")
        if not self._send_line({"cmd": "reset"}):
            return False, self._fatal_reason("crash au reset")
        resp = self._wait_cmd("reset", timeout_s)
        if resp is None:
            return False, self._fatal_reason("timeout au reset")
        if not resp.get("ok"):
            return False, str(resp.get("error") or "échec reset")
        return True, ""

    def decide(self, decision_id: int, state_dict: dict[str, Any], timeout_ms: int) -> DecisionResult:
        if not self.is_alive():
            reason = "oom" if self.was_oom_killed() else "crash"
            return DecisionResult(action=None, fatal=True, fatal_reason=reason)

        # Si une requête précédente est encore bloquée (ex: boucle infinie), on vérifie sans bloquer
        # si elle vient de répondre ; sinon on garde immédiatement l'action précédente.
        if self._in_flight_id is not None:
            while True:
                try:
                    msg = self._msg_queue.get_nowait()
                except queue.Empty:
                    break
                if msg is None:
                    reason = "oom" if self.was_oom_killed() else "crash"
                    return DecisionResult(action=None, fatal=True, fatal_reason=reason)
                if msg.get("cmd") == "decide" and msg.get("decision_id") == self._in_flight_id:
                    self._in_flight_id = None
                    break

            if self._in_flight_id is not None:
                cpu_now = self._read_cpu_usec()
                cpu_ms = 0.0
                if cpu_now is not None and self._last_cpu_usec is not None:
                    cpu_ms = max(0.0, (cpu_now - self._last_cpu_usec) / 1000.0)
                    self._last_cpu_usec = cpu_now
                return DecisionResult(action=None, timed_out=True, cpu_ms=cpu_ms)

        cpu_start = self._read_cpu_usec()
        wall_start = time.perf_counter()

        if not self._send_line({"cmd": "decide", "decision_id": decision_id, "state": state_dict}):
            reason = "oom" if self.was_oom_killed() else "crash"
            return DecisionResult(action=None, fatal=True, fatal_reason=reason)

        self._in_flight_id = decision_id
        deadline = wall_start + (timeout_ms / 1000.0)

        while True:
            rem = deadline - time.perf_counter()
            if rem <= 0:
                cpu_end = self._read_cpu_usec()
                self._last_cpu_usec = cpu_end
                cpu_ms = (
                    max(0.0, (cpu_end - cpu_start) / 1000.0)
                    if (cpu_start is not None and cpu_end is not None)
                    else (time.perf_counter() - wall_start) * 1000.0
                )
                return DecisionResult(action=None, timed_out=True, cpu_ms=cpu_ms)
            try:
                msg = self._msg_queue.get(timeout=rem)
            except queue.Empty:
                cpu_end = self._read_cpu_usec()
                self._last_cpu_usec = cpu_end
                cpu_ms = (
                    max(0.0, (cpu_end - cpu_start) / 1000.0)
                    if (cpu_start is not None and cpu_end is not None)
                    else (time.perf_counter() - wall_start) * 1000.0
                )
                return DecisionResult(action=None, timed_out=True, cpu_ms=cpu_ms)

            if msg is None:
                reason = "oom" if self.was_oom_killed() else "crash"
                return DecisionResult(action=None, fatal=True, fatal_reason=reason)
            if msg.get("cmd") != "decide":
                continue
            # Réponse arrivée en retard pour une ancienne décision -> ignorée
            if msg.get("decision_id") != decision_id:
                continue

            self._in_flight_id = None
            cpu_end = self._read_cpu_usec()
            cpu_ms = (
                max(0.0, (cpu_end - cpu_start) / 1000.0)
                if (cpu_start is not None and cpu_end is not None)
                else (time.perf_counter() - wall_start) * 1000.0
            )
            if "error" in msg:
                return DecisionResult(action=AIAction(), error=str(msg["error"]), cpu_ms=cpu_ms)
            try:
                action = AIAction.model_validate(msg["action"])
            except Exception as exc:
                return DecisionResult(action=AIAction(), error=f"Action invalide : {exc}", cpu_ms=cpu_ms)
            return DecisionResult(action=action, cpu_ms=cpu_ms)

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.poll() is None:
                self._send_line({"cmd": "quit"})
                try:
                    self.proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2.0)
        except Exception:
            pass
        finally:
            self.proc = None


class DockerAgentProcess(_PipeAgentBase):
    """Lance l'agent dans un conteneur Docker isolé selon la spec."""

    def __init__(
        self,
        agent_dir: Path,
        agent_name: str,
        *,
        image: str,
        work_volume: str,
        work_dir: Path,
        cgroup_root: Path,
        memory_mb: int = 768,
    ):
        super().__init__(agent_name)
        self.agent_dir = agent_dir.resolve()
        self.image = image
        self.work_volume = work_volume
        self.work_dir = work_dir.resolve()
        self.cgroup_root = cgroup_root
        self.memory_mb = int(memory_mb)
        self.container_name = f"automathon-agent-{uuid.uuid4().hex[:12]}"
        self._container_id: str | None = None
        self._cpu_stat_path: Path | None = None
        self._oom_cached: bool | None = None

    def _spawn_process(self) -> subprocess.Popen:
        subpath = self.agent_dir.relative_to(self.work_dir).as_posix()
        mem = f"{self.memory_mb}m"
        cmd = [
            "docker", "run", "-i",
            "--name", self.container_name,
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:size=100m",
            "--user", "1000:1000",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "64",
            "--memory", mem,
            "--memory-swap", mem,
            "--cpus", "1",
            "--mount", f"type=volume,source={self.work_volume},target=/agent,readonly,volume-subpath={subpath}",
            "-e", "AGENT_DIR=/agent",
            "-e", f"AGENT_NAME={self.agent_name}",
            self.image,
        ]
        return subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

    def _on_process_started(self) -> None:
        # Attendre brièvement que le conteneur apparaisse pour lire son ID et localiser cpu.stat
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            res = subprocess.run(
                ["docker", "inspect", "--format", "{{.Id}}", self.container_name],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                self._container_id = res.stdout.strip()
                self._cpu_stat_path = find_container_cpu_stat(self.cgroup_root, self._container_id)
                break
            if not self.is_alive():
                break
            time.sleep(0.05)

    def _read_cpu_usec(self) -> int | None:
        if self._cpu_stat_path is None and self._container_id:
            self._cpu_stat_path = find_container_cpu_stat(self.cgroup_root, self._container_id)
        if self._cpu_stat_path is None:
            return None
        try:
            return parse_cpu_stat_usec(self._cpu_stat_path.read_text(encoding="utf-8"))
        except OSError:
            return None

    def was_oom_killed(self) -> bool:
        if self._oom_cached is not None:
            return self._oom_cached
        try:
            res = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.OOMKilled}}", self.container_name],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if res.returncode == 0:
                self._oom_cached = res.stdout.strip().lower() == "true"
                return self._oom_cached
        except Exception:
            pass
        return False

    def stop(self) -> None:
        super().stop()
        try:
            subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True,
                timeout=5.0,
            )
        except Exception:
            pass


class SubprocessAgentProcess(_PipeAgentBase):
    """Exécuteur sous-processus local réservé aux tests unitaires hors Docker."""

    def __init__(self, agent_dir: Path, agent_name: str, kit_python: Path = KIT_PYTHON, python_exe: str | None = None):
        super().__init__(agent_name)
        self.agent_dir = agent_dir.resolve()
        self.kit_python = kit_python
        self.python_exe = python_exe or sys.executable

    def _spawn_process(self) -> subprocess.Popen:
        host_script = REPO_ROOT / "agent_host" / "host.py"
        env = os.environ.copy()
        env["AGENT_DIR"] = str(self.agent_dir)
        env["AGENT_NAME"] = self.agent_name
        env["AUTOMATHON_KIT_PYTHON"] = str(self.kit_python)
        env["PYTHONPATH"] = str(self.kit_python)
        return subprocess.Popen(
            [self.python_exe, "-u", str(host_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        )
