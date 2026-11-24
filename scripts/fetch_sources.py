"""Télécharge et vérifie les archives officielles de la release v1.0.0 d'antorchn/automathon.

Usage:
    python scripts/fetch_sources.py [--extract] [--dest sources]
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ASSETS = {
    "Automathon_1.0.0_Linux-x86-64.zip": {
        "url": "https://github.com/antorchn/automathon/releases/download/v1.0.0/Automathon_1.0.0_Linux-x86-64.zip",
        "sha256": "d3c76c810d87ba23bc886879bb7366908db2a40d886b8e6c9317aa841d124916",
    },
    "Automathon_1.0.0_WebGL.zip": {
        "url": "https://github.com/antorchn/automathon/releases/download/v1.0.0/Automathon_1.0.0_WebGL.zip",
        "sha256": "d2654404e05d7a1c6b36a658805ece777c30340b355ddededce2269abd22c720",
    },
    "Automathon_1.0.0_webplay.zip": {
        "url": "https://github.com/antorchn/automathon/releases/download/v1.0.0/Automathon_1.0.0_webplay.zip",
        "sha256": "5da8b07370a540ff3fad01a26ef866377c1e82f601028605e2a1e00315b575e4",
    },
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_asset(name: str, url: str, expected_sha256: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    if target.is_file():
        actual = sha256_file(target)
        if actual == expected_sha256:
            print(f"[OK] {name} déjà présent (sha256 vérifié)")
            return target
        print(f"[WARN] {name} corrompu (sha256={actual}), re-téléchargement...")
        target.unlink()

    tmp = target.with_suffix(".zip.tmp")
    print(f"[DL] {url} -> {target}")
    req = urllib.request.Request(url, headers={"User-Agent": "automathon-arene-fetcher/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out, 1024 * 1024)
    actual = sha256_file(tmp)
    if actual != expected_sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Empreinte SHA-256 invalide pour {name}: attendu {expected_sha256}, reçu {actual}")
    tmp.replace(target)
    print(f"[OK] {name} téléchargé et vérifié ({actual[:12]}...)")
    return target


def _safe_extract(zip_path: Path, out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_root = out_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            dest_path = (resolved_root / info.filename).resolve()
            if dest_path != resolved_root and resolved_root not in dest_path.parents:
                raise RuntimeError(f"Chemin suspect dans {zip_path.name}: {info.filename}")
        zf.extractall(out_dir)
    # Restore Unix executable permissions if stored in external_attr
    for info in zipfile.ZipFile(zip_path, "r").infolist():
        perm = (info.external_attr >> 16) & 0o777
        target = out_dir / info.filename
        if perm and target.is_file():
            try:
                target.chmod(perm)
            except OSError:
                pass


def _find_subdir_containing(root: Path, filename: str) -> Path:
    if (root / filename).exists():
        return root
    for p in root.rglob(filename):
        return p.parent
    raise FileNotFoundError(f"{filename} introuvable dans {root}")


def _build_agent_zip(agent_dir: Path, out_zip: Path) -> None:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    agent_name = agent_dir.name
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(agent_dir.rglob("*")):
            if not file_path.is_file():
                continue
            if "__pycache__" in file_path.parts or file_path.suffix == ".pyc":
                continue
            arcname = f"{agent_name}/{file_path.relative_to(agent_dir).as_posix()}"
            zf.write(file_path, arcname)


def extract_sources(dest_dir: Path) -> None:
    extracted = dest_dir / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)

    # 1. Kit Linux x86-64
    kit_raw = extracted / "_raw_kit"
    _safe_extract(dest_dir / "Automathon_1.0.0_Linux-x86-64.zip", kit_raw)
    kit_root = _find_subdir_containing(kit_raw, "Headless")
    kit_dir = extracted / "kit"
    if kit_dir.exists():
        shutil.rmtree(kit_dir)
    shutil.move(str(kit_root), str(kit_dir))
    shutil.rmtree(kit_raw, ignore_errors=True)
    headless_bin = kit_dir / "Headless" / "Headless"
    if headless_bin.is_file():
        headless_bin.chmod(0o755)

    # 2. WebGL build
    webgl_raw = extracted / "_raw_webgl"
    _safe_extract(dest_dir / "Automathon_1.0.0_WebGL.zip", webgl_raw)
    webgl_root = _find_subdir_containing(webgl_raw, "index.html")
    webgl_dir = extracted / "webgl"
    if webgl_dir.exists():
        shutil.rmtree(webgl_dir)
    shutil.move(str(webgl_root), str(webgl_dir))
    shutil.rmtree(webgl_raw, ignore_errors=True)

    # 3. Webplay tools
    webplay_dir = extracted / "webplay"
    _safe_extract(dest_dir / "Automathon_1.0.0_webplay.zip", webplay_dir)

    # 4. Reference agent zips (rush_bot and dummy_bot) from kit 1.0.0
    ref_dir = extracted / "reference_agents"
    agents_src = kit_dir / "PythonAI" / "agents"
    for name in ("rush_bot", "dummy_bot"):
        _build_agent_zip(agents_src / name, ref_dir / f"{name}.zip")

    print(f"[OK] Sources extraites dans {extracted}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Télécharge et vérifie les sources Automathon v1.0.0.")
    parser.add_argument("--dest", type=Path, default=ROOT / "sources", help="Dossier cible (défaut : sources/)")
    parser.add_argument("--extract", action="store_true", help="Extrait les archives dans <dest>/extracted/")
    args = parser.parse_args()

    for name, meta in ASSETS.items():
        download_asset(name, meta["url"], meta["sha256"], args.dest)

    if args.extract:
        extract_sources(args.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
