"""Archive creation and extraction behind one interface.

The old module had three ``bundle_*`` methods commented out and a
``bundle_content`` whose ``zip`` branch referenced ``self.source_folder``
(gone) and never assigned ``cmd`` (so the shared ``subprocess.run(cmd)`` at
the bottom raised NameError). ZIP -- the only format available without 7-Zip
or WinRAR installed -- could not produce an archive at all.

Now each format is one small class implementing the same three operations,
and the exit codes those tools return are translated into an ``ArchiveError``
carrying a ``.code``, so callers stop doing ``e.args[0]`` inside an except
block and crashing when the exception has no args.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

import py_paths

SEVENZ_PATH = Path(r"C:\Program Files\7-Zip\7z.exe")
WINRAR_PATH = Path(r"C:\Program Files\WinRAR\WinRAR.exe")

# Exit codes these tools use that we can explain to a human.
EXIT_NO_FILES = 10       # WinRAR: nothing matched the source wildcard
EXIT_WRONG_PASSWORD = 11  # WinRAR: bad password
EXIT_USER_CANCELLED = 255


class ArchiveError(Exception):
    """Any archive operation failure, with the tool's exit code attached."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ToolMissingError(ArchiveError):
    """The external archiver isn't installed."""


# -------------------------
# region ARCHIVERS
# -------------------------
class Archiver:
    """Interface. ``create`` and ``extract`` raise ArchiveError on failure."""

    fmt = ""
    label = ""
    supports_password = False

    @classmethod
    def is_available(cls) -> bool:
        raise NotImplementedError

    @classmethod
    def create(cls, source_dir: Path, output_file: Path,
               password: str | None = None) -> Path:
        raise NotImplementedError

    @classmethod
    def extract(cls, archive: Path, out_dir: Path,
                password: str | None = None) -> Path:
        raise NotImplementedError


class ZipArchiver(Archiver):
    fmt = "zip"
    label = "ZIP                                    / Windows Built-in"
    supports_password = False  # zipfile can read encrypted zips, not write them

    @classmethod
    def is_available(cls) -> bool:
        return True  # stdlib

    @classmethod
    def create(cls, source_dir: Path, output_file: Path,
               password: str | None = None) -> Path:
        files = [p for p in source_dir.rglob("*") if p.is_file()]
        if not files:
            raise ArchiveError("Nothing to archive.", EXIT_NO_FILES)

        py_paths.ensure(output_file.parent)

        with zipfile.ZipFile(
            output_file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as zf:
            for full_path in files:
                zf.write(full_path, full_path.relative_to(source_dir))

        return output_file

    @classmethod
    def extract(cls, archive: Path, out_dir: Path,
                password: str | None = None) -> Path:
        try:
            with zipfile.ZipFile(archive, "r") as zf:
                # extractall() sanitises member paths, so a crafted archive
                # can't escape out_dir.
                zf.extractall(out_dir, pwd=password.encode() if password else None)
        except RuntimeError as exc:
            # zipfile signals a bad password this way.
            raise ArchiveError(str(exc), EXIT_WRONG_PASSWORD) from exc
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"Archive is corrupted: {exc}") from exc
        return out_dir


class SevenZipArchiver(Archiver):
    fmt = "7z"
    label = "7Z  (password supported)               / https://www.7-zip.org/download.html"
    supports_password = True

    @classmethod
    def is_available(cls) -> bool:
        return SEVENZ_PATH.exists()

    @classmethod
    def create(cls, source_dir: Path, output_file: Path,
               password: str | None = None) -> Path:
        if not cls.is_available():
            raise ToolMissingError("7z.exe not found at expected path.")

        py_paths.ensure(output_file.parent)

        cmd = [
            str(SEVENZ_PATH),
            "a",
            "-t7z",       # 7z container
            "-r",         # recurse
            "-m0=lzma2",
            "-mx=9",      # ultra
            "-mmt=on",    # multithread
            "-ms=on",     # solid
            "-mfb=273",
            "-md=64m",
        ]
        if password:
            cmd += [f"-p{password}", "-mhe=on"]  # -mhe encrypts the file list too
        cmd += [str(output_file), str(source_dir / "*")]

        _run(cmd)
        return output_file

    @classmethod
    def extract(cls, archive: Path, out_dir: Path,
                password: str | None = None) -> Path:
        if not cls.is_available():
            raise ToolMissingError("7z.exe not found at expected path.")

        cmd = [str(SEVENZ_PATH), "x", str(archive), f"-o{out_dir}", "-y"]
        if password:
            cmd.append(f"-p{password}")

        _run(cmd)
        return out_dir


class RarArchiver(Archiver):
    fmt = "rar"
    label = "RAR (password supported) + recommended / https://www.win-rar.com/download.html"
    supports_password = True

    @classmethod
    def is_available(cls) -> bool:
        return WINRAR_PATH.exists()

    @classmethod
    def create(cls, source_dir: Path, output_file: Path,
               password: str | None = None) -> Path:
        if not cls.is_available():
            raise ToolMissingError("WinRAR.exe not found at expected path.")

        py_paths.ensure(output_file.parent)

        cmd = [
            str(WINRAR_PATH),
            "a",
            "-r",     # recurse
            "-ep1",   # strip the base path
            "-ma5",   # RAR5
            "-m5",    # best compression
            "-s",     # solid
            "-md64",
        ]
        if password:
            cmd.append(f"-hp{password}")  # encrypt data *and* file list
        cmd += [str(output_file), str(source_dir / "*")]

        _run(cmd)
        return output_file

    @classmethod
    def extract(cls, archive: Path, out_dir: Path,
                password: str | None = None) -> Path:
        if not cls.is_available():
            raise ToolMissingError("WinRAR.exe not found at expected path.")

        cmd = [str(WINRAR_PATH), "x", "-y"]
        if password:
            cmd.append(f"-p{password}")
        # WinRAR wants the destination to end in a separator.
        cmd += [str(archive), str(out_dir) + os.sep]

        _run(cmd)
        return out_dir


ARCHIVERS: dict[str, type[Archiver]] = {
    ZipArchiver.fmt: ZipArchiver,
    SevenZipArchiver.fmt: SevenZipArchiver,
    RarArchiver.fmt: RarArchiver,
}


def get_archiver(fmt: str) -> type[Archiver]:
    try:
        return ARCHIVERS[fmt]
    except KeyError:
        raise ArchiveError(f"Unknown archive format: {fmt}") from None


def available_formats() -> list[str]:
    return [fmt for fmt, cls in ARCHIVERS.items() if cls.is_available()]


# -------------------------
# region PUBLIC OPERATIONS
# -------------------------
def bundle_directory(source_dir: Path, output_file: Path, fmt: str,
                     password: str | None = None) -> Path:
    """Archive everything under ``source_dir`` into ``output_file``.

    ``output_file`` must not live inside ``source_dir`` -- that's what made the
    hash archive try to contain itself.
    """
    source_dir = Path(source_dir)
    output_file = Path(output_file)

    if not source_dir.exists() or not source_dir.is_dir():
        raise ArchiveError(f"Source folder not found: {source_dir}")

    if output_file.resolve().parent == source_dir.resolve() or \
            source_dir.resolve() in output_file.resolve().parents:
        raise ArchiveError(
            f"Refusing to write {output_file.name} inside the folder being archived."
        )

    # A stale archive from a previous run would otherwise be appended to by
    # 7z/WinRAR rather than replaced.
    if output_file.exists():
        output_file.unlink()

    return get_archiver(fmt).create(source_dir, output_file, password)


def extract_archive(archive_path: Path, password: str | None = None) -> Path:
    """Extract into a fresh timestamped folder and return it."""
    archive_path = Path(archive_path)
    if not archive_path.exists():
        raise ArchiveError(f"Archive not found: {archive_path}")

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    out_dir = py_paths.extracted_dir() / f"{archive_path.stem}_{stamp}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    py_paths.ensure(out_dir)

    name = archive_path.name.lower()
    for fmt in ("zip", "7z", "rar"):
        if fmt in name:
            return ARCHIVERS[fmt].extract(archive_path, out_dir, password)

    raise ArchiveError(f"Unrecognised archive type: {archive_path.name}")


def generate_md5_map(source_dir: Path) -> dict[str, str]:
    """Relative path -> MD5 hex digest, for every file under ``source_dir``."""
    source_dir = Path(source_dir)
    if not source_dir.exists() or not source_dir.is_dir():
        raise ArchiveError(f"Cannot hash missing folder: {source_dir}")

    md5_map: dict[str, str] = {}
    for full_path in sorted(source_dir.rglob("*")):
        if not full_path.is_file():
            continue
        hasher = hashlib.md5()
        with full_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                hasher.update(chunk)
        md5_map[str(full_path.relative_to(source_dir))] = hasher.hexdigest()

    return md5_map


def write_md5_file(md5_map: dict[str, str], target: Path,
                   pad_bytes: int = 1024) -> Path:
    """Write a ``<md5>  <relative path>`` listing.

    ``pad_bytes`` of random hex is interleaved between entries so the hash
    listing doesn't compress down to a size that leaks how many mods it
    describes. Pass 0 to disable.
    """
    py_paths.ensure(target.parent)
    with target.open("w", encoding="utf-8") as fh:
        for rel_path, md5 in md5_map.items():
            fh.write(f"{md5}  {rel_path}\n")
            if pad_bytes:
                fh.write(os.urandom(pad_bytes).hex() + "\n")
    return target


def explain_exit_code(error: ArchiveError, *, during: str = "extraction") -> str:
    """Turn a tool exit code into something a user can act on."""
    if isinstance(error, ToolMissingError):
        return error.message

    code = error.code

    if code == EXIT_NO_FILES and during == "bundling":
        return "There is nothing to bundle -- the folder is empty."
    if code in (EXIT_NO_FILES, EXIT_WRONG_PASSWORD):
        return "Incorrect password or archive is corrupted."
    if code == 3:
        return "Archive failed."
    if code == EXIT_USER_CANCELLED:
        return "User cancelled the operation."

    return error.message


# -------------------------
# region INTERNAL
# -------------------------
def _run(cmd: list[str]) -> None:
    """Run an archiver, converting its exit code into an ArchiveError."""
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise ArchiveError(
            f"{Path(cmd[0]).name} exited with code {exc.returncode}", exc.returncode
        ) from exc
    except FileNotFoundError as exc:
        raise ToolMissingError(f"{Path(cmd[0]).name} not found.") from exc
