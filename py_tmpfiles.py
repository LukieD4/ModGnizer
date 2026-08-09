"""tmpfiles.org transport: split, upload, download, reassemble.

Manifest rendering/parsing moved to py_manifest -- this module now only moves
bytes. Two fixes worth naming:

* ``download_from_paste`` used to return ``Path(links[0])`` as the hash-archive
  path, which is a *URL string* wrapped in a Path, not a file that exists. It
  now returns the file it actually downloaded.
* A single-link manifest indexed ``downloaded_parts[1]`` and raised IndexError.

Note on the upload site: as of 07/08/2026 tmpfiles.org no longer lets you build
the /dl/ URL by string substitution -- an ID is injected at upload time and is
absent from the JSON response. ``_resolve_direct_url`` scrapes the share page
for the real link, which is why every download costs two requests.
"""

from __future__ import annotations

import itertools
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List
from urllib.parse import urlparse

import requests

import py_paths
from py_ui import format_bytes

DEFAULT_CHUNK_SIZE = 90 * 1024 * 1024

# A transfer only counts toward the speed estimate once it lasted this long.
# The MD5 archive is tiny and can arrive inside one buffered read, which would
# otherwise produce a nonsense bytes/sec figure for the whole session.
MIN_RELIABLE_TRANSFER_SECONDS = 0.025


class TmpFilesError(Exception):
    """Raised for tmpfiles.org upload/download errors."""


class TmpFilesClient:
    UPLOAD_URL = "https://tmpfiles.org/api/v1/upload"
    DEFAULT_TIMEOUT = 120

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.setdefault(
            "User-Agent", "LukieD4s ModGnizer/1.0 (+https://tmpfiles.org)"
        )

    # -------------------------
    # region UPLOAD
    # -------------------------
    def upload(self, file_path: Path) -> str:
        """Upload one file, return its share URL."""
        file_path = Path(file_path)
        if not file_path.is_file():
            raise TmpFilesError(f"File not found: {file_path}")

        try:
            with file_path.open("rb") as fh:
                resp = self.session.post(
                    self.UPLOAD_URL,
                    files={"file": (self._masked_name(file_path), fh)},
                    timeout=self.timeout,
                )
        except requests.RequestException as exc:
            raise TmpFilesError(f"Network error during upload: {exc}") from exc

        if not resp.ok:
            raise TmpFilesError(
                f"HTTP {resp.status_code} during upload\n{resp.text[:1000]}"
            )

        try:
            payload = resp.json()
        except ValueError:
            raise TmpFilesError(
                f"Upload response was not JSON:\n{resp.text[:2000]}"
            ) from None

        link = self._extract_link(payload)
        if not link:
            raise TmpFilesError(f"Upload succeeded but no URL found: {payload}")

        return link

    def upload_in_chunks(self, file_path: Path,
                         chunk_size: int = DEFAULT_CHUNK_SIZE,
                         cleanup_parts: bool = True) -> List[str]:
        """Upload a file, splitting it first if it exceeds ``chunk_size``.

        Returns the share URLs in reassembly order.
        """
        file_path = Path(file_path)
        if not file_path.is_file():
            raise TmpFilesError(f"File not found: {file_path}")

        file_size = file_path.stat().st_size
        if file_size <= chunk_size:
            # Small enough to go in one request. This used to happen in total
            # silence, which made it look like the first *chunked* file was
            # the first file uploaded.
            print(f"Uploading whole: {file_path.name} "
                  f"({format_bytes(file_size)}, single part)")
            return [self.upload(file_path)]

        parts = self._split_into_parts(file_path, chunk_size)
        links: List[str] = []

        print("Chunking ...")
        uploaded = 0
        started = datetime.now()

        try:
            for part in parts:
                part_size = part.stat().st_size

                remaining = file_size - uploaded
                elapsed = (datetime.now() - started).total_seconds()
                speed = uploaded / elapsed if elapsed > 0 else 0

                print(f"Uploading part: {part.name} ({format_bytes(part_size)})")
                print(f"         *Remaining: {format_bytes(remaining)}*"
                      f"\n        ->ETA: {_format_eta(remaining, speed)}")

                links.append(self.upload(part))
                uploaded += part_size

        except Exception as exc:
            self._cleanup(parts)
            raise TmpFilesError(f"Chunked upload failed: {exc}") from exc

        if cleanup_parts:
            self._cleanup(parts)

        return links

    # -------------------------
    # region DOWNLOAD
    # -------------------------
    def download_manifest(self, manifest) -> tuple[Path, Path]:
        """Download every part of a ShareManifest.

        Returns ``(hash_archive_path, payload_path)``. The payload is
        reassembled from parts 1..n when the share was chunked.
        """
        links = list(manifest.links)
        if len(links) < 2:
            raise TmpFilesError(
                "This share is incomplete -- it should contain a hash archive "
                "and at least one data part."
            )

        total_parts = len(links)
        expected_bytes = manifest.size_bytes
        downloaded_bytes = 0
        sample_bytes = 0
        sample_seconds = 0.0

        parts: list[Path] = []

        for index, link in enumerate(links, start=1):
            # Speed is measured only from the body-transfer phase of parts
            # already finished, so DNS/TLS/TTFB don't drag the estimate down.
            speed = (
                sample_bytes / sample_seconds
                if sample_seconds >= MIN_RELIABLE_TRANSFER_SECONDS
                else 0
            )
            remaining = (
                max(expected_bytes - downloaded_bytes, 0) if expected_bytes else 0
            )

            print(f"Downloading part [{index}/{total_parts}]: {link}")
            print(f"        ->Speed: {_format_speed(speed)}"
                  f"  ->ETA: {_format_eta(remaining, speed) if expected_bytes else 'calculating...'}")

            try:
                part, transfer_seconds = self._download(link)
            except TmpFilesError:
                raise
            except Exception as exc:
                raise TmpFilesError(f"Failed to download part {link}: {exc}") from exc

            part_size = part.stat().st_size
            parts.append(part)
            downloaded_bytes += part_size

            if transfer_seconds >= MIN_RELIABLE_TRANSFER_SECONDS:
                sample_bytes += part_size
                sample_seconds += transfer_seconds

            print(f"Saved: {part} ({part_size:,} bytes, transfer {transfer_seconds:.2f}s)")

        hash_archive = parts[0]
        payload_parts = parts[1:]

        if len(payload_parts) == 1:
            return hash_archive, payload_parts[0]

        return hash_archive, self._reassemble(payload_parts, manifest.internal_name)

    def _reassemble(self, parts: list[Path], internal_name: str | None) -> Path:
        target = parts[0].parent / (internal_name or "reassembled_file")

        with target.open("wb") as out:
            for part in parts:
                with part.open("rb") as fh:
                    # Stream rather than part.read() -- these are ~90MB each.
                    for chunk in iter(lambda fh=fh: fh.read(1024 * 1024), b""):
                        out.write(chunk)

        return target

    def _download(self, url: str) -> tuple[Path, float]:
        """Fetch one part. Returns its path and the body-transfer duration."""
        direct_url = self._resolve_direct_url(self._ensure_dl_path(url))

        download_dir = py_paths.ensure(py_paths.downloads_dir())

        filename = Path(urlparse(direct_url).path).name
        if not filename:
            raise TmpFilesError("Could not determine filename from URL.")

        target = download_dir / self._unmask_name(filename)

        try:
            with self.session.get(direct_url, stream=True, timeout=self.timeout) as resp:
                if not resp.ok:
                    raise TmpFilesError(
                        f"HTTP {resp.status_code} during download\n{resp.text[:500]}"
                    )

                # Headers have arrived; only the body read below is timed.
                started = time.perf_counter()
                with target.open("wb") as out:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            out.write(chunk)
                transfer_seconds = time.perf_counter() - started

        except requests.RequestException as exc:
            raise TmpFilesError(f"Network error during download: {exc}") from exc

        if not target.exists() or target.stat().st_size == 0:
            raise TmpFilesError("Downloaded file is empty or missing.")

        return target, transfer_seconds

    def _resolve_direct_url(self, url: str) -> str:
        """Scrape the share page for the real /dl/ link (see module docstring)."""
        import re  # // Lazy import

        try:
            resp = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise TmpFilesError(f"Network error resolving download link: {exc}") from exc

        if not resp.ok:
            raise TmpFilesError(
                f"HTTP {resp.status_code} resolving download link\n{resp.text[:500]}"
            )

        matches = re.findall(
            r'href="(https://tmpfiles\.org/dl/[^"]+)"', resp.text, flags=re.IGNORECASE
        )
        if not matches:
            raise TmpFilesError(
                "Could not find a direct tmpfiles.org download URL on the page."
            )

        return matches[0]

    # -------------------------
    # region HELPERS
    # -------------------------
    def _split_into_parts(self, file_path: Path, chunk_size: int) -> List[Path]:
        """Split into ``<name>0.zip``, ``<name>1.zip``, ... under %TEMP%/Gnizer/uploads."""
        uploads = py_paths.ensure(py_paths.uploads_dir())

        parts: List[Path] = []
        try:
            with file_path.open("rb") as src:
                for index in itertools.count():
                    chunk = src.read(chunk_size)
                    if not chunk:
                        break

                    part = uploads / f"{file_path.name}{index}.zip"
                    # Overwrite rather than append to a leftover from a past run.
                    part.unlink(missing_ok=True)
                    part.write_bytes(chunk)
                    parts.append(part)

        except OSError as exc:
            self._cleanup(parts)
            raise TmpFilesError(f"Failed splitting file: {exc}") from exc

        if not parts:
            raise TmpFilesError("Splitting resulted in no parts.")

        return parts

    @staticmethod
    def _cleanup(parts: List[Path]) -> None:
        for part in parts:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                # Never let a failed cleanup mask the real outcome.
                pass

    @staticmethod
    def _masked_name(file_path: Path) -> str:
        """tmpfiles.org only accepts certain extensions, so everything ships as .zip."""
        name = Path(file_path).name
        return name if name.lower().endswith(".zip") else f"{name}.zip"

    @staticmethod
    def _unmask_name(filename: str) -> str:
        """Undo ``_masked_name``: mods.7z.zip -> mods.7z, archive.zip unchanged."""
        if filename.lower().endswith(".zip"):
            base = filename[:-4]
            if "." in base:
                return base
        return filename

    @staticmethod
    def _extract_link(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if isinstance(data, dict) and "url" in data:
            return data["url"]
        return payload.get("url")

    @staticmethod
    def _ensure_dl_path(url: str) -> str:
        if "/dl/" in url:
            return url.replace("http://", "https://")

        parsed = urlparse(url)
        if "tmpfiles.org" not in parsed.netloc:
            raise TmpFilesError("URL is not a tmpfiles.org link.")

        return f"https://tmpfiles.org/dl/{parsed.path.lstrip('/')}"


# -------------------------
# region FORMATTING
# -------------------------
def _format_eta(bytes_left: float, speed: float) -> str:
    if speed <= 0 or bytes_left <= 0:
        return "calculating..."
    mins, secs = divmod(int(bytes_left / speed), 60)
    return f"{mins}m {secs}s"


def _format_speed(speed: float) -> str:
    if speed <= 0:
        return "calculating..."
    if speed < 1024 * 1024:
        return f"{speed / 1024:.1f} KB/s"
    return f"{speed / (1024 * 1024):.2f} MB/s"
