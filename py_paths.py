"""Every path Gnizer writes to, in one place.

The old code rebuilt ``Path(os.environ.get("TEMP", ...)) / "Gnizer"`` in six
different functions, which is how the hash archive ended up being written
*inside* the directory that was about to be archived into it.

Layout under %TEMP%/Gnizer:

    bundled/                        finished mod + hash archives, ready to upload
    world_bundled/                  finished world archives
    hash_staging/                   the .md5.txt file, alone, so it can be zipped
                                    without the output archive landing inside it
    uploads/                        split parts on the way out
    downloaded_from_tmpfiles_org/   parts on the way in
    extracted_reassembled/          extraction targets
    backup_*/  world_backup_*/      pre-install backups
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def temp_root() -> Path:
    """The system temp directory, with a sane fallback."""
    return Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))


def gnizer_temp() -> Path:
    """Root of everything Gnizer owns. Cleared wholesale by the temp menu."""
    return temp_root() / "Gnizer"


# -------------------------
# region SUBDIRECTORIES
# -------------------------
def bundled_dir() -> Path:
    return gnizer_temp() / "bundled"


def world_bundled_dir() -> Path:
    return gnizer_temp() / "world_bundled"


def hash_staging_dir() -> Path:
    """Holds only the .md5.txt file.

    Kept separate from ``bundled_dir`` on purpose: this directory is the *input*
    to the hash archive, so the output must never live here.
    """
    return gnizer_temp() / "hash_staging"


def uploads_dir() -> Path:
    return gnizer_temp() / "uploads"


def downloads_dir() -> Path:
    return gnizer_temp() / "downloaded_from_tmpfiles_org"


def extracted_dir() -> Path:
    return gnizer_temp() / "extracted_reassembled"


def backup_dir(prefix: str = "backup") -> Path:
    """A fresh timestamped backup directory. Not created -- just named."""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return gnizer_temp() / f"{prefix}_{stamp}"


# -------------------------
# region HELPERS
# -------------------------
def ensure(path: Path) -> Path:
    """mkdir -p, returning the path so it can be used inline."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def directory_size(path: Path) -> int:
    """Total bytes of every file under ``path``. 0 if it does not exist."""
    if not path.exists():
        return 0
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            # A file vanishing mid-walk shouldn't break the main menu.
            continue
    return total


def clear_gnizer_temp() -> None:
    """Delete %TEMP%/Gnizer entirely. Raises OSError on failure."""
    import shutil  # // Lazy import

    target = gnizer_temp()
    if target.exists():
        shutil.rmtree(target)
