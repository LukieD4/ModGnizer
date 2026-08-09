"""Recover the Minecraft version and mod loader from a profile's game logs.

Modrinth dropped both fields from its database, so profiles used to display
"vUnavailable   Unavailable". The launcher still writes them into the game log
on every launch, which means we can read them back -- at the cost of the
profile having been played at least once.

Three log dialects cover essentially everything, and all three also name the
loader:

  ModLauncher (Forge / NeoForge)
    args [--username, x, --version, 1.18.2, ... --fml.forgeVersion, 40.3.12,
    --fml.mcVersion, 1.18.2, ...]

  Fabric / Quilt
    Loading Minecraft 1.21.5 with Fabric Loader 0.16.14

  Legacy Forge (1.12.2 and older)
    Forge Mod Loader version 14.23.5.2860 for Minecraft 1.12.2 loading

Measured against 35 real profiles: matching only ``--version`` finds 16.
All three patterns together find 33, every one within the first 4 lines. The
remaining 2 had a rotated ``latest.log`` that began mid-session, so we fall
back to the newest gzipped archives, which recovers both.
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from pathlib import Path

# The launch header is always at the very top of a session. Reading further is
# wasted I/O on logs that can reach tens of megabytes.
MAX_LINES = 60

# How many rotated archives to try when latest.log has already scrolled past
# the header. Newest first.
MAX_ARCHIVES = 3

_MC_VERSION = r"([0-9][0-9A-Za-z.\-_]*)"

# Preferred over --version: --version occasionally carries a launcher profile
# name ("fabric-loader-0.16.14-1.21.5") rather than the game version.
_FML_MC_VERSION_RE = re.compile(rf"--fml\.mcVersion,\s*{_MC_VERSION}\s*[,\]]")
_ARG_VERSION_RE = re.compile(rf"--version,\s*{_MC_VERSION}\s*[,\]]")
_NEOFORGE_RE = re.compile(r"--fml\.neoForgeVersion", re.I)
_FORGE_ARG_RE = re.compile(r"--fml\.forgeVersion|forgeclient", re.I)

_FABRIC_RE = re.compile(
    rf"Loading Minecraft\s+{_MC_VERSION}\s+with\s+(\S+)\s+Loader", re.I
)
_LEGACY_FORGE_RE = re.compile(
    rf"Mod Loader version\s+\S+\s+for Minecraft\s+{_MC_VERSION}", re.I
)

UNKNOWN = "Unavailable"


@dataclass(frozen=True)
class GameInfo:
    version: str = UNKNOWN
    loader: str = UNKNOWN

    @property
    def found(self) -> bool:
        return self.version != UNKNOWN


def read_game_info(profile_root: Path) -> GameInfo:
    """Best-effort version/loader for a profile. Never raises."""
    logs_dir = Path(profile_root) / "logs"
    if not logs_dir.is_dir():
        return GameInfo()

    for log_path, opener in _candidate_logs(logs_dir):
        try:
            info = _scan(log_path, opener)
        except OSError:
            # An unreadable or half-written log shouldn't break the profile list.
            continue
        if info.found:
            return info

    return GameInfo()


def _candidate_logs(logs_dir: Path):
    """latest.log first, then the newest rotated archives."""
    latest = logs_dir / "latest.log"
    if latest.is_file():
        yield latest, _open_text

    try:
        # debug-*.log.gz holds the same session with far more noise; skip it.
        archives = [
            p for p in logs_dir.glob("*.log.gz")
            if not p.name.startswith("debug")
        ]
        archives.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return

    for archive in archives[:MAX_ARCHIVES]:
        yield archive, _open_gzip


def _open_text(path: Path):
    return path.open("r", encoding="utf-8", errors="replace")


def _open_gzip(path: Path):
    return gzip.open(path, "rt", encoding="utf-8", errors="replace")


def _scan(path: Path, opener) -> GameInfo:
    with opener(path) as fh:
        for lineno, line in enumerate(fh, 1):
            if lineno > MAX_LINES:
                break
            info = _match_line(line)
            if info.found:
                return info
    return GameInfo()


def _match_line(line: str) -> GameInfo:
    # ModLauncher: the whole arg list is on one line, so the loader is here too.
    match = _FML_MC_VERSION_RE.search(line) or _ARG_VERSION_RE.search(line)
    if match:
        return GameInfo(version=match.group(1), loader=_modlauncher_loader(line))

    match = _FABRIC_RE.search(line)
    if match:
        # group(2) is "Fabric" or "Quilt".
        return GameInfo(version=match.group(1), loader=match.group(2).title())

    match = _LEGACY_FORGE_RE.search(line)
    if match:
        return GameInfo(version=match.group(1), loader="Forge")

    return GameInfo()


def _modlauncher_loader(line: str) -> str:
    if _NEOFORGE_RE.search(line):
        return "NeoForge"
    if _FORGE_ARG_RE.search(line):
        return "Forge"
    return UNKNOWN
