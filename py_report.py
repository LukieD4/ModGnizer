"""Compare what's in the archive against what's installed, then install it.

``review_and_install_mods`` and ``install_world`` used to be two ~85%-identical
copies of the same algorithm -- same MD5 loop, same four buckets, same report
printer, same backup/wipe/install dance -- differing only in whether files were
keyed by name or by relative path. They've drifted apart at least once already.

Now there's one engine and two thin entry points, and the functions return a
Result instead of being handed a ``lambda text: setattr(app, ...)`` callback to
write the app's status line from the inside.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import py_paths
import py_ui
from py_imports import Fore
from py_models import Profile, Result, World


@dataclass
class Diff:
    identical: list[str] = field(default_factory=list)
    differing: list[str] = field(default_factory=list)
    only_in_archive: list[str] = field(default_factory=list)
    only_in_target: list[str] = field(default_factory=list)

    @property
    def change_count(self) -> int:
        return len(self.differing) + len(self.only_in_archive) + len(self.only_in_target)


# -------------------------
# region ENTRY POINTS
# -------------------------
def install_modlist(extracted_path: Path, profile: Profile) -> Result:
    """Replace a profile's mods folder with the contents of an archive."""
    mods_dir = profile.mods_dir

    if not mods_dir.exists():
        return Result.error(f"Mods folder not found: {mods_dir}")

    archive_files = _files_under(extracted_path)
    # Mod archives are flat, and so is a mods folder -- compare by filename.
    installed_files = [p for p in mods_dir.iterdir() if p.is_file()]

    diff = _compute_diff(
        {p.name: p for p in archive_files},
        {p.name: p for p in installed_files},
    )
    _print_report(diff, removed_label="Removed (in profile only)")

    if diff.change_count == 0:
        py_ui.success("\nNo differences detected. Nothing to install.")
        return Result.info("No changes detected.")

    py_ui.warn(f"\nDetected {diff.change_count} mismatched or new files.")

    if not _confirm_destructive(
        "Proceed with installation (this will replace your mods)",
        "WARNING: This will DELETE ALL existing mods in this profile.",
    ):
        return Result.info("Installation cancelled.")

    backup_root = py_paths.ensure(py_paths.backup_dir("backup"))

    try:
        for existing in installed_files:
            shutil.copy2(existing, backup_root / existing.name)
        for existing in installed_files:
            existing.unlink()
        for source in archive_files:
            shutil.copy2(source, mods_dir / source.name)
    except OSError as exc:
        return Result.error(f"Installation failed: {exc}")

    py_ui.success(f"\nInstallation complete. Backup saved to: {backup_root}")
    return Result.ok(f"Installed fresh modlist. Backup saved to: {backup_root}")


def install_world(extracted_path: Path, world: World) -> Result:
    """Replace (or create) a save folder from the contents of an archive."""
    target = world.path

    archive_files = _files_under(extracted_path)
    installed_files = _files_under(target) if target.exists() else []

    # Worlds are trees, so identity is the relative path, not the filename.
    diff = _compute_diff(
        {str(p.relative_to(extracted_path)): p for p in archive_files},
        {str(p.relative_to(target)): p for p in installed_files},
    )
    _print_report(diff, removed_label="Removed (in world only)")

    if diff.change_count == 0:
        py_ui.success("\nNo differences detected. Nothing to install.")
        return Result.info("No changes detected.")

    py_ui.warn(f"\nDetected {diff.change_count} mismatched or new files.")

    # A brand-new world has nothing to destroy, so skip both confirmations.
    if not world.is_new and not _confirm_destructive(
        "Proceed with world installation (this will replace the world)",
        "WARNING: This will DELETE the existing world folder.",
    ):
        return Result.info("Installation cancelled.")

    backup_root = py_paths.ensure(py_paths.backup_dir("world_backup"))

    try:
        if not world.is_new and target.exists():
            shutil.copytree(target, backup_root / world.name)
            shutil.rmtree(target)

        target.mkdir(parents=True, exist_ok=True)

        for source in archive_files:
            destination = target / source.relative_to(extracted_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    except OSError as exc:
        return Result.error(f"World installation failed: {exc}")

    py_ui.success(f"\nWorld installation complete. Backup saved to: {backup_root}")
    return Result.ok(f"Installed fresh world. Backup saved to: {backup_root}")


# -------------------------
# region ENGINE
# -------------------------
def _compute_diff(archive: dict[str, Path], target: dict[str, Path]) -> Diff:
    """Bucket two keyed file sets into identical / differing / added / removed."""
    diff = Diff()

    for key, archive_file in archive.items():
        target_file = target.get(key)
        if target_file is None:
            diff.only_in_archive.append(key)
        elif _md5(archive_file) == _md5(target_file):
            diff.identical.append(key)
        else:
            diff.differing.append(key)

    diff.only_in_target = [key for key in target if key not in archive]

    return diff


def _print_report(diff: Diff, *, removed_label: str) -> None:
    if diff.identical:
        py_ui.print_section(Fore.GREEN, "Identical", diff.identical)
    if diff.differing:
        py_ui.print_section(Fore.YELLOW, "Differing", diff.differing)
    if diff.only_in_archive:
        py_ui.print_section(Fore.CYAN, "New (in archive only)", diff.only_in_archive)
    if diff.only_in_target:
        py_ui.print_section(Fore.MAGENTA, removed_label, diff.only_in_target)


def _confirm_destructive(first: str, warning: str) -> bool:
    """Two confirmations, worded from opposite directions."""
    if not py_ui.ask_consent(Fore.YELLOW + first):
        return False

    print("\n" + Fore.RED + warning)
    return py_ui.ask_consent(Fore.RED + "Are you absolutely sure you want to continue")


def _files_under(root: Path) -> list[Path]:
    return [p for p in Path(root).rglob("*") if p.is_file()]


def _md5(path: Path, chunk_size: int = 8192) -> str:
    hasher = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
