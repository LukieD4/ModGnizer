"""Colorama bootstrap.

Import the names you need explicitly (``from py_imports import Fore, Style``).
Do NOT use ``from py_imports import *`` -- the wildcard is what made Fore/Style
invisible to every linter and IDE in the old layout.
"""

from colorama import init, Fore, Style

init(autoreset=True)

__all__ = ["Fore", "Style"]
