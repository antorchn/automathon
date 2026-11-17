import os
import sys
import stat
import time
import socket
import plistlib
import subprocess

# -----------------------------------------------------------------------------
# Utilitaires cross-platform (Windows, Linux, macOS) partages par les scripts
# d'entrainement, d'evaluation et le lanceur d'agent.
#
# Disposition attendue du projet (racine = dossier parent de PythonAI/) :
#   <racine>/
#   ├── PythonAI/
#   ├── Headless/Headless.exe       (Windows)  |  Headless/Headless (Linux, macOS)
#   └── jeu graphique               (voir GAME_CANDIDATES ci-dessous)
# Dans le depot de developpement, les binaires sont cherches ensuite dans les
# sorties de tools/build.py : build/headless/<rid>/ et build/game/<cible>/.
# -----------------------------------------------------------------------------

GITHUB_RELEASES_URL = "https://github.com/antorchn/automathon_2k26/releases/latest/download"

TRAINING_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHONAI_DIR = os.path.dirname(TRAINING_DIR)

# Noms possibles du jeu graphique a la racine du projet, par OS. Les builds Unity
# actuels s'appellent AutomathonMVC.exe / linux.x86_64 / Mac.app ; AutomathonGame*
# est le nom prevu pour les futures releases.
GAME_CANDIDATES = {
    "win32": ["AutomathonGame.exe", "AutomathonMVC.exe"],
    "linux": ["AutomathonGame.x86_64", "AutomathonGame", "linux.x86_64", "AutomathonMVC.x86_64"],
    "darwin": ["AutomathonGame.app", "AutomathonMVC.app", "Mac.app"],
}

_BINARIES_HINT = (
    "Les binaires (jeu + dossier Headless/) sont fournis par les organisateurs : "
    "placez-les a la racine du projet, a cote de PythonAI/."
)

_prepared_paths: set[str] = set()


def get_project_root() -> str:
    """Retourne la racine du projet (dossier parent de PythonAI/)."""
    return os.path.dirname(PYTHONAI_DIR)


def _host_rid() -> str:
    """Plateforme courante au format .NET (win-x64, linux-arm64, osx-arm64...), comme tools/build.py."""
    import platform
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    return f"{ {'win32': 'win', 'darwin': 'osx'}.get(sys.platform, 'linux') }-{arch}"


def _build_dir(*parts: str) -> str:
    """Dossier des sorties de tools/build.py (depot de developpement)."""
    return os.path.join(get_project_root(), "build", *parts)


def resolve_training_path(path: str) -> str:
    """Chemin relatif -> relatif a PythonAI/training/. Un chemin absolu est garde tel quel."""
    return os.path.join(TRAINING_DIR, os.path.expanduser(path))


def _ensure_executable(path: str):
    """Repositionne le bit d'execution (perdu quand une archive est creee sous Windows)."""
    mode = os.stat(path).st_mode
    if not mode & stat.S_IXUSR:
        os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _prepare_macos(path: str):
    """
    macOS (Gatekeeper) : un binaire telecharge ou dezippe porte l'attribut
    com.apple.quarantine et est bloque au lancement. On retire cet attribut et,
    si la signature est absente ou invalide, on signe ad hoc (obligatoire sur
    Apple Silicon). Ne s'applique qu'aux binaires du projet.
    """
    if path in _prepared_paths:
        return
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", path], capture_output=True)
    if subprocess.run(["codesign", "--verify", path], capture_output=True).returncode != 0:
        print(f"[macOS] Signature ad hoc de {os.path.basename(path)}...")
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", path], capture_output=True)
    _prepared_paths.add(path)


def get_unity_headless_path() -> str:
    """
    Detecte l OS et retourne le chemin absolu vers le binaire Headless Unity.

    Priorite :
      1. Variable d environnement AUTOMATHON_HEADLESS_PATH (pour Databricks / DBFS)
      2. Chemin standard relatif a la racine du projet
      3. Sortie de tools/build.py pour la plateforme courante (build/headless/<rid>/)

    Raises FileNotFoundError si le binaire est introuvable.
    """
    env_path = os.environ.get("AUTOMATHON_HEADLESS_PATH")
    if env_path:
        path = env_path
    else:
        binary = "Headless.exe" if sys.platform == "win32" else "Headless"
        candidates = [os.path.join(get_project_root(), "Headless", binary),
                      _build_dir("headless", _host_rid(), binary)]
        path = next((p for p in candidates if os.path.exists(p)), candidates[0])

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Binaire Unity Headless introuvable : {path}\n{_BINARIES_HINT}\n"
            f"Ou definissez AUTOMATHON_HEADLESS_PATH pour pointer vers votre binaire."
        )

    if sys.platform != "win32":
        _ensure_executable(path)
    if sys.platform == "darwin":
        _prepare_macos(path)

    return path


def get_unity_headless_cwd() -> str:
    """
    Retourne le working directory depuis lequel lancer le binaire Unity.
    Unity cherche ses fichiers data (*_Data/) relativement au CWD.
    Sur Linux (Colab/Databricks), il faut que le CWD soit le dossier
    contenant le binaire, pas PythonAI/.
    """
    headless_path = get_unity_headless_path()
    return os.path.dirname(headless_path)


def get_unity_popen_args(port: int) -> tuple[list, dict]:
    """
    Retourne (cmd_args, popen_kwargs) adaptes a l OS pour lancer le jeu headless.

    NOTE: Le binaire Headless est une application C# .NET autonome (pas un build Unity standard).
    Il n'a pas besoin de -batchmode ni de -nographics, et ne possede pas de dossier _Data/.
    """
    headless_path = get_unity_headless_path()
    cmd = [headless_path, f"tcp://127.0.0.1:{port}"]
    kwargs = {}

    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        # CWD = racine du projet (l'application C# peut chercher des chemins relatifs)
        kwargs["cwd"] = get_project_root()

        # FIX pour le crash SIGSEGV (PAL_SEHException) sur Colab/Docker
        # Les applications .NET autonomes crashent souvent sur des Linux minimalistes
        # a cause de la globalisation (ICU) ou du Garbage Collector (cgroups).
        env = os.environ.copy()
        env["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] = "1"
        env["DOTNET_gcServer"] = "0"
        kwargs["env"] = env

    return cmd, kwargs


# -----------------------------------------------------------------------------
# Arret des Headless avec le processus Python qui les a lances
#
# Sans cela, un script qui plante ou qu'on tue laisse ses Headless tourner : ils
# gardent leurs ports et le lancement suivant echoue ("port deja utilise").
#   - Windows : Job Object "kill on close", le systeme tue les Headless des que le
#     processus Python disparait, meme tue brutalement (Gestionnaire des taches...).
#   - Linux   : PR_SET_PDEATHSIG, le noyau envoie SIGKILL au Headless a la mort
#     du thread qui l'a lance (applique seulement depuis le thread principal).
#   - Partout : nettoyage a la sortie du processus (seul filet sous macOS :
#     couvre les exceptions et Ctrl+C, pas un kill -9).
# -----------------------------------------------------------------------------

_launched: list[subprocess.Popen] = []
_win_job = None
_warned_no_job = False


def _kill_launched_headless():
    for process in _launched:
        if process.poll() is None:
            process.kill()


def _register_exit_cleanup():
    import multiprocessing.util
    # Execute a la sortie du processus principal (atexit) et a la fin des processus
    # multiprocessing (SubprocVecEnv, ProcessPoolExecutor), y compris apres une exception.
    multiprocessing.util.Finalize(None, _kill_launched_headless, exitpriority=0)


_register_exit_cleanup()


def _windows_job():
    """Job Object du processus courant : tout processus qui y est rattache meurt avec lui."""
    global _win_job
    if _win_job is None:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        job = kernel32.CreateJobObjectW(None, None)
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())  # 9 = JobObjectExtendedLimitInformation
        # Le handle n'est jamais ferme : il l'est par le systeme a la mort du processus, ce qui tue le job
        _win_job = (kernel32, job)
    return _win_job


def _linux_die_with_parent_fn():
    """preexec_fn (execute dans l'enfant avant exec) : SIGKILL quand le thread parent meurt."""
    import ctypes
    import signal
    import threading
    if threading.current_thread() is not threading.main_thread():
        return None  # PDEATHSIG suit le thread : un thread temporaire tuerait le Headless trop tot
    prctl = ctypes.CDLL(None, use_errno=True).prctl  # resolu dans le parent : rien a charger apres fork
    parent_pid = os.getpid()

    def die_with_parent():
        prctl(1, signal.SIGKILL)  # 1 = PR_SET_PDEATHSIG
        if os.getppid() != parent_pid:  # parent deja mort entre fork et prctl
            os.kill(os.getpid(), signal.SIGKILL)

    return die_with_parent


def _bind_to_current_process(process: subprocess.Popen):
    """Windows : rattache le Headless au Job Object du processus courant (voir plus haut)."""
    global _warned_no_job
    _launched[:] = [p for p in _launched if p.poll() is None]
    _launched.append(process)
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32, job = _windows_job()
        if not kernel32.AssignProcessToJobObject(job, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
    except OSError as e:
        if not _warned_no_job:
            print(f"[Avertissement] Arret automatique des Headless indisponible ({e}) : "
                  f"en cas de plantage, arretez-les avec 'taskkill /F /IM Headless.exe'.")
            _warned_no_job = True


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def launch_headless(port: int, timeout: float | None = None) -> subprocess.Popen:
    """
    Lance une instance Headless sur `port` et attend qu'elle ecoute.

    Attend au plus `timeout` secondes (defaut : get_startup_wait()) au lieu d'un
    sleep fixe : rapide sur un PC, tolerant sur une VM lente ou au premier
    lancement sous macOS (verification Gatekeeper).
    Le Headless est arrete automatiquement quand le processus Python qui l'a lance se
    termine, meme sur plantage (voir _bind_to_current_process).
    Raises RuntimeError si le processus meurt ou n'ecoute pas a temps.
    """
    if timeout is None:
        timeout = get_startup_wait()
    if _port_is_open(port):
        raise RuntimeError(
            f"Le port {port} est deja utilise (un agent run_my_agent.py ou un autre Headless ?). "
            f"Changez AUTOMATHON_PORT_BASE ou le port du script."
        )
    cmd, kwargs = get_unity_popen_args(port)
    if sys.platform.startswith("linux"):
        kwargs["preexec_fn"] = _linux_die_with_parent_fn()
    process = subprocess.Popen(cmd, **kwargs)
    _bind_to_current_process(process)

    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"Le Headless (port {port}) a crashe au lancement (code {process.returncode}).")
        if _port_is_open(port):
            return process
        if time.monotonic() > deadline:
            process.kill()
            raise RuntimeError(
                f"Le Headless (port {port}) n'ecoute pas apres {timeout}s. "
                f"Port deja utilise ? Sinon augmentez AUTOMATHON_STARTUP_WAIT."
            )
        time.sleep(0.2)


def make_popen_kwargs() -> dict:
    """
    Retourne les kwargs supplementaires pour subprocess.Popen selon l OS.
    Windows : masque la fenetre console du processus Unity.
    Linux   : pas de kwargs supplementaires.
    DEPRECATED : utiliser launch_headless() pour les lancements Unity.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def get_game_path() -> str:
    """
    Retourne le chemin du jeu graphique (.exe, binaire Linux ou bundle .app).

    Priorite : AUTOMATHON_GAME_PATH, puis les noms de GAME_CANDIDATES a la racine du projet,
    puis dans la sortie de tools/build.py pour la plateforme courante (build/game/<cible>/).
    """
    env_path = os.environ.get("AUTOMATHON_GAME_PATH")
    if env_path:
        if not os.path.exists(env_path):
            raise FileNotFoundError(f"AUTOMATHON_GAME_PATH pointe vers un chemin inexistant : {env_path}")
        return env_path

    root = get_project_root()
    build_game_dir = _build_dir("game", {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux"))
    candidates = GAME_CANDIDATES.get(sys.platform, GAME_CANDIDATES["linux"])
    for folder in (root, build_game_dir):
        for name in candidates:
            path = os.path.join(folder, name)
            if os.path.exists(path):
                return path
    raise FileNotFoundError(
        f"Jeu graphique introuvable dans {root} ni dans {build_game_dir} (noms cherches : {', '.join(candidates)}).\n"
        f"{_BINARIES_HINT}\nOu definissez AUTOMATHON_GAME_PATH."
    )


def _macos_app_executable(app_path: str) -> str:
    """Executable interne d'un bundle .app (Contents/MacOS/<CFBundleExecutable>)."""
    with open(os.path.join(app_path, "Contents", "Info.plist"), "rb") as f:
        name = plistlib.load(f)["CFBundleExecutable"]
    return os.path.join(app_path, "Contents", "MacOS", name)


def launch_game() -> subprocess.Popen:
    """
    Lance le jeu graphique et retourne le processus (que l'on peut kill()).
    Sous macOS on lance l'executable interne du .app plutot que `open`, pour
    garder la main sur le processus.
    """
    path = get_game_path()
    exe = path
    if sys.platform == "darwin" and path.endswith(".app"):
        exe = _macos_app_executable(path)
        _ensure_executable(exe)
        _prepare_macos(path)
    elif sys.platform != "win32":
        _ensure_executable(exe)
    print(f"Lancement du jeu : {exe}")
    return subprocess.Popen([exe], cwd=os.path.dirname(path))


def get_port_base() -> int:
    """
    Retourne le port de base pour les instances Unity headless.
    Configurable via AUTOMATHON_PORT_BASE pour eviter les collisions
    entre participants sur un cluster Databricks partage.
    Defaut : 5600.
    """
    return int(os.environ.get("AUTOMATHON_PORT_BASE", "5600"))


def get_num_envs() -> int:
    """
    Retourne le nombre d instances Unity a lancer en parallele.
    Configurable via AUTOMATHON_NUM_ENVS.
    Defaut : min(os.cpu_count(), 16) pour ne pas surcharger les VMs Colab.
    """
    default = min(os.cpu_count() or 2, 16)
    return int(os.environ.get("AUTOMATHON_NUM_ENVS", str(default)))


def get_startup_wait() -> int:
    """
    Nombre de secondes maximum a attendre le demarrage d'une instance Headless.
    Sur Colab/Databricks les VMs sont plus lentes, 15s est plus sur.
    Configurable via AUTOMATHON_STARTUP_WAIT. Defaut : 15.
    """
    return int(os.environ.get("AUTOMATHON_STARTUP_WAIT", "15"))


def get_checkpoint_dir() -> str:
    """
    Retourne le repertoire de sauvegarde des checkpoints.
    Configurable via AUTOMATHON_CHECKPOINT_DIR.
    Sur Databricks : /dbfs/FileStore/automathon/checkpoints/<equipe>
    Defaut : PythonAI/training/checkpoints/
    """
    return os.environ.get("AUTOMATHON_CHECKPOINT_DIR", os.path.join(TRAINING_DIR, "checkpoints"))


def register_config_resolvers():
    """Resolvers OmegaConf utilises par les configs Hydra de training/conf/ (ex : ${training_dir:})."""
    from omegaconf import OmegaConf
    OmegaConf.register_new_resolver("training_dir", lambda: TRAINING_DIR.replace("\\", "/"), replace=True)
