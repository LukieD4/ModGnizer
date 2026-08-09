"""Toggleable file logger.

The old version could only be switched on -- ``menu_toggle_error_logging``
returned early if ``self.debug`` was already set. This one toggles both ways
and keeps every module's logging call identical (``log.info(...)``) whether
logging is on or off.
"""

from __future__ import annotations

import logging
from pathlib import Path

LOG_FILENAME = "err.log"


class Log:
    """A logger that is a no-op until enabled."""

    def __init__(self) -> None:
        self._logger: logging.Logger | None = None
        self.enabled = False

    # -------------------------
    # region TOGGLE
    # -------------------------
    def enable(self, build_id: int, log_path: Path | None = None) -> None:
        if self.enabled:
            return

        path = Path(log_path) if log_path else Path(LOG_FILENAME)

        logger = logging.getLogger(f"Gnizer {build_id}")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        # Drop any handler left over from a previous enable/disable cycle so we
        # never end up writing each line twice.
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        )
        logger.addHandler(handler)

        self._logger = logger
        self.enabled = True
        self.info("Started")

    def disable(self) -> None:
        if not self.enabled or self._logger is None:
            return

        self.info("Stopped")
        for handler in list(self._logger.handlers):
            self._logger.removeHandler(handler)
            handler.close()

        self._logger = None
        self.enabled = False

    def toggle(self, build_id: int) -> bool:
        """Flip logging on/off. Returns the new state."""
        if self.enabled:
            self.disable()
        else:
            self.enable(build_id)
        return self.enabled

    # -------------------------
    # region WRITE
    # -------------------------
    def info(self, msg: object) -> None:
        self._write(logging.INFO, msg)

    def warning(self, msg: object) -> None:
        self._write(logging.WARNING, msg)

    def error(self, msg: object) -> None:
        self._write(logging.ERROR, msg)

    def exception(self, msg: object) -> None:
        """Log an error together with the active traceback."""
        if self._logger is None:
            return
        self._logger.error(str(msg), exc_info=True)

    def _write(self, level: int, msg: object) -> None:
        if self._logger is None:
            return
        self._logger.log(level, str(msg))
