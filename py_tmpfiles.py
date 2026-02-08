# py_tmpfiles.py
from __future__ import annotations
from typing import Dict, Any, List
from urllib.parse import urlparse
from py_imports import *
import requests


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
            "ModGnizer/1.0 (+https://tmpfiles.org)"
        )

    # -------------------------
    # FILE SPLITTING (helper)
    # -------------------------
    def _split_file_to_parts(self, file_path: Path, chunk_size: int) -> List[Path]:
        """
        Split file into zip-branded chunks written into:
        %TEMP%/ModGnizer/uploads/

        Part names: <original_filename>0.zip, <original_filename>1.zip, ...

        Returns list of part Path objects (ascending order).
        """
        file_path = Path(file_path)
        if not file_path.exists() or not file_path.is_file():
            raise TmpFilesError(f"File not found: {file_path}")

        # Resolve ModGnizer temp uploads dir
        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        uploads_dir = temp_root / "ModGnizer" / "uploads"
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

    def upload(self, file_path: Path) -> Dict[str, Any]:
        file_path = Path(file_path)

        if not file_path.exists() or not file_path.is_file():
            raise TmpFilesError(f"File not found: {file_path}")

        try:
            with file_path.open("rb") as f:
                resp = self.session.post(
                    self.UPLOAD_URL,
                    files={"file": (file_path.name, f)},
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
            single_resp = self.upload(file_path)  # existing method
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
        try:
            for p in parts:
                # Reuse the upload() method so we keep consistent request handling
                print(f"Uploading part: {p.name} ({p.stat().st_size} bytes)")
                resp = self.upload(p)
                link = resp.get("link") or resp.get("share_url") or resp.get("direct_url")
                if not link:
                    raise TmpFilesError(f"Upload succeeded but no link returned for part: {p.name}")
                links.append(link)
                payloads.append(resp.get("payload"))
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

    def download_from_paste(self, manifest: dict, cleanup_parts: bool = True) -> list[Path]:
        """
        Download parts described in a parsed MODGNIZER manifest.

        Args:
            manifest: dict returned by parse_modgnizer_manifest(), must contain "links" (ordered).
            cleanup_parts: if True, remove individual part files after successful reassembly/rename.

        Returns:
            List[Path] - if multiple parts were downloaded but not reassembled, returns all part paths.
                         If reassembled (or single part renamed), returns a single-element list with the final file path.

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
        for idx, link in enumerate(links, start=1):
            try:
                print(f"Downloading part [{idx}/{len(links)}]: {link}")
                p = self.download(link)
                downloaded_parts.append(p)
                print(f"Saved: {p}")
            except Exception as e:
                # Best-effort cleanup of any parts already downloaded
                for q in downloaded_parts:
                    try:
                        q.unlink(missing_ok=True)
                    except Exception:
                        pass
                raise TmpFilesError(f"Failed to download part {link}: {e}") from e

        # If only one part and we have an internal name, rename to preserve original filename
        internal_name = manifest.get("internal_name")
        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        download_dir = temp_root / "ModGnizer" / "downloaded_from_tmpfiles_org"

        if len(downloaded_parts) == 1:
            single = downloaded_parts[0]
            if internal_name:
                assembled = download_dir / internal_name
                try:
                    # If the downloaded file already has the correct name, skip rename
                    if single.resolve() == assembled.resolve():
                        return [assembled]

                    # If assembled exists, overwrite
                    if assembled.exists():
                        assembled.unlink()

                    single.replace(assembled)
                    return [assembled]

                except Exception as e:
                    raise TmpFilesError(f"Failed to rename downloaded file to internal name: {e}") from e

            return downloaded_parts


        # Multiple parts: if we have an internal_name, reassemble by concatenation
        if internal_name:
            assembled = download_dir / internal_name
            try:
                # Ensure parent exists
                assembled.parent.mkdir(parents=True, exist_ok=True)
                # Overwrite if exists
                if assembled.exists():
                    assembled.unlink()
                with assembled.open("wb") as out:
                    for part in downloaded_parts:
                        with part.open("rb") as pf:
                            while True:
                                chunk = pf.read(1024 * 1024)
                                if not chunk:
                                    break
                                out.write(chunk)
                # Optionally remove part files
                if cleanup_parts:
                    for part in downloaded_parts:
                        try:
                            part.unlink(missing_ok=True)
                        except Exception:
                            pass
                return [assembled]
            except Exception as e:
                # Attempt cleanup
                try:
                    assembled.unlink(missing_ok=True)
                except Exception:
                    pass
                raise TmpFilesError(f"Failed to reassemble parts: {e}") from e

        # No internal name: return the list of downloaded parts (caller must reassemble)
        return downloaded_parts


    def download(self, url: str) -> Path:
        """
        Downloads a tmpfiles.org file into %TEMP%\\ModGnizer\\
        Accepts either share URL or /dl/ direct URL.

        Returns:
            Path to downloaded file.
        """
        direct_url = self._ensure_direct_url(url)

        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        download_dir = temp_root / "ModGnizer" / "downloaded_from_tmpfiles_org"
        download_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(urlparse(direct_url).path).name
        if not filename:
            raise TmpFilesError("Could not determine filename from URL.")

        target_path = download_dir / filename

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

                with target_path.open("wb") as out:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            out.write(chunk)

        except requests.RequestException as e:
            raise TmpFilesError(f"Network error during download: {e}") from e

        if not target_path.exists() or target_path.stat().st_size == 0:
            raise TmpFilesError("Downloaded file is empty or missing.")

        return target_path

    # -------------------------
    # HELPERS
    # -------------------------

    def parse_modgnizer_manifest(raw_text: str) -> dict:
        """
        Parse a MODGNIZER markdown block or free text and return:
            {
                "internal_name": str,   # original filename (required if present)
                "size_bytes": Optional[int],
                "timestamp": Optional[str],
                "links": List[str]      # ordered list of tmpfiles.org URLs
            }
        Raises ModGnizerManifestError on malformed content.
        """
        if not raw_text or not raw_text.strip():
            raise ValueError("Manifest text is empty.")

        text = raw_text.strip()

        # Detect a MODGNIZER block (loose detection is fine)
        is_modgnizer = "# MODGNIZER" in text or "MODGNIZER" in text.splitlines()[0] if text else False

        # Extract tmpfiles.org urls (preserve order)
        urls = re.findall(r"https?://(?:www\.)?tmpfiles\.org/[^\s`]+", text, flags=re.IGNORECASE)

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
            }

        # Not an explicit MODGNIZER block — fallback to plain links
        if urls:
            return {"internal_name": None, "size_bytes": None, "timestamp": None, "links": urls}

        raise ValueError("Text does not contain MODGNIZER data or tmpfiles.org links.")

    def _ensure_direct_url(self, url: str) -> str:
        """
        Converts:
        http(s)://tmpfiles.org/12345/file.rar
        → https://tmpfiles.org/dl/12345/file.rar
        """
        if "/dl/" in url:
            return url.replace("http://", "https://")

        parsed = urlparse(url)
        if "tmpfiles.org" not in parsed.netloc:
            raise TmpFilesError("URL is not a tmpfiles.org link.")

        path = parsed.path.lstrip("/")
        return f"https://tmpfiles.org/dl/{path}"