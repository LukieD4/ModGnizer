"""Gnizer -- a Minecraft mod organizer.

Entry point. This file owns the menu registry, the main loop, and build-id
handling -- and nothing else. Everything it used to do lives in a module named
after the job:

    py_ui         console primitives and the Cancelled exception
    py_getters    the Getter Menus
    py_actions    what each menu entry does
    py_paths      every path under %TEMP%/Gnizer
    py_managers   registry detection
    py_undbj      reading profiles and worlds
    py_archive    zip / 7z / rar behind one interface
    py_manifest   the share block, rendered and parsed together
    py_tmpfiles   upload and download
    py_report     diffing and installing
    py_secure     scanning extracted contents
"""

from __future__ import annotations

import sys
from pathlib import Path

import py_actions
import py_paths
import py_ui
from py_getters import GetterError
from py_imports import Fore, Style
from py_log import Log
from py_models import Context, Result
from py_ui import Cancelled


class App:
    VERSION_FILE = "buildId.version"
    MENU_TITLE = "Main Menu"

    # -------------------------
    # region RUNTIME
    # -------------------------
    @staticmethod
    def is_running_as_exe() -> bool:
        return "__compiled__" in globals()

    @staticmethod
    def get_runtime_base() -> Path:
        if "__compiled__" in globals():
            return Path(sys.argv[0]).resolve().parent
        return Path(__file__).parent

    @staticmethod
    def get_temp_runtime_base() -> Path:
        """Always the debundled folder when running as an EXE."""
        return Path(__file__).resolve().parent

    # -------------------------
    # region SETUP
    # -------------------------
    def __init__(self) -> None:
        self.log = Log()
        self.status: Result | None = None
        self.running = False

        # Keep the changelog on screen through the first cls().
        py_ui.skip_next_cls()

        accepted_update = self._check_for_updates()
        if not accepted_update:
            py_ui.cls()

        self.build_id, self.is_first_time_setup = self.load_or_init_build_id()
        self.ctx = Context(build_id=self.build_id, log=self.log, xaero_enabled=True)

    def _check_for_updates(self) -> bool:
        if not self.is_running_as_exe():
            return False

        # // Lazy import -- pulls in requests
        from py_updater import check_for_updates

        def consent() -> bool:
            # 'c' at the update prompt means "no", not "abort the launch".
            try:
                return py_ui.ask_consent("\n\n\n\n\nDo you want to update")
            except Cancelled:
                return False

        return check_for_updates(
            self.get_temp_runtime_base() / self.VERSION_FILE, consent, past_n=8
        )

    def load_or_init_build_id(self) -> tuple[int, bool]:
        version_path = self.get_temp_runtime_base() / self.VERSION_FILE

        # DEV MODE: first run
        if not version_path.exists() and not self.is_running_as_exe():
            print(Fore.BLUE + "First-time setup detected.\n (reserved)...")
            version_path.write_text("1", encoding="utf-8")
            return 1, True

        # EXE MODE: the id is baked in, never incremented
        if self.is_running_as_exe():
            try:
                return int(version_path.read_text().strip()), False
            except (OSError, ValueError):
                return 0, False

        # DEV MODE: bump on every run
        try:
            new_id = int(version_path.read_text().strip()) + 1
        except (OSError, ValueError):
            new_id = 1
        version_path.write_text(str(new_id), encoding="utf-8")
        return new_id, False

    # -------------------------
    # region MENU
    # -------------------------
    def menu_items(self) -> dict[str, tuple[str, object]]:
        """Rebuilt each render so the temp size and toggle states stay current."""
        temp_size = py_ui.format_bytes(py_paths.directory_size(py_paths.gnizer_temp()))
        logging_state = "ON" if self.log.enabled else "OFF"

        return {
            "1": ("Load *DATA* from an ARCHIVE (or link)", py_actions.load_data),
            "2": ("Bundle *MODS* to an ARCHIVE", py_actions.bundle_mods),
            "3": ("Bundle *WORLD* to an ARCHIVE", py_actions.bundle_world),
            "4": (f"Xaero prompts ({'ON' if self.ctx.xaero_enabled else 'OFF'})",
                  self.toggle_xaero),
            "5": (f"Clear temp cache & backups ({temp_size})",
                  py_actions.clear_temp_cache),
            "6": (f"Log errors ({logging_state})", self.toggle_error_logging),
            "#": ("Quit", self.quit),
        }

    def toggle_xaero(self, ctx: Context) -> Result:
        ctx.xaero_enabled = not ctx.xaero_enabled
        return Result.ok(f"Xaero prompts: {'ON' if ctx.xaero_enabled else 'OFF'}")

    def toggle_error_logging(self, ctx: Context) -> Result:
        """Now toggles both ways -- the old handler returned early once enabled."""
        enabled = self.log.toggle(ctx.build_id)
        return Result.ok(f"Error logging {'ENABLED' if enabled else 'DISABLED'}.")

    def quit(self, ctx: Context) -> Result:
        print(Fore.WHITE + "Goodbye.")
        self.running = False
        return Result.info("")

    # -------------------------
    # region LOOP
    # -------------------------
    def render(self, items: dict[str, tuple[str, object]]) -> None:
        py_ui.cls()
        py_ui.set_window_title(f"Gnizer v{self.build_id}")

        print(Fore.WHITE + f" // (Version {self.build_id}) //\nby @LukieD4 on GitHub\n")
        print(Style.BRIGHT + f"\n-x-x- {{ {self.MENU_TITLE} }} -x-x-\n")

        for key, (label, _) in items.items():
            print(Fore.LIGHTBLACK_EX + f"[{key}]. {label}")

        print("\n Enter a valid number from the list above ^")
        py_ui.note(" or 'c' to cancel current prompt when asked for input. ^\n")

        if self.status and self.status.message:
            print(f"\n{Fore.LIGHTBLACK_EX}[INFO: {self.status.render()}]")

    def run(self) -> None:
        self.running = True

        while self.running:
            items = self.menu_items()
            self.render(items)

            choice = input(Fore.WHITE + "> ").strip()
            entry = items.get(choice)

            if entry is None:
                self.status = Result.error("Invalid choice.")
                continue

            self.status = self.dispatch(entry[1])

    def dispatch(self, handler) -> Result:
        """Run one action, translating every exit route into a Result.

        This is the single place cancellation is handled. Actions no longer
        thread a ``None`` return value back up through five call frames.
        """
        self.log.info(f"ACTION -> {getattr(handler, '__name__', handler)}")

        try:
            return handler(self.ctx) or Result.info("")

        except Cancelled as exc:
            return Result.info(exc.message)

        except GetterError as exc:
            # Nothing to pick, clipboard unavailable, etc. -- expected and
            # explainable, so report the message rather than a traceback.
            return Result.error(exc.message)

        except KeyboardInterrupt:
            return Result.info("Cancelled.")

        except Exception as exc:
            # A genuine bug. Log the traceback and say so plainly rather than
            # dressing it up as an expected outcome.
            self.log.exception(exc)
            message = f"Unexpected error ({type(exc).__name__}): {exc}"
            if not self.log.enabled:
                message += "\n         Enable 'Log errors' to write a full traceback to err.log."
            return Result.error(message)


if __name__ == "__main__":
    App().run()
