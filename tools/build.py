"""
Compile le Headless (.NET) et le jeu visuel (Unity), et prepare les paquets tout-en-un.

Usage (depuis la racine du depot) :
    python tools/build.py headless [--rid win-x64|win-arm64|linux-x64|linux-arm64|osx-arm64|osx-x64|all]
    python tools/build.py game     [--target windows|windows-arm64|linux|macos|webgl|all]
    python tools/build.py package  [--name Windows-ARM64 ...]
    python tools/build.py all
    python tools/build.py export DOSSIER

Sorties :
    build/headless/<rid>/       Headless autonome (self-contained, non single-file)
    build/game/<cible>/         jeu visuel (AutomathonGame.exe / .x86_64 / .app ; webgl : index.html + Build/)
    build/dist/*.zip            kits tout-en-un des participants (jeu + Headless/ + PythonAI/ + guide)
    build/logs/                 logs de compilation Unity
Sans --rid / --target : plateforme courante uniquement. --target all : les jeux de bureau (webgl a part,
a demander explicitement : module "Web Build Support" de Unity requis). Sans --name, package refait tous les paquets
dont le jeu et le Headless sont compiles.

export : copie dans DOSSIER (le depot des participants) les fichiers suivis par git, sans ceux reserves aux
organisateurs (PARTICIPANT_EXCLUDED), avec les fichiers de kit/ a la racine. Voir ORGANISATION.md.
"""

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNITY_PROJECT = ROOT / "game" / "AutomathonUnity"
HEADLESS_PROJECT = ROOT / "game" / "Headless" / "Headless.csproj"
PYTHONAI_DIR = ROOT / "PythonAI"
BUILD_DIR = ROOT / "build"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

RIDS = ["win-x64", "win-arm64", "linux-x64", "linux-arm64", "osx-arm64", "osx-x64"]

# Cible du jeu -> (argument -buildTarget de Unity, executable produit). windows-arm64 : meme cible Unity, avec
# l'architecture ARM64 (Assets/Editor/AutomathonBuild.cs)
TARGETS = {
    "windows": ("Win64", "AutomathonGame.exe"),
    "windows-arm64": ("Win64", "AutomathonGame.exe"),
    "linux": ("Linux64", "AutomathonGame.x86_64"),
    "macos": ("OSXUniversal", "AutomathonGame.app"),
    "webgl": ("WebGL", "index.html"),  # a servir par HTTP (PythonAI/webplay/server.py), voir PythonAI/webplay/README.md
}
DESKTOP_TARGETS = ["windows", "windows-arm64", "linux", "macos"]
# Architecture attendue des executables Windows, verifiee dans leur en-tete PE apres compilation
WINDOWS_ARCH = {"windows": "x64", "windows-arm64": "arm64", "win-x64": "x64", "win-arm64": "arm64"}
PE_MACHINES = {0x8664: "x64", 0xAA64: "arm64", 0x14C: "x86"}

# Paquet tout-en-un -> (cible du jeu, ou None sans jeu visuel ; RID du Headless)
PACKAGES = {
    "Windows": ("windows", "win-x64"),
    "Windows-ARM64": ("windows-arm64", "win-arm64"),
    "Linux-x86-64": ("linux", "linux-x64"),
    "Linux-ARM-databricks": (None, "linux-arm64"),
    "MacOS-arm64": ("macos", "osx-arm64"),
    "MacOS-x64": ("macos", "osx-x64"),
}

# Reserve aux organisateurs : ni dans les kits tout-en-un, ni dans le depot des participants (ORGANISATION.md).
# Chemins relatifs a la racine du depot ; un chemin termine par "/" exclut tout le dossier.
PARTICIPANT_EXCLUDED = (
    "CLAUDE.md", "ORGANISATION.md", "README.md", "kit/",
    "PythonAI/evaluation/run_tournament.py", "PythonAI/evaluation/record_tournament.py",
    "PythonAI/evaluation/record_highlights.py", "PythonAI/requirements_admin.txt", "PythonAI/webplay/",
    # Outil local de mesure du classement, qui lit des agents d'organisateurs hors du dépôt
    "scripts/run_local_leaderboard.py",
)
# kit/<fichier> va a la racine du kit des participants (zips et depot) ; README.md y remplace celui de ce depot
KIT_ROOT_FILES = ("README.md", "DEMARRAGE.md")
# Autres fichiers du depot ajoutes aux kits tout-en-un, au meme chemin ; un chemin termine par "/" ajoute tous les
# fichiers suivis par git de ce dossier
KIT_EXTRA_FILES = ("tools/smoke_test.py", "databricks/", "VERSION", "CHANGELOG.md")


def kit_extra_files() -> list[str]:
    """KIT_EXTRA_FILES, dossiers developpes en leurs fichiers suivis par git (chemins relatifs, separateurs /)."""
    files = []
    for rel in KIT_EXTRA_FILES:
        if rel.endswith("/"):
            listed = subprocess.run(["git", "ls-files", "-z", "--", rel], cwd=ROOT, capture_output=True, text=True,
                                    check=True).stdout.split("\0")
            files += sorted(f for f in listed if f)
        else:
            files.append(rel)
    return files


def participant_excluded(rel: str) -> bool:
    """rel : chemin relatif a la racine du depot, separateurs /."""
    return any(rel == e or (e.endswith("/") and rel.startswith(e)) for e in PARTICIPANT_EXCLUDED)


# Exclus de PythonAI/ dans les paquets (environnements, caches, sorties d'entrainement)
PYTHONAI_EXCLUDED_DIRS = {".venv", "__pycache__", "outputs", "checkpoints", "tensorboard", "benchmark"}
PYTHONAI_EXCLUDED_FILES = ("rl_model", "bench_model", "mon_agent_")


def host_rid() -> str:
    """RID .NET de la machine courante (ex : win-x64, linux-arm64, osx-arm64)."""
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    os_name = {"win32": "win", "darwin": "osx"}.get(sys.platform, "linux")
    return f"{os_name}-{arch}"


def host_target() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def run(cmd: list[str], **kwargs) -> int:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, **kwargs).returncode


def pe_architecture(path: Path) -> str:
    """Architecture d'un executable ou d'une DLL Windows, lue dans son en-tete PE (x64, arm64, x86)."""
    with open(path, "rb") as f:
        data = f.read(4096)
    offset = int.from_bytes(data[0x3C:0x40], "little")
    if data[:2] != b"MZ" or data[offset:offset + 4] != b"PE\0\0":
        return "inconnue"
    machine = int.from_bytes(data[offset + 4:offset + 6], "little")
    return PE_MACHINES.get(machine, hex(machine))


def check_windows_arch(name: str, files: list[Path]):
    """Verifie que les executables Windows de `name` (cible ou RID) sont de l'architecture attendue."""
    expected = WINDOWS_ARCH.get(name)
    if expected is None:
        return
    for path in files:
        found = pe_architecture(path)
        if found != expected:
            sys.exit(f"{path.relative_to(ROOT)} est en {found}, {expected} attendu.")
        print(f"Architecture {found} : {path.relative_to(ROOT)}")


# --------------------------------------------------------------------------- Headless

def build_headless(rids: list[str]):
    if shutil.which("dotnet") is None:
        sys.exit("dotnet introuvable : installez le SDK .NET 10 (https://dotnet.microsoft.com/download).")
    for rid in rids:
        out = BUILD_DIR / "headless" / rid
        shutil.rmtree(out, ignore_errors=True)
        print(f"\n=== Headless {rid} -> {out.relative_to(ROOT)}")
        code = run(["dotnet", "publish", str(HEADLESS_PROJECT), "-c", "Release", "-r", rid,
                    "--self-contained", "true", "-p:PublishSingleFile=false", f"-p:Version={VERSION}",
                    "-o", str(out), "--nologo"])
        binary = out / ("Headless.exe" if rid.startswith("win") else "Headless")
        if code != 0 or not binary.exists():
            sys.exit(f"Echec de la compilation du Headless {rid}.")
        check_windows_arch(rid, [binary])
        print(f"OK : {binary.relative_to(ROOT)}")


# --------------------------------------------------------------------------- Jeu Unity

def unity_version() -> str:
    for line in (UNITY_PROJECT / "ProjectSettings" / "ProjectVersion.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("m_EditorVersion:"):
            return line.split(":", 1)[1].strip()
    sys.exit("Version de Unity introuvable dans ProjectSettings/ProjectVersion.txt.")


def _hub_editor_locations(version: str) -> list[Path]:
    """Emplacements de l'editeur `version` connus de Unity Hub (editors-v2.json)."""
    if sys.platform == "win32":
        hub_file = Path(os.environ.get("APPDATA", "")) / "UnityHub" / "editors-v2.json"
    elif sys.platform == "darwin":
        hub_file = Path.home() / "Library" / "Application Support" / "UnityHub" / "editors-v2.json"
    else:
        hub_file = Path.home() / ".config" / "UnityHub" / "editors-v2.json"
    try:
        data = json.loads(hub_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = []
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get("version") == version:
                location = item.get("location")
                found += [Path(p) for p in (location if isinstance(location, list) else [location]) if p]
            stack += list(item.values())
        elif isinstance(item, list):
            stack += item
    return found


def find_unity(version: str) -> Path:
    if os.environ.get("UNITY_EDITOR"):
        candidates = [Path(os.environ["UNITY_EDITOR"])]
    else:
        candidates = _hub_editor_locations(version)
        if sys.platform == "win32":
            candidates += [Path(r"C:\Program Files\Unity\Hub\Editor") / version / "Editor" / "Unity.exe",
                           Path(rf"C:\Program Files\Unity {version}\Editor\Unity.exe")]
        elif sys.platform == "darwin":
            candidates += [Path("/Applications/Unity/Hub/Editor") / version / "Unity.app"]
        else:
            candidates += [Path.home() / "Unity" / "Hub" / "Editor" / version / "Editor" / "Unity"]
    for path in candidates:
        if path.suffix == ".app":
            path = path / "Contents" / "MacOS" / "Unity"
        if path.is_file():
            return path
    sys.exit(f"Unity {version} introuvable. Installez cette version exacte avec Unity Hub, "
             f"ou definissez UNITY_EDITOR avec le chemin de l'executable Unity.")


def check_project_closed():
    """Unity verrouille Temp/UnityLockfile tant que le projet est ouvert dans l'editeur."""
    lock = UNITY_PROJECT / "Temp" / "UnityLockfile"
    if lock.exists():
        try:
            lock.unlink()  # fichier laisse par un editeur ferme ou plante : sans danger
        except OSError:
            sys.exit("Le projet Unity est ouvert dans l'editeur : fermez-le avant de compiler.")


def build_game(targets: list[str]):
    version = unity_version()
    unity = find_unity(version)
    logs = BUILD_DIR / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    for target in targets:
        check_project_closed()
        build_target, executable = TARGETS[target]
        out = BUILD_DIR / "game" / target
        shutil.rmtree(out, ignore_errors=True)
        log = logs / f"unity_{target}.log"
        print(f"\n=== Jeu {target} (Unity {version}) -> {out.relative_to(ROOT)}")
        print(f"Log : {log.relative_to(ROOT)} (la premiere compilation importe tout le projet : plusieurs minutes)")
        start = time.monotonic()
        code = run([str(unity), "-batchmode", "-quit", "-projectPath", str(UNITY_PROJECT),
                    "-buildTarget", build_target, "-logFile", str(log),
                    "-executeMethod", "AutomathonEditor.AutomathonBuild.Build",
                    "-automathonTarget", target, "-automathonOutput", str(out), "-automathonVersion", VERSION])
        minutes = (time.monotonic() - start) / 60
        if code != 0 or not (out / executable).exists():
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.exists() else []
            errors = [l for l in lines if "error" in l.lower() or "exception" in l.lower()][-20:]
            print("\n".join(errors or lines[-30:]))
            sys.exit(f"Echec de la compilation du jeu {target} (code {code}, {minutes:.1f} min). Voir {log}.")
        check_windows_arch(target, [out / executable, out / "UnityPlayer.dll"])
        print(f"OK en {minutes:.1f} min : {(out / executable).relative_to(ROOT)}")


# --------------------------------------------------------------------------- Paquets

def _is_unix_executable(arcname: str) -> bool:
    name = arcname.rsplit("/", 1)[-1]
    return ("/Contents/MacOS/" in arcname or name.endswith((".x86_64", ".so", ".dylib"))
            or ("/Headless/" in arcname and "." not in name))


def _add_file(z: zipfile.ZipFile, path: Path, arcname: str, unix: bool):
    info = zipfile.ZipInfo.from_file(path, arcname)
    info.compress_type = zipfile.ZIP_DEFLATED
    if unix:
        mode = 0o755 if _is_unix_executable(arcname) else 0o644
        info.external_attr = (stat.S_IFREG | mode) << 16
        info.create_system = 3  # Unix : unzip restaure les droits
    with open(path, "rb") as src, z.open(info, "w") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)


def _add_tree(z: zipfile.ZipFile, src: Path, prefix: str, unix: bool, skip_dirs=(), skip_files=(),
              participant_kit=False):
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames
                       if d not in skip_dirs and not d.endswith(".egg-info") and "DoNotShip" not in d]
        for name in filenames:
            if name.startswith(skip_files) or name.endswith(".pyc"):
                continue
            path = Path(dirpath) / name
            if participant_kit and participant_excluded(path.relative_to(ROOT).as_posix()):
                continue
            _add_file(z, path, f"{prefix}/{path.relative_to(src).as_posix()}", unix)


def package(names: list[str] | None = None):
    dist = BUILD_DIR / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    made = []
    for name, (target, rid) in PACKAGES.items():
        if names and name not in names:
            continue
        headless = BUILD_DIR / "headless" / rid
        game = BUILD_DIR / "game" / target if target else None
        missing = [p for p in (headless, game) if p is not None and not p.is_dir()]
        if missing:
            print(f"- {name} : ignore (manque {', '.join(str(p.relative_to(ROOT)) for p in missing)})")
            continue
        top = f"Automathon_{VERSION}_{name}"
        zip_path = dist / f"{top}.zip"
        unix = not name.startswith("Windows")
        with zipfile.ZipFile(zip_path, "w") as z:
            _add_tree(z, headless, f"{top}/Headless", unix)
            if game is not None:
                _add_tree(z, game, top, unix)
            _add_tree(z, PYTHONAI_DIR, f"{top}/PythonAI", unix,
                      skip_dirs=PYTHONAI_EXCLUDED_DIRS, skip_files=PYTHONAI_EXCLUDED_FILES, participant_kit=True)
            for kit_file in KIT_ROOT_FILES:
                _add_file(z, ROOT / "kit" / kit_file, f"{top}/{kit_file}", unix)
            for rel in kit_extra_files():
                _add_file(z, ROOT / rel, f"{top}/{rel}", unix)
        made.append(zip_path)
        print(f"- {name} : {zip_path.relative_to(ROOT)} ({zip_path.stat().st_size // 2**20} Mo)")
    if not made:
        sys.exit("Aucun paquet produit : compilez d'abord le Headless (et le jeu).")


# --------------------------------------------------------------------------- Depot des participants

def export(dest: Path, allow_dirty: bool):
    """Remplace le contenu de dest (sauf .git) par le kit des participants, tire des fichiers suivis par git."""
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.strip()
    if dirty and not allow_dirty:
        sys.exit("Copie de travail modifiee : commitez (et taguez) d'abord, ou passez --allow-dirty.")
    dest = dest.resolve()
    if ROOT == dest or ROOT in dest.parents:
        sys.exit("Le dossier d'export doit etre hors de ce depot.")
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.split("\0")
    files = [f for f in files if f and not participant_excluded(f)]
    dest.mkdir(parents=True, exist_ok=True)
    for item in dest.iterdir():
        if item.name != ".git":
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    for name in KIT_ROOT_FILES:
        shutil.copy2(ROOT / "kit" / name, dest / name)
    print(f"Kit des participants {VERSION} exporte dans {dest} : {len(files) + len(KIT_ROOT_FILES)} fichiers")


# --------------------------------------------------------------------------- Entree

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("headless", help="compile le Headless .NET")
    p.add_argument("--rid", default=None, help=f"{', '.join(RIDS)} ou all (defaut : plateforme courante)")
    p = sub.add_parser("game", help="compile le jeu visuel avec Unity")
    p.add_argument("--target", default=None, help="windows, linux, macos, webgl ou all (defaut : plateforme courante ; all = "
                        "les jeux de bureau)")
    p = sub.add_parser("package", help="cree les paquets tout-en-un a partir de build/")
    p.add_argument("--name", action="append", choices=list(PACKAGES), help="paquet a creer (repetable ; defaut : tous)")
    sub.add_parser("all", help="tous les Headless, les jeux de bureau, puis les paquets (sans le WebGL)")
    p = sub.add_parser("export", help="exporte le kit des participants dans un dossier (leur depot git)")
    p.add_argument("dest", type=Path)
    p.add_argument("--allow-dirty", action="store_true", help="exporte meme avec des modifications non commitees")
    args = parser.parse_args()

    print(f"Automathon {VERSION}")
    if args.command == "headless":
        rids = RIDS if args.rid == "all" else [args.rid or host_rid()]
        if not set(rids) <= set(RIDS):
            parser.error(f"--rid inconnu : {args.rid}")
        build_headless(rids)
    elif args.command == "game":
        targets = DESKTOP_TARGETS if args.target == "all" else [args.target or host_target()]
        if not set(targets) <= set(TARGETS):
            parser.error(f"--target inconnue : {args.target}")
        build_game(targets)
    elif args.command == "package":
        package(args.name)
    elif args.command == "export":
        export(args.dest, args.allow_dirty)
    else:
        build_headless(RIDS)
        build_game(DESKTOP_TARGETS)  # le WebGL (outil des organisateurs) se compile a part : game --target webgl
        package()


if __name__ == "__main__":
    main()
