"""Importe des agents invités (sans compte GitHub), pour un administrateur : par exemple les agents d'un tournoi.

Dans le conteneur du site, sur la VM (le dossier data/ de l'hôte y est /data) :

    docker compose exec arene-web python -m arene.import_agents /data/import

Le dossier contient des zips au format de l'arène (un dossier <nom>/ avec agent.py) et, en option, un fichier
equipes.json qui donne le nom affiché de chaque zip : [{"zip": "<nom>.zip", "team": "Nom de l'équipe"}, ...].
Chaque zip crée ou met à jour le compte invité de son nom de dossier ; les agents passent ensuite par la validation et
le placement habituels, un à la fois, sans cooldown ni limite de file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from arene import scheduler
from arene.config import Settings
from arene.db import create_db_engine, create_session_factory
from arene.migrations import apply_migrations
from arene.storage import LocalFileStore
from arene.zip_validator import ZipValidationError


def _collect(paths: list[Path], names_file: Path | None) -> tuple[list[Path], dict[str, str]]:
    zips: list[Path] = []
    manifests: list[Path] = [names_file] if names_file else []
    for path in paths:
        if path.is_dir():
            zips += sorted(path.glob("*.zip"))
            if names_file is None and (path / "equipes.json").is_file():
                manifests.append(path / "equipes.json")
        elif path.suffix == ".zip" and path.is_file():
            zips.append(path)
        else:
            raise SystemExit(f"Introuvable ou pas un zip : {path}")
    names: dict[str, str] = {}
    for manifest in manifests:
        for entry in json.loads(manifest.read_text(encoding="utf-8")):
            names[entry["zip"]] = entry["team"]
    return zips, names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path, help="zips, ou dossiers de zips")
    parser.add_argument("--names", type=Path, default=None,
                        help="fichier equipes.json des noms affichés (par défaut : celui du dossier, s'il existe)")
    args = parser.parse_args(argv)

    zips, names = _collect(args.paths, args.names)
    if not zips:
        print("Aucun zip à importer.")
        return 1

    settings = Settings()
    engine = create_db_engine(settings.database_url)
    apply_migrations(engine)
    session_factory = create_session_factory(engine)
    store = LocalFileStore(settings.data_dir)

    imported = 0
    with session_factory() as session:
        # Comme au démarrage du site : bots de référence (dummy_bot sert à la validation), sans effet s'ils existent
        scheduler.seed_reference_agents(session, store, settings)
        for zip_path in zips:
            team = names.get(zip_path.name)
            try:
                agent_id = scheduler.import_guest_agent(
                    session, store, settings, zip_path.read_bytes(), team, "admin-cli"
                )
            except (ZipValidationError, scheduler.SubmissionRejectedError) as exc:
                print(f"[REFUSÉ] {zip_path.name} : {exc}")
                continue
            imported += 1
            print(f"[OK] {zip_path.name} -> {agent_id}" + (f" ({team})" if team else ""))
    engine.dispose()

    print(f"{imported}/{len(zips)} agents importés : ils passent en validation puis en placement, un à la fois.")
    return 0 if imported == len(zips) else 1


if __name__ == "__main__":
    sys.exit(main())
