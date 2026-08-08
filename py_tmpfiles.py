# py_tmpfiles.py
from __future__ import annotations
from typing import Dict, Any, List
from py_imports import *

from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path
import requests, os, re, time


class TmpFilesError(Exception):
    """Raised for tmpfiles.org upload/download errors."""


class TmpFilesClient:
    UPLOAD_URL = "https://tmpfiles.org/api/v1/upload"
    DEFAULT_TIMEOUT = 120

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.setdefault(
            "User-Agent",
            "Gnizer/1.0 (+https://tmpfiles.org)"
        )

    # -------------------------
    # FILE SPLITTING (helper)
    # -------------------------
    def _split_file_to_parts(self, file_path: Path, chunk_size: int) -> List[Path]:
        """
        Split file into zip-branded chunks written into:
        %TEMP%/Gnizer/uploads/

        Part names: <original_filename>0.zip, <original_filename>1.zip, ...

        Returns list of part Path objects (ascending order).
        """
        file_path = Path(file_path)
        if not file_path.exists() or not file_path.is_file():
            raise TmpFilesError(f"File not found: {file_path}")

        # Resolve Gnizer temp uploads dir
        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        uploads_dir = temp_root / "Gnizer" / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        parts: List[Path] = []
        index = 0
        try:
            with file_path.open("rb") as src:
                while True:
                    chunk = src.read(chunk_size)
                    if not chunk:
                        break

                    # keep the original extension inside the name, but force .zip suffix
                    part_name = f"{file_path.name}{index}.zip"
                    part_path = uploads_dir / part_name

                    # If parts collide from a previous run, ensure we don't accidentally append to them
                    if part_path.exists():
                        try:
                            part_path.unlink()
                        except Exception:
                            pass

                    with part_path.open("wb") as out:
                        out.write(chunk)

                    parts.append(part_path)
                    index += 1

        except OSError as e:
            # Clean up any partial parts written
            for p in parts:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
            raise TmpFilesError(f"Failed splitting file: {e}") from e

        if not parts:
            raise TmpFilesError("Splitting resulted in no parts.")

        return parts

    # -------------------------
    # UPLOAD
    # -------------------------
    def _masked_upload_filename(self, file_path: Path) -> str:
        """
        Return a filename to present to the server that preserves the original
        filename and extension but forces a .zip suffix so the site accepts it.

        If the file already ends with .zip (e.g., chunked part), do NOT append
        another .zip.
        """
        base_name = Path(file_path).name

        # Avoid double-masking chunked parts
        if base_name.lower().endswith(".zip"):
            return base_name

        return f"{base_name}.zip"


    def upload(self, file_path: Path) -> Dict[str, Any]:
        file_path = Path(file_path)

        if not file_path.exists() or not file_path.is_file():
            raise TmpFilesError(f"File not found: {file_path}")

        masked_name = self._masked_upload_filename(file_path)

        try:
            with file_path.open("rb") as f:
                # Send the masked filename in the multipart form so the server sees .zip
                resp = self.session.post(
                    self.UPLOAD_URL,
                    files={"file": (masked_name, f)},
                    timeout=self.timeout,
                )
        except requests.RequestException as e:
            raise TmpFilesError(f"Network error during upload: {e}") from e

        if not resp.ok:
            raise TmpFilesError(
                f"HTTP {resp.status_code} during upload\n{resp.text[:1000]}"
            )

        try:
            payload = resp.json()
        except ValueError:
            raise TmpFilesError(
                "Upload response was not JSON:\n"
                f"{resp.text[:2000]}"
            )

        link = None
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and "url" in data:
                link = data["url"]
            elif "url" in payload:
                link = payload["url"]

        if not link:
            raise TmpFilesError(f"Upload succeeded but no URL found: {payload}")

        return {
            "link": link,  # ← restore expected key
            "share_url": link,
            "direct_url": self._ensure_direct_url(link),
            "payload": payload,
        }

    # -------------------------
    # CHUNKED UPLOAD (public)
    # -------------------------
    def upload_in_chunks(self, file_path: Path, chunk_size: int = 90 * 1024 * 1024, cleanup_parts: bool = True) -> Dict[str, Any]:
        """
        If file <= chunk_size → uploads as single file (behaves like upload()) and returns a dict:
            { "links": [shareable_link], "parts": [file_path], "payloads": [payloads...] }

        If file > chunk_size → splits file into parts and uploads each part separately.
        Returns:
            {
                "links": [link_part1, link_part2, ...],
                "parts": [Path(part1), Path(part2), ...],
                "payloads": [payload1, payload2, ...]
            }

        NOTE: After uploading parts, the consumer must re-assemble them in order:
            Reassembly instructions:
            - Download all parts into the same folder
            - Ensure correct order (…0.zip, …1.zip, …2.zip)

            Windows (CMD):
            copy /b mods.rar0.zip+mods.rar1.zip+mods.rar2.zip mods.rar

            Linux / macOS:
            cat mods.rar0.zip mods.rar1.zip mods.rar2.zip > mods.rar
        """

        file_path = Path(file_path)
        if not file_path.exists() or not file_path.is_file():
            raise TmpFilesError(f"File not found: {file_path}")

        file_size = file_path.stat().st_size
        if file_size <= chunk_size:
            # small enough for single upload
            single_resp = self.upload(file_path)  # existing method (now masked)
            return {
                "links": [single_resp["link"]],
                "parts": [file_path],
                "payloads": [single_resp.get("payload")]
            }

        # split then upload
        parts = self._split_file_to_parts(file_path, chunk_size)
        links: List[str] = []
        payloads: List[Any] = []

        print("Chunking ...")

        # --- NEW: progress tracking ---
        uploaded_bytes = 0
        start_time = datetime.now()

        try:
            for p in parts:
                part_size = p.stat().st_size

                # --- NEW: compute remaining + ETA ---
                bytes_left = file_size - uploaded_bytes
                elapsed = (datetime.now() - start_time).total_seconds()
                speed = uploaded_bytes / elapsed if elapsed > 0 else 0
                eta_seconds = (bytes_left / speed) if speed > 0 else float("inf")

                if eta_seconds == float("inf"):
                    eta_str = "calculating..."
                else:
                    mins, secs = divmod(int(eta_seconds), 60)
                    eta_str = f"{mins}m {secs}s"

                print(f"Uploading part: {p.name} ({part_size} bytes)")
                print(f"         *Remaining: {bytes_left:,} bytes*\n        ->ETA: {eta_str}")

                # Reuse the upload() method so we keep consistent request handling
                resp = self.upload(p)
                link = resp.get("link") or resp.get("share_url") or resp.get("direct_url")
                if not link:
                    raise TmpFilesError(f"Upload succeeded but no link returned for part: {p.name}")
                links.append(link)
                payloads.append(resp.get("payload"))

                uploaded_bytes += part_size  # update progress

        except Exception as e:
            # Attempt best-effort cleanup of parts on failure
            for p in parts:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
            raise TmpFilesError(f"Chunked upload failed: {e}") from e

        # Optionally remove parts after successful upload
        if cleanup_parts:
            for p in parts:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    # don't block the overall success if part deletion fails
                    pass

        return {"links": links, "parts": parts, "payloads": payloads}


    # -------------------------
    # DOWNLOAD
    # -------------------------

    def download_from_paste(self, manifest: dict, cleanup_parts: bool = True) -> tuple[Path, Path]:
        """
        Download parts described in a parsed MODGNIZER manifest.

        Args:
            manifest: dict returned by parse_modgnizer_manifest(), must contain "links" (ordered).
            cleanup_parts: if True, remove individual part files after successful reassembly/rename.

        Returns:
            tuple[Path, Path] - a tuple containing the path to the first downloaded part (MD5 hash archive) and the path to the reassembled file.

        Raises:
            TmpFilesError on any failure.
        """
        if not isinstance(manifest, dict):
            raise TmpFilesError("Manifest must be a dict.")

        links = manifest.get("links") or []
        if not links:
            raise TmpFilesError("Manifest contains no links to download.")

        # Download each link using existing download() method
        downloaded_parts: list[Path] = []

        total_parts = len(links)
        total_bytes_expected = manifest.get("size_bytes")
        bytes_downloaded = 0

        # A transfer sample only counts toward the speed estimate once it lasted at
        # least this long. Tiny files (like the MD5 hash archive that always downloads
        # first) can arrive in a single buffered read in a fraction of a millisecond,
        # which would otherwise turn into a wildly noisy bytes/sec reading.
        MIN_RELIABLE_TRANSFER_SECONDS = 0.1
        speed_sample_bytes = 0
        speed_sample_seconds = 0.0

        for idx, link in enumerate(links, start=1):
            try:
                # --- ETA: speed is measured only from the transfer phase of parts
                # already downloaded (connection setup / TTFB excluded), starting with
                # the small MD5 hash archive, then projected onto the remaining bytes
                # of the manifest's known size ---
                speed = (
                    speed_sample_bytes / speed_sample_seconds
                    if speed_sample_seconds >= MIN_RELIABLE_TRANSFER_SECONDS
                    else 0
                )

                if speed > 0 and total_bytes_expected:
                    bytes_left = max(total_bytes_expected - bytes_downloaded, 0)
                    eta_seconds = bytes_left / speed
                    speed_str = (
                        f"{speed / 1024:.1f} KB/s"
                        if speed < 1024 * 1024
                        else f"{speed / (1024 * 1024):.2f} MB/s"
                    )
                else:
                    eta_seconds = float("inf")
                    speed_str = "calculating..."

                if eta_seconds == float("inf"):
                    eta_str = "calculating..."
                else:
                    mins, secs = divmod(int(eta_seconds), 60)
                    eta_str = f"{mins}m {secs}s"

                print(f"Downloading part [{idx}/{total_parts}]: {link}")
                print(f"        ->Speed: {speed_str}  ->ETA: {eta_str}")

                p, transfer_seconds = self._download_with_timing(link)
                part_size = p.stat().st_size

                downloaded_parts.append(p)
                bytes_downloaded += part_size

                if transfer_seconds >= MIN_RELIABLE_TRANSFER_SECONDS:
                    speed_sample_bytes += part_size
                    speed_sample_seconds += transfer_seconds

                print(f"Saved: {p} ({part_size:,} bytes, transfer {transfer_seconds:.2f}s)")

            except Exception as e:
                raise TmpFilesError(f"Failed to download part {link}: {e}") from e

        # If only one part and we have an internal name, rename to preserve original filename
        # internal_name = manifest.get("internal_name")
        # temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        # download_dir = temp_root / "Gnizer" / "downloaded_from_tmpfiles_org"

        return_path = downloaded_parts[1]

        if len(links) > 2:
            # Reassemble parts into a single file, not including the MD5 hash archive (first part)
            reassembled_name = manifest.get("internal_name") or "reassembled_file"
            reassembled_path = downloaded_parts[1].parent / reassembled_name
            reassembled_parent = reassembled_path.parent

            return_path = reassembled_path

            with reassembled_path.open("wb") as out:
                for part in downloaded_parts[1:]:  # skip the first part (MD5 hash archive)
                    with part.open("rb") as p:
                        out.write(p.read())



        return Path(links[0]), return_path # return the first part (MD5 hash archive) and the reassembled file path


    def download(self, url: str) -> Path:
        """
        Downloads a tmpfiles.org file into %TEMP%\\Gnizer\\
        Accepts either share URL or /dl/ direct URL.

        Returns:
            Path to downloaded file.
        """
        path, _ = self._download_with_timing(url)
        return path

    def _download_webpage(self, url: str) -> str:
        """
        Downloads a tmpfiles.org webpage (HTML) and returns the text.
        Raises TmpFilesError on failure.
        """
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if not resp.ok:
                raise TmpFilesError(
                    f"HTTP {resp.status_code} during webpage download\n"
                    f"{resp.text[:500]}"
                )

            # It will spit out the HTML and we need `href="https://tmpfiles.org/dl/1786186406.be0342cad24006bf/wjwXF8NMZFsE/example_md5.rar.zip"`
            matches = re.findall(
                r'href="(https://tmpfiles\.org/dl/[^"]+)"',
                resp.text,
                flags=re.IGNORECASE,
            )

            if not matches:
                raise TmpFilesError(
                    "Could not find a direct tmpfiles.org download URL "
                    "in the webpage."
                )

            return matches[0]
        
        except requests.RequestException as e:
            raise TmpFilesError(f"Network error during webpage download: {e}") from e

    def _download_with_timing(self, url: str) -> "tuple[Path, float]":
        """
        Same as download(), but also returns how long the response body took to
        stream in (seconds), measured from after the response headers arrive to
        the last byte written. That excludes DNS/TCP-connect/TLS-handshake/TTFB,
        so it reflects real transfer speed rather than one-time connection overhead.
        """
        direct_url = self._download_webpage(url=self._ensure_direct_url(url))

        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        download_dir = temp_root / "Gnizer" / "downloaded_from_tmpfiles_org"
        download_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(urlparse(direct_url).path).name
        if not filename:
            raise TmpFilesError("Could not determine filename from URL.")

        # NEW: unmask the filename if needed
        final_name = self._unmask_filename(filename)

        target_path = download_dir / final_name

        transfer_seconds = 0.0

        try:
            with self.session.get(
                direct_url,
                stream=True,
                timeout=self.timeout,
            ) as resp:
                if not resp.ok:
                    raise TmpFilesError(
                        f"HTTP {resp.status_code} during download\n"
                        f"{resp.text[:500]}"
                    )

                # Headers have arrived by this point; only the body read below
                # counts toward the transfer time.
                transfer_start = time.perf_counter()
                with target_path.open("wb") as out:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            out.write(chunk)
                transfer_seconds = time.perf_counter() - transfer_start

        except requests.RequestException as e:
            raise TmpFilesError(f"Network error during download: {e}") from e

        if not target_path.exists() or target_path.stat().st_size == 0:
            raise TmpFilesError("Downloaded file is empty or missing.")

        return target_path, transfer_seconds

    # -------------------------
    # HELPERS
    # -------------------------

    def parse_gnizer_manifest(raw_text: str) -> dict:
        """
        Parse a MODGNIZER markdown block or free text and return:
            {
                "internal_name": str,   # original filename (required if present)
                "size_bytes": Optional[int],
                "timestamp": Optional[str],
                "links": List[str]      # ordered list of tmpfiles.org URLs
            }
        Raises GnizerManifestError on malformed content.
        """
        if not raw_text or not raw_text.strip():
            raise ValueError("Manifest text is empty.")

        text = raw_text.strip()

        # Detect a GNIZER block (loose detection is fine)
        header_split_text = text.splitlines()[0].lower()
        shareType_split_text = text.splitlines()[2].lower()
        is_modlist = "modlist" in shareType_split_text
        is_savefolder = "savefolder" in shareType_split_text
        is_modgnizer = "gnizer" in header_split_text

        if not is_modlist and not is_savefolder:
            raise ValueError("Manifest doesn't know how to handle this. Did you highlight and copy everything?")

        type_of_install = "modlist" if is_modlist else "savefolder"

        # Extract tmpfiles.org urls; must decode base64 (preserve order)
        import base64
        data_split_text = text.splitlines()[17]
        data_decoded = base64.b64decode(data_split_text).decode("utf-8")
        urls = re.findall(r"https?://(?:www\.)?tmpfiles\.org/[^\s`]+", data_decoded, flags=re.IGNORECASE)

        if is_modgnizer:
            # Try to extract internal name
            internal_name = None
            m_name = re.search(r'Internal name:\s*"(.*?)"', text, flags=re.IGNORECASE)
            if m_name:
                internal_name = m_name.group(1).strip()

            # Size (optional)
            m_size = re.search(r'Size of modlist:\s*([0-9]+)\s*bytes', text, flags=re.IGNORECASE)
            size_bytes = int(m_size.group(1)) if m_size else None

            # Timestamp (optional)
            m_ts = re.search(r'Date of modlist:\s*([0-9:\- \w]+)', text, flags=re.IGNORECASE)
            timestamp = m_ts.group(1).strip() if m_ts else None

            if not urls:
                raise ValueError("Found MODGNIZER header but no tmpfiles.org links were detected.")

            return {
                "internal_name": internal_name,
                "size_bytes": size_bytes,
                "timestamp": timestamp,
                "links": urls,
                "is_modlist": is_modlist,
                "is_savefolder": is_savefolder, 
                "type_of_install": type_of_install
            }

        # Not an explicit MODGNIZER block — fallback to plain links
        if urls:
            raise Exception("Something went wrong in digesting the manifest")

        raise ValueError("Text does not contain MODGNIZER data or tmpfiles.org links.")
    
    def _unmask_filename(self, filename: str) -> str:
        """
        If the uploaded file was masked (e.g., mods.7z.zip), remove the final .zip
        so the user receives the original filename (mods.7z).

        Rules:
            - If filename ends with .zip but contains another extension before it,
            strip only the final .zip.
            - If the file is a real .zip (e.g., archive.zip), keep it unchanged.
        """
        lower = filename.lower()
        if lower.endswith(".zip"):
            # Remove only the last .zip
            base = filename[:-4]
            # If base still has an extension, we assume masking was used
            if "." in base:
                return base
        return filename


    def _ensure_direct_url(self, url: str) -> str:
        """
        Converts:
        http(s)://tmpfiles.org/12345/file.rar
        → https://tmpfiles.org/dl/12345/file.rar
        """

        # IMPORTANT: 07/08/2026, just found out the scheme changed.

        # https://tmpfiles.org/wtwJF6Cl3t7P/test_md5.rar.zip can no longer translate to https://tmpfiles.org/dl/wtwJF6Cl3t7P/test_md5.rar.zip

        # it gets fitted with an ID upon upload now and does NOT return in the .json response (we have to have a workaround).
        # https://tmpfiles.org/dl/1786139746.4ca658c92dcedbd6/wtwJF6Cl3t7P/test_md5.rar.zip

        # We need to find a workaround because the chunks keep downloading the html page instead of the actual file.


        if "/dl/" in url:
            return url.replace("http://", "https://")

        # Follow the redirect to capture the real download URL.
        # try:
        #     response = requests.get(url, allow_redirects=True, timeout=10)
        #     response.json(key="url")  # Ensure it's a tmpfiles.org response
        # except Exception as exc:
        #     raise TmpFilesError(f"Failed to resolve tmpfiles.org link: {exc}")        

        parsed = urlparse(url)
        if "tmpfiles.org" not in parsed.netloc:
            raise TmpFilesError("URL is not a tmpfiles.org link.")

        path = parsed.path.lstrip("/")
        return f"https://tmpfiles.org/dl/{path}"
