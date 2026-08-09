"""Reading profiles and worlds out of Modrinth and CurseForge.

Was a class whose ``__init__`` did ``try: Path(data) except: self.data = data``,
so an instance ended up with *either* ``source_path`` *or* ``data`` depending
on what you passed, and only one method understood the second shape. These are
plain functions now -- each one says what it takes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import py_gamelog
import py_managers
from py_models import ModManager, Profile, World

NEW_WORLD_LABEL = "➕  Create a New World!"


# -------------------------
# region PUBLIC API
# -------------------------
def read_profiles(manager: ModManager) -> list[Profile]:
    """Every usable profile in a mod manager, ready to display."""
    if manager.db_path.is_file() and manager.db_path.suffix == ".db":
        profiles = _read_modrinth_profiles(manager)
    elif manager.profiles_path.is_dir():
        profiles = _read_curseforge_profiles(manager)
    else:
        return []

    return _format_profiles(profiles)


def read_worlds(saves_dir: Path, *, allow_new: bool = False) -> list[World]:
    """Save folders under ``saves_dir``.

    When ``allow_new`` is set, a synthetic "create a new world" entry is
    appended so the loader can install into a profile that has no worlds yet.
    """
    worlds: list[World] = []

    if saves_dir.exists() and saves_dir.is_dir():
        for entry in sorted(saves_dir.iterdir()):
            try:
                if not entry.is_dir():
                    continue
                worlds.append(
                    World(
                        path=entry,
                        name=entry.name,
                        last_played=_epoch_to_date(int(entry.stat().st_mtime)),
                        is_new=False,
                    )
                )
            except OSError:
                continue

    if allow_new:
        worlds.append(
            World(
                path=saves_dir / NEW_WORLD_LABEL,
                name=NEW_WORLD_LABEL,
                last_played="never",
                is_new=True,
            )
        )

    return _format_worlds(worlds)


def read_xaero_waypoints(profile_root: Path) -> list[dict]:
    """Xaero minimap/world-map instances that exist in both folders.

    Not currently wired into any menu -- the Xaero toggle only sets a flag.
    Kept because the reader is correct and the feature is half-built.
    """
    xaero_root = profile_root / "xaero"
    minimap_root = xaero_root / "minimap"
    worldmap_root = xaero_root / "world-map"

    if not minimap_root.is_dir() or not worldmap_root.is_dir():
        return []

    minimap = {p.name for p in minimap_root.iterdir() if p.is_dir()}
    worldmap = {p.name for p in worldmap_root.iterdir() if p.is_dir()}

    return [
        {
            "name": instance,
            "minimap_path": minimap_root / instance,
            "worldmap_path": worldmap_root / instance,
        }
        for instance in sorted(minimap & worldmap)
    ]


# -------------------------
# region MODRINTH (SQLite)
# -------------------------
def _read_modrinth_profiles(manager: ModManager) -> list[Profile]:
    profiles: list[Profile] = []
    profiles_root = py_managers.modrinth_profiles_root()

    try:
        # read-only so we never take a write lock on the launcher's live DB
        uri = f"file:{manager.db_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            rows = conn.execute(
                "SELECT path, name, last_played FROM instances"
            ).fetchall()
    except sqlite3.Error as exc:
        print(f"Error reading Modrinth DB: {exc}")
        return profiles

    for folder, name, last_played in rows:
        root = profiles_root / folder

        # Modrinth doesn't prune deleted instances from the DB.
        if not root.exists():
            continue

        # Modrinth removed both fields from the schema, so the only remaining
        # source is the game log -- which requires the profile to have been
        # launched at least once. Unplayed profiles stay "Unavailable".
        game = py_gamelog.read_game_info(root)

        profiles.append(
            Profile(
                root=root,
                mods_dir=root / "mods",
                saves_dir=root / "saves",
                folder=folder,
                name=name,
                game_version=game.version,
                mod_loader=game.loader,
                last_played=_epoch_to_date(last_played),
            )
        )

    return profiles


# -------------------------
# region CURSEFORGE (JSON)
# -------------------------
_INSTANCE_JSON = "minecraftinstance.json"


def _read_curseforge_profiles(manager: ModManager) -> list[Profile]:
    profiles: list[Profile] = []
    base = manager.profiles_path

    if not base.exists() or not base.is_dir():
        return profiles

    for inst in sorted(base.iterdir()):
        try:
            if not inst.is_dir():
                continue
            profiles.append(_curseforge_profile(inst))
        except OSError:
            # Skip an unreadable instance rather than losing the whole list.
            continue

    return profiles


def _curseforge_profile(inst: Path) -> Profile:
    data = _load_instance_json(inst)

    mods_dir = inst / "mods"
    root = inst
    if not mods_dir.exists() and (inst / "minecraft" / "mods").exists():
        root = inst / "minecraft"
        mods_dir = root / "mods"

    if not data:
        # No manifest -- still offer the instance, named after its folder.
        return Profile(
            root=root,
            mods_dir=mods_dir,
            saves_dir=root / "saves",
            folder=inst.name,
            name=inst.name,
            game_version="unknown",
            mod_loader="unknown",
            last_played=_epoch_to_date(_safe_mtime(inst)),
        )

    game_version = str(
        _first_of(
            data,
            ("minecraftVersion", "version", "mcVersion", "minecraft_version"),
            "unknown",
        )
    )
    mod_loader = str(
        _first_of(
            data,
            ("modLoader", "modLoaderType", "loader", "modloader", "mod_loader"),
            "unknown",
        )
    )

    # CurseForge manifests vary a lot by version and frequently omit the loader.
    # Only pay for a log read when the JSON actually came up short.
    if "unknown" in (game_version, mod_loader):
        game = py_gamelog.read_game_info(root)
        if game.found:
            if game_version == "unknown":
                game_version = game.version
            if mod_loader == "unknown":
                mod_loader = game.loader

    return Profile(
        root=root,
        mods_dir=mods_dir,
        saves_dir=root / "saves",
        folder=inst.name,
        name=_first_of(data, ("name", "instanceName", "displayName"), inst.name),
        game_version=game_version,
        mod_loader=mod_loader,
        last_played=_epoch_to_date(_curseforge_last_played(data, inst)),
    )


def _load_instance_json(inst: Path) -> dict:
    candidates = [
        inst / _INSTANCE_JSON,
        inst / "instance" / _INSTANCE_JSON,
        inst / "config" / _INSTANCE_JSON,
    ]

    json_path = next((p for p in candidates if p.exists()), None)

    if json_path is None:
        # Shallow search as a fallback; don't descend into the whole instance.
        for found in inst.rglob(_INSTANCE_JSON):
            if len(found.relative_to(inst).parts) <= 4:
                json_path = found
                break

    if json_path is None:
        return {}

    try:
        with json_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def _curseforge_last_played(data: dict, inst: Path) -> int | None:
    for key in ("lastPlayed", "last_played", "lastLaunch", "lastLaunchTime"):
        raw = data.get(key)
        if not raw:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        # Some manifests store milliseconds.
        return value // 1000 if value > 10**12 else value

    return _safe_mtime(inst)


def _first_of(data: dict, keys: tuple[str, ...], default):
    for key in keys:
        if data.get(key):
            return data[key]
    return default


def _safe_mtime(path: Path) -> int | None:
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return None


# -------------------------
# region FORMATTING
# -------------------------
def _epoch_to_date(epoch: int | None) -> str:
    if not epoch:
        return "never"
    try:
        return datetime.fromtimestamp(epoch).strftime("%d %B %Y")
    except (OSError, OverflowError, ValueError):
        return "never"


def _format_profiles(profiles: list[Profile]) -> list[Profile]:
    if not profiles:
        return profiles

    name_width = max(len(p.name) for p in profiles)
    version_width = max(len(p.game_version) for p in profiles)
    loader_width = max(len(p.mod_loader) for p in profiles)

    for p in profiles:
        p.display = (
            f"{p.name:<{name_width}}   "
            f"v{p.game_version:<{version_width}}   "
            f"{p.mod_loader:<{loader_width}}   "
            f"Last Played: {p.last_played}"
        )

    return profiles


def _format_worlds(worlds: list[World]) -> list[World]:
    if not worlds:
        return worlds

    name_width = max(len(w.name) for w in worlds)

    for w in worlds:
        w.display = f"{w.name:<{name_width}}   Last Played: {w.last_played}"

    return worlds
