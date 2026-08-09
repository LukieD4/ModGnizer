"""Getter Menus: present a list, return the thing the user picked.

Same scheme as before -- one ``get_*`` per domain concept -- but each getter
now returns a typed object and raises ``Cancelled`` instead of returning None,
so callers stop needing a ``if not x: return True`` after every single line.

A getter raises ``NothingToPick`` when the list is legitimately empty (no mod
managers installed, no profiles, no worlds). That's a different situation from
the user backing out, and the two used to be indistinguishable.
"""

from __future__ import annotations

from pathlib import Path

import pyperclip

import py_archive
import py_managers
import py_ui
import py_undbj
from py_imports import Fore
from py_models import ArchivePrefs, ModManager, Profile, World


class GetterError(Exception):
    """An expected, explainable failure while gathering input.

    Distinct from Cancelled (the user backed out) and from an unexpected
    exception (a bug). The main loop turns these into a plain error message.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NothingToPick(GetterError):
    """The list a getter would have shown is empty."""


class ClipboardUnavailable(GetterError):
    """The OS clipboard could not be read or written."""


# -------------------------
# region MOD MANAGERS / PROFILES / WORLDS
# -------------------------
def get_mod_manager() -> ModManager:
    py_ui.heading("Select a **MOD MANAGER**")

    managers = py_managers.detect_mod_managers()
    if not managers:
        print(Fore.WHITE + "Please install Modrinth or CurseForge and try again.")
        raise NothingToPick("No supported mod managers were detected.")

    for i, manager in enumerate(managers, 1):
        print(Fore.LIGHTBLACK_EX + f"{i}. {manager.name}")

    return managers[py_ui.ask_number(len(managers)) - 1]


def get_mod_profile(manager: ModManager) -> Profile:
    profiles = py_undbj.read_profiles(manager)
    if not profiles:
        raise NothingToPick("No profiles were detected in this mod manager.")

    py_ui.divider()
    return profiles[
        py_ui.ask_choice(
            [p.display for p in profiles], title="Select a **MOD PROFILE**"
        )
    ]


def get_world(saves_dir: Path, *, allow_new: bool = False) -> World:
    worlds = py_undbj.read_worlds(saves_dir, allow_new=allow_new)
    # if not worlds:
    #     raise NothingToPick(f"No worlds were detected in {saves_dir}")

    py_ui.divider()
    return worlds[
        py_ui.ask_choice([w.display for w in worlds], title="Select a **WORLD**")
    ]


def get_new_world_name(saves_dir: Path) -> str:
    """Validate a name for a world that doesn't exist yet."""
    import re  # // Lazy import

    def validator(raw: str) -> str | None:
        if not raw:
            return None
        if not re.match(r"^[A-Za-z0-9 _-]+$", raw):
            raise ValueError("Name contains invalid characters")
        if (saves_dir / raw).exists():
            raise ValueError("A directory with that name already exists")
        return raw

    return py_ui.ask(
        f"{Fore.WHITE}\nEnter a Name for your New World:\n > ",
        validator,
        error_message="Invalid world name.",
    )


# -------------------------
# region ARCHIVE
# -------------------------
def get_archive_preferences() -> ArchivePrefs:
    """Pick a format, and a password if the format supports one.

    The old menu printed all three options and *then* checked availability, so
    it advertised 7z/RAR on machines without them and rejected the choice after
    the fact. Unavailable formats are now labelled as such up front.
    """
    formats = ["zip", "7z", "rar"]

    py_ui.heading("Choose **ARCHIVE** Format")
    py_ui.note("Note: Some options may need to be installed separately.\n")

    labels = []
    for fmt in formats:
        archiver = py_archive.get_archiver(fmt)
        suffix = "" if archiver.is_available() else "   [not installed]"
        labels.append(archiver.label + suffix)

    while True:
        index = py_ui.ask_choice(labels)
        archiver = py_archive.get_archiver(formats[index])

        if not archiver.is_available():
            py_ui.error(
                f"{archiver.fmt.upper()} isn't installed on this machine. "
                "Pick another format or 'c' to cancel."
            )
            continue

        if not archiver.supports_password:
            print(Fore.WHITE + "Zipping...")
            return ArchivePrefs(format=archiver.fmt, password=None)

        password = py_ui.ask_password(
            "\nEnter a password for the archive (required, or 'c' to cancel): ",
            required=True,
        )
        return ArchivePrefs(format=archiver.fmt, password=password)


def get_shared_text() -> str:
    """Read a Gnizer share block from the clipboard.

    The local-file source was removed in the last release; the dead '2force'
    branch and the menu entry that advertised an unavailable option are gone
    with it, so this is now a single confirmation rather than a choice.
    """
    import py_manifest  # // Lazy import

    py_ui.heading("Load *DATA* From Clipboard")
    py_ui.note("or 'c' to cancel and return to the previous menu. ^\n")

    while True:
        print(Fore.CYAN + "Press ENTER to read Gnizer data from your clipboard."
              + Fore.LIGHTBLACK_EX + "\n(Make sure you copied the entire shared block)")
        py_ui.ask_text(Fore.WHITE + "> ", allow_empty=True)

        text = read_clipboard()

        if not text:
            py_ui.error("Clipboard is empty. Try again, or press 'c' to cancel.")
            continue

        if not py_manifest.looks_like_share(text):
            py_ui.error(
                "Clipboard content is not a valid Gnizer share. "
                "Try again, or press 'c' to cancel."
            )
            continue

        return text


def get_archive_password() -> str | None:
    """Password for an incoming archive. Blank means unencrypted."""
    return py_ui.ask_password(
        "\nEnter password for archive (leave blank if none): ", required=False
    )


# -------------------------
# region CONSENT
# -------------------------
def confirm_overwrite(file_path: Path) -> bool:
    """Ask before replacing an existing output archive. True = go ahead."""
    if not file_path.exists():
        return True

    print(Fore.RED + f"\nThe file already exists:\n{file_path}")
    if not py_ui.ask_consent(Fore.RED + "Delete it and continue"):
        return False

    try:
        import send2trash  # // Lazy import

        send2trash.send2trash(str(file_path))
        py_ui.info("Existing file moved to Recycle Bin.")
        return True
    except Exception as exc:
        py_ui.error(f"Failed to delete file: {exc}")
        return False


def confirm_upload(archive_names: list[str]) -> bool:
    py_ui.warn("\n--> Note: tmpfiles.org automatically deletes uploads after 60 minutes. <--")
    py_ui.error("--> [!] if your archive is NOT password protected, others could download! <--")

    listed = "\n".join(f"● {name}" for name in archive_names)
    return py_ui.ask_consent(
        f"Files due to upload:\n{listed}\n"
        "Would you like to send these to tmpfiles.org to share with friends"
    )


# -------------------------
# region CLIPBOARD / SHELL
# -------------------------
def read_clipboard() -> str | None:
    """Clipboard text, or None if it's empty or holds something non-textual."""
    try:
        text = pyperclip.paste()
    except pyperclip.PyperclipException as exc:
        raise ClipboardUnavailable(f"Could not read the clipboard: {exc}") from exc

    return text if text and text.strip() else None


def write_clipboard(text: str) -> None:
    try:
        pyperclip.copy(text)
    except pyperclip.PyperclipException as exc:
        raise ClipboardUnavailable(f"Could not write to the clipboard: {exc}") from exc


def open_in_shell(path: Path) -> None:
    """Hand a path to Windows -- Explorer for folders, default app for files."""
    import os  # // Lazy import

    try:
        os.startfile(path)
    except OSError as exc:
        py_ui.error(f"Failed to open {path}: {exc}")


def reveal_in_explorer(path: Path) -> None:
    open_in_shell(path.parent if path.exists() else path)
