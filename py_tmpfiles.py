"""tmpfiles.org transport: split, upload, download, reassemble.

Manifest rendering/parsing moved to py_manifest -- this module now only moves
bytes. Two fixes worth naming:

* ``download_from_paste`` used to return ``Path(links[0])`` as the hash-archive
  path, which is a *URL string* wrapped in a Path, not a file that exists. It
  now returns the file it actually downloaded.
* A single-link manifest indexed ``downloaded_parts[1]`` and raised IndexError.
* Parts were concatenated at whatever size they arrived. A split produces
  full-size parts with one short tail, so ``download_manifest`` now holds each
  part to that shape (py_secure.check_payload_sizes) -- a part appended to a
  share leaves a short one in the middle, which is caught before the appended
  part is even fetched.

Note on the upload site: as of 07/08/2026 tmpfiles.org no longer lets you build
the /dl/ URL by string substitution -- an ID is injected at upload time and is
absent from the JSON response. ``_scrape_page`` reads the real link off the
share page, which is why every download costs two requests. That same page
also carries "File expires in N minutes", so ``preflight`` collects both in
one pass -- the upload-order check in py_secure is free of extra traffic.
"""

from __future__ import annotations

import itertools
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List
from urllib.parse import urlparse

import requests

import py_paths
import py_secure
from py_models import UPLOAD_CHUNK_SIZE
from py_ui import format_bytes

# Per https://tmpfiles.org/api the upload endpoint accepts:
#   file    (required) File to upload, max 100 MB
#   expire  (optional) Seconds until deletion (60-172800), default 3600
#
# 3600 is already the server default, but we send it explicitly on every
# upload. Relying on a remote default means someone else's config change
# silently decides how long a user's archive sits on a public host -- and the
# 60-minute figure is promised to the user in the upload warning and in the
# share block, so it needs to be ours to keep.
EXPIRY_SECONDS = 60 * 60
EXPIRY_MIN_SECONDS = 60
EXPIRY_MAX_SECONDS = 172800

# Hard server-side cap on a single upload. DEFAULT_CHUNK_SIZE must stay below
# it, otherwise every split part is rejected with an opaque HTTP error.
#
# The chunk size itself now lives in py_models: both the part count a manifest
# is allowed to declare and the size each downloaded part is checked against
# are derived from it, and a second copy of the number is a second thing to
# forget to change.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_CHUNK_SIZE = UPLOAD_CHUNK_SIZE

# A transfer only counts toward the speed estimate once it lasted this long.
# The MD5 archive is tiny and can arrive inside one buffered read, which would
# otherwise produce a nonsense bytes/sec figure for the whole session.
MIN_RELIABLE_TRANSFER_SECONDS = 0.025


class TmpFilesError(Exception):
    """Raised for tmpfiles.org upload/download errors."""


class TamperedShareError(TmpFilesError):
    """What came down the wire isn't what the share block described.

    A subclass, so every existing handler still catches it -- but this is not
    a transfer failure and callers that report it shouldn't say "download
    failed" when the download went perfectly.
    """


@dataclass(frozen=True)
class PartMeta:
    """What the share page told us about one part, before downloading it."""

    label: str
    link: str
    direct_url: str
    remaining_seconds: int | None
    expiry_instant: float | None


# Observed live: <p class="file-meta">File expires in 51 minutes</p>
# Tolerant on purpose -- units and wording may vary ("1 hour", "30 seconds"),
# and an unparseable page must degrade to "unknown", never to a hard failure.
_FILE_META_RE = re.compile(r"<p[^>]*file-meta[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_EXPIRES_RE = re.compile(r"expires?\s+in\s+([^<]*)", re.IGNORECASE)
_DURATION_RE = re.compile(
    r"(\d+)\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)", re.IGNORECASE
)
_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1}


def _parse_remaining_seconds(html: str) -> int | None:
    """Seconds until deletion, read off the share page. None if unreadable."""
    meta = _FILE_META_RE.search(html)
    haystack = meta.group(1) if meta else html

    expires = _EXPIRES_RE.search(haystack)
    if not expires:
        return None

    total = 0
    found = False
    for amount, unit in _DURATION_RE.findall(expires.group(1)):
        total += int(amount) * _UNIT_SECONDS[unit[0].lower()]
        found = True

    return total if found else None


def _expiry_seconds() -> int:
    """The expiry we send, checked against the API's accepted range.

    A plain ``assert`` would be useless here: the release build compiles with
    --python-flag=no_asserts, so it would be stripped out of the EXE.
    """
    if not EXPIRY_MIN_SECONDS <= EXPIRY_SECONDS <= EXPIRY_MAX_SECONDS:
        raise TmpFilesError(
            f"EXPIRY_SECONDS is set to {EXPIRY_SECONDS}, outside the range "
            f"tmpfiles.org accepts ({EXPIRY_MIN_SECONDS}-{EXPIRY_MAX_SECONDS})."
        )
    return EXPIRY_SECONDS


if DEFAULT_CHUNK_SIZE > MAX_UPLOAD_BYTES:
    raise TmpFilesError(
        f"DEFAULT_CHUNK_SIZE ({DEFAULT_CHUNK_SIZE}) exceeds the per-file "
        f"upload limit ({MAX_UPLOAD_BYTES}); every split part would be rejected."
    )


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
        """Upload one file with a 60-minute expiry, return its share URL."""
        file_path = Path(file_path)
        if not file_path.is_file():
            raise TmpFilesError(f"File not found: {file_path}")

        size = file_path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            # Caught here rather than letting the server reject it, so the
            # message names the real cause instead of an HTTP status.
            raise TmpFilesError(
                f"{file_path.name} is {format_bytes(size)}, over the "
                f"{format_bytes(MAX_UPLOAD_BYTES)} per-file limit. "
                "It should have been split into parts first."
            )

        try:
            with file_path.open("rb") as fh:
                resp = self.session.post(
                    self.UPLOAD_URL,
                    files={"file": (self._masked_name(file_path), fh)},
                    data={"expire": str(_expiry_seconds())},
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
    def download_manifest(self, manifest,
                          metas: list[PartMeta] | None = None) -> tuple[Path, Path]:
        """Download every part of a ShareManifest.

        Returns ``(hash_archive_path, payload_path)``. The payload is
        reassembled from parts 1..n when the share was chunked.

        Pass the ``metas`` from ``preflight()`` to reuse the already-resolved
        download URLs; otherwise this resolves them itself.
        """
        links = list(manifest.links)
        if len(links) < 2:
            raise TmpFilesError(
                "This share is incomplete -- it should contain a hash archive "
                "and at least one data part."
            )

        if metas is None:
            metas = self.preflight(manifest)

        total_parts = len(links)
        expected_bytes = manifest.size_bytes
        downloaded_bytes = 0
        sample_bytes = 0
        sample_seconds = 0.0

        parts: list[Path] = []

        for index, meta in enumerate(metas, start=1):
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

            print(f"Downloading part [{index}/{total_parts}]: {meta.link}")
            print(f"        ->Speed: {_format_speed(speed)}"
                  f"  ->ETA: {_format_eta(remaining, speed) if expected_bytes else 'calculating...'}")

            try:
                part, transfer_seconds = self._download(meta.direct_url)
            except TmpFilesError:
                raise
            except Exception as exc:
                raise TmpFilesError(
                    f"Failed to download part {meta.link}: {exc}"
                ) from exc

            part_size = part.stat().st_size
            parts.append(part)
            downloaded_bytes += part_size

            # Checked as we go, not just at the end: an appended part shows up
            # as the *previous* part being short, so this fails one part before
            # the added one would have been fetched.
            self._verify_payload_sizes(
                [p.stat().st_size for p in parts[1:]], total_parts - 1
            )

            if transfer_seconds >= MIN_RELIABLE_TRANSFER_SECONDS:
                sample_bytes += part_size
                sample_seconds += transfer_seconds

            print(f"Saved: {part} ({part_size:,} bytes, transfer {transfer_seconds:.2f}s)")

        hash_archive = parts[0]
        payload_parts = parts[1:]

        self._verify_payload_sizes(
            [part.stat().st_size for part in payload_parts],
            len(payload_parts),
            declared_total=expected_bytes,
        )

        if len(payload_parts) == 1:
            return hash_archive, payload_parts[0]

        return hash_archive, self._reassemble(payload_parts, manifest.internal_name)

    @staticmethod
    def _verify_payload_sizes(sizes: list[int], expected_parts: int,
                              declared_total: int | None = None) -> None:
        """Refuse a payload whose parts aren't the shape a split produces."""
        known: list[int | None] = list(sizes)
        known += [None] * (expected_parts - len(known))

        problems = py_secure.check_payload_sizes(
            known, DEFAULT_CHUNK_SIZE, declared_total
        )
        if not problems:
            return

        raise TamperedShareError(
            "The downloaded parts are not the size this share says they are, "
            "so it is not\nthe file your friend bundled:\n  - "
            + "\n  - ".join(problems)
            + "\nAsk your friend to re-send the whole block."
        )

    def _reassemble(self, parts: list[Path], internal_name: str | None) -> Path:
        target = parts[0].parent / (internal_name or "reassembled_file")

        with target.open("wb") as out:
            for part in parts:
                with part.open("rb") as fh:
                    # Stream rather than part.read() -- these are ~90MB each.
                    for chunk in iter(lambda fh=fh: fh.read(1024 * 1024), b""):
                        out.write(chunk)

        return target

    def _download(self, direct_url: str) -> tuple[Path, float]:
        """Fetch one part from an already-resolved /dl/ URL.

        Returns its path and the body-transfer duration.
        """
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

    # -------------------------
    # region PRE-FLIGHT
    # -------------------------
    def preflight(self, manifest) -> list["PartMeta"]:
        """Resolve every part's download URL and expiry before downloading.

        Costs nothing extra: the share page we already have to fetch in order
        to find the /dl/ link is the same page that carries the expiry. Doing
        it up front means an expired or reordered share is caught in seconds
        rather than after several hundred megabytes have come down the wire.
        """
        metas: list[PartMeta] = []
        total = len(manifest.links)

        for index, link in enumerate(manifest.links):
            print(f"Checking part [{index + 1}/{total}] ...")
            direct_url, remaining = self._scrape_page(self._ensure_dl_path(link))

            metas.append(
                PartMeta(
                    label="MD5 hashes" if index == 0 else f"payload part {index}",
                    link=link,
                    direct_url=direct_url,
                    remaining_seconds=remaining,
                    # Absolute instant, so the delay between these sequential
                    # page reads cannot masquerade as an ordering problem.
                    expiry_instant=(
                        time.monotonic() + remaining if remaining is not None else None
                    ),
                )
            )

        return metas

    def _scrape_page(self, url: str) -> tuple[str, int | None]:
        """Return the real /dl/ link and the remaining lifetime, in seconds.

        The /dl/ URL cannot be built by string substitution any more (see the
        module docstring), so it has to be read off the page. Expiry is read
        from the same response; it is best-effort, and a None simply means the
        upload-order check skips this part rather than blocking the download.
        """
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

        return matches[0], _parse_remaining_seconds(resp.text)

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
