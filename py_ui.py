"""Console primitives: the building blocks every Getter Menu is made of.

Nothing in here knows what a mod, a profile or an archive is.

The important change is ``Cancelled``. In the old code every prompt returned
``None`` on 'c', so each action was a ladder of ``if not x: return True`` --
and because ``return True`` also meant "the menu loop should keep running",
success, cancellation and failure were indistinguishable at the call site.
Now a cancel raises, the main loop catches it once, and actions read top to
bottom.
"""

from __future__ import annotations

import os
from typing import Callable, Sequence

from py_imports import Fore, Style

CANCEL_WORDS = ("c", "q")
DIVIDER = "-- -x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x- --"

_cls_skip = 0


class Cancelled(Exception):
    """Raised when the user backs out of a prompt with 'c' or 'q'."""

    def __init__(self, message: str = "Cancelled.") -> None:
        super().__init__(message)
        self.message = message


# -------------------------
# region SCREEN
# -------------------------
def skip_next_cls(count: int = 1) -> None:
    """Keep the next ``count`` screens (used so the changelog stays readable)."""
    global _cls_skip
    _cls_skip = count


def cls(force: bool = False) -> None:
    global _cls_skip
    if _cls_skip > 0 and not force:
        _cls_skip -= 1
        return
    os.system("cls")


def set_window_title(title: str) -> None:
    os.system(f"title {title}")


# -------------------------
# region OUTPUT
# -------------------------
def divider() -> None:
    print("\n" + DIVIDER)


def heading(text: str) -> None:
    print(Style.BRIGHT + f"\n-x-x- {{ {text} }} -x-x-\n")


def note(text: str) -> None:
    print(Fore.LIGHTBLACK_EX + text)


def info(text: str) -> None:
    print(Fore.BLUE + text)


def success(text: str) -> None:
    print(Fore.GREEN + text)


def warn(text: str) -> None:
    print(Fore.YELLOW + text)


def error(text: str) -> None:
    print(Fore.RED + text)


def print_section(colour: str, title: str, items: Sequence[str]) -> None:
    print(colour + f"{title} ({len(items)}):")
    for item in items:
        print(Fore.LIGHTBLACK_EX + f"  - {item}")
    print()


def format_bytes(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# -------------------------
# region INPUT
# -------------------------
def ask(prompt: str, validator: Callable[[str], object | None], *,
        error_message: str = "Invalid input.",
        cancel_words: Sequence[str] = CANCEL_WORDS):
    """Prompt until ``validator`` returns a non-None value.

    ``validator`` may also raise, in which case its message is shown and the
    prompt repeats. Entering a cancel word raises ``Cancelled``.
    """
    while True:
        raw = input(prompt).strip()

        if raw.lower() in cancel_words:
            raise Cancelled()

        try:
            value = validator(raw)
        except Cancelled:
            raise
        except Exception as exc:
            print(Fore.RED + f"{error_message} ({exc})")
            continue

        if value is None:
            print(Fore.RED + error_message)
            continue

        return value


def ask_text(prompt: str, *, allow_empty: bool = False,
             cancel_words: Sequence[str] = CANCEL_WORDS) -> str:
    """Free text. Returns '' only when ``allow_empty``."""
    def validator(raw: str) -> str | None:
        if not raw and not allow_empty:
            return None
        return raw

    if allow_empty:
        raw = input(prompt).strip()
        if raw.lower() in cancel_words:
            raise Cancelled()
        return raw

    return ask(prompt, validator, error_message="Please enter a value.",
               cancel_words=cancel_words)


def ask_number(max_value: int, *, prompt: str | None = None) -> int:
    """A 1-based selection between 1 and ``max_value``."""
    if prompt is None:
        prompt = Fore.WHITE + "> "

    def validator(raw: str) -> int | None:
        if not raw.isdigit():
            return None
        n = int(raw)
        return n if 1 <= n <= max_value else None

    return ask(
        prompt,
        validator,
        error_message=f"Enter a number between 1 and {max_value}, or 'c' to cancel.",
    )


def ask_choice(labels: Sequence[str], *, title: str | None = None,
               before: Callable[[], None] | None = None) -> int:
    """Print a numbered list and return the 0-based index chosen."""
    if before:
        before()

    for i, label in enumerate(labels, 1):
        print(Fore.LIGHTBLACK_EX + f"[{i}]. {label}")

    if title:
        heading(title)

    return ask_number(len(labels)) - 1


def ask_consent(message: str) -> bool:
    """y/n. 'c' raises Cancelled rather than returning False.

    The distinction matters: "no, don't install" and "abort this whole
    operation" used to collapse into the same value.
    """
    while True:
        print(Fore.YELLOW + f"{message}? (y/n)  (or 'c' to cancel)")
        choice = input(Fore.YELLOW + "> ").strip().lower()

        if choice in ("y", "yes"):
            return True
        if choice in ("n", "no"):
            return False
        if choice in CANCEL_WORDS:
            raise Cancelled()

        print(Fore.RED + "Invalid selection. Please enter 'y' or 'n', or 'c' to cancel.")


def ask_password(prompt: str, *, required: bool) -> str | None:
    """Returns the password, or None when blank input is allowed and given."""
    while True:
        raw = input(Fore.YELLOW + prompt).strip()

        if raw.lower() in CANCEL_WORDS:
            raise Cancelled()

        if raw:
            return raw

        if not required:
            return None

        print(Fore.RED + "Password is required for this format. Enter a password or 'c' to cancel.")


def pause(message: str) -> None:
    input(message)
