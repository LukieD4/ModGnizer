"""Typed values passed between getters, actions and adapters.

The old code moved everything around as bare dicts, so a mistyped key
(``chosen_world["fake_world"]``) only failed at runtime, deep inside an
action, where a broad ``except Exception`` turned it into a polite message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from py_imports import Fore

# Install kinds. The scanner in py_secure infers these from archive contents;
# the share manifest also carries one, and the two are compared to detect a
# tampered share.
INSTALL_MODLIST = "modlist"
INSTALL_SAVEFOLDER = "savefolder"
INSTALL_UNKNOWN = "unknown"


# -------------------------
# region DOMAIN
# -------------------------
@dataclass(frozen=True)
class ModManager:
    """A detected Modrinth / CurseForge installation."""

    name: str
    profiles_path: Path
    db_path: Path


@dataclass
class Profile:
    """One instance inside a mod manager.

    All three directories are resolved once, at discovery time, by the reader
    that knows the manager's layout. The old code stored a single ``path`` that
    meant the profile root for Modrinth but the mods folder for CurseForge, and
    then re-derived ``profiles_path / folder / "mods"`` at the call site -- two
    sources of truth that disagreed for CurseForge instances using the
    ``minecraft/mods`` layout.
    """

    root: Path
    mods_dir: Path
    saves_dir: Path
    folder: str
    name: str
    game_version: str
    mod_loader: str
    last_played: str
    display: str = ""


@dataclass
class World:
    """A save folder, or the synthetic 'create a new world' entry."""

    path: Path
    name: str
    last_played: str
    is_new: bool = False
    display: str = ""


@dataclass(frozen=True)
class ArchivePrefs:
    format: str  # "zip" | "7z" | "rar"
    password: str | None = None


@dataclass(frozen=True)
class ShareManifest:
    """A parsed Gnizer share block.

    ``links[0]`` is always the MD5 hash archive; the remaining links are the
    payload parts in order.
    """

    internal_name: str | None
    size_bytes: int | None
    timestamp: str | None
    links: list[str]
    install_type: str

    @property
    def payload_links(self) -> list[str]:
        return self.links[1:]


# -------------------------
# region CONTROL FLOW
# -------------------------
@dataclass(frozen=True)
class Result:
    """What an action reports back to the main loop.

    Replaces the old ``self.operation_text`` side-channel and the
    ``return True`` that meant "keep looping" rather than "succeeded".
    """

    message: str
    level: str = "ok"  # "ok" | "info" | "error"

    @classmethod
    def ok(cls, message: str) -> "Result":
        return cls(message, "ok")

    @classmethod
    def info(cls, message: str) -> "Result":
        return cls(message, "info")

    @classmethod
    def error(cls, message: str) -> "Result":
        return cls(message, "error")

    def render(self) -> str:
        colour = {"ok": Fore.GREEN, "info": Fore.LIGHTBLACK_EX, "error": Fore.RED}
        return colour.get(self.level, Fore.WHITE) + self.message


@dataclass
class Context:
    """The small amount of app state actions genuinely need."""

    build_id: int
    log: object  # py_log.Log
    xaero_enabled: bool = True
    extras: dict = field(default_factory=dict)
