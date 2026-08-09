"""Where mod managers live, and which ones are actually installed."""

from __future__ import annotations

import os
from pathlib import Path

from py_models import ModManager

# HKCU keys that prove an installation exists.
REGISTRY_MAP: dict[str, list[str]] = {
    "Modrinth": [r"Software\ModrinthApp\Modrinth App"],
    "CurseForge (Overwolf)": [r"Software\Overwolf\CurseForge"],
    "CurseForge": [r"Software\OverwolfElectron"],
}

# Where each manager keeps its profile data. "db" is a SQLite file for
# Modrinth and the instances directory for CurseForge -- py_undbj picks the
# reader based on which it gets.
PATH_MAP: dict[str, dict[str, str]] = {
    "Modrinth": {
        "db": r"%APPDATA%\ModrinthApp\app.db",
        "profiles": r"%APPDATA%\ModrinthApp\profiles",
    },
    "CurseForge (Overwolf)": {
        "db": r"%USERPROFILE%\curseforge\minecraft\Instances",
        "profiles": r"%USERPROFILE%\curseforge\minecraft\Instances",
    },
    "CurseForge": {
        "db": r"%USERPROFILE%\curseforge\minecraft\Instances",
        "profiles": r"%USERPROFILE%\curseforge\minecraft\Instances",
    },
}


def modrinth_profiles_root() -> Path:
    """Used by the Modrinth DB reader to verify a recorded profile still exists."""
    return Path(os.path.expandvars(PATH_MAP["Modrinth"]["profiles"]))


def is_installed(registry_paths: list[str]) -> bool:
    import winreg  # // Lazy import -- Windows only, and only needed on detection

    for reg_path in registry_paths:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path):
                return True
        except (FileNotFoundError, OSError):
            continue
    return False


def detect_mod_managers() -> list[ModManager]:
    """Every installed manager, in REGISTRY_MAP order."""
    found: list[ModManager] = []

    for name, registry_paths in REGISTRY_MAP.items():
        if not is_installed(registry_paths):
            continue
        paths = PATH_MAP[name]
        found.append(
            ModManager(
                name=name,
                profiles_path=Path(os.path.expandvars(paths["profiles"])),
                db_path=Path(os.path.expandvars(paths["db"])),
            )
        )

    return found
