"""The Gnizer share block: rendered and parsed in one file.

Previously the template lived in ``py_main.save_links_md_and_copy_to_clipboard``
and the parser lived in ``py_tmpfiles.parse_gnizer_manifest``, coupled by line
number -- ``text.splitlines()[17]`` for the payload and ``[2]`` for the share
type. Adding a single line to the template would have silently broken every
share, in a different file, with no error until a friend pasted one in.

The wire format is unchanged, so blocks produced by older builds still parse.
Parsing is now structural: it looks for the ``**Data**`` marker rather than
counting lines. Run this module directly to check the round-trip.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime

from py_models import INSTALL_MODLIST, INSTALL_SAVEFOLDER, ShareManifest

DATA_MARKER = "**Data**"
FENCE = "```"

# Every decoded line must match this exactly -- see _validate_links.
#
# The trailing .zip is not a guess: TmpFilesClient._masked_name forces a .zip
# suffix on every upload because tmpfiles.org rejects other extensions, so a
# link that doesn't end in .zip did not come out of Gnizer. The 1-3 path
# segments cover both the share form (/<token>/<file>) and the direct form
# (/dl/<id>/<token>/<file>).
_STRICT_LINK_RE = re.compile(
    r"^https?://(?:www\.)?tmpfiles\.org/(?:[A-Za-z0-9._-]+/){1,3}"
    r"[A-Za-z0-9._%+-]+\.zip$",
    re.IGNORECASE,
)

# A share is always at least a hash archive plus one payload part. The ceiling
# is a sanity bound: at 90MB per chunk, 128 parts is over 11GB.
MIN_LINKS = 2
MAX_LINKS = 128
_KIND_RE = re.compile(r"shared their\s+(\w+)\s+with you", re.IGNORECASE)
_KIND_FALLBACK_RE = re.compile(r"Size of\s+(\w+)\s*:", re.IGNORECASE)
_NAME_RE = re.compile(r'Internal name:\s*"(.*?)"', re.IGNORECASE)
# NOTE: \w+ rather than a hardcoded "modlist" -- the old regex never matched a
# savefolder share, so size_bytes was always None and the download ETA never
# had a total to work from.
_SIZE_RE = re.compile(r"Size of\s+\w+\s*:\s*([0-9]+)\s*bytes", re.IGNORECASE)
_DATE_RE = re.compile(r"Date of\s+\w+\s*:\s*([0-9:\- \w]+)", re.IGNORECASE)


class ManifestError(ValueError):
    """The pasted text isn't a share block we can use."""


# -------------------------
# region RENDER
# -------------------------
def render(links: list[str], *, build_id: int, internal_name: str,
           size_bytes: int, install_type: str,
           now: datetime | None = None) -> str:
    """Build the block the user copies to a friend.

    ``links[0]`` must be the MD5 hash archive; the loader relies on that
    ordering to know which downloaded part is the payload.
    """
    if not links:
        raise ManifestError("Cannot render a share with no links.")

    now = now or datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    discord_relative = f"<t:{int(now.timestamp())}:R>"

    payload = base64.b64encode("\n".join(links).encode()).decode().strip()

    return f"""### Gnizer (v{build_id}) `{internal_name}`

A friend has shared their {install_type} with you {discord_relative}!
{FENCE}
Internal name: \"{internal_name}\"
Size of {install_type}: {size_bytes} bytes
Date of {install_type}: {timestamp}*

**Instructions**
- CTRL+C this!
- Open Gnizer
- Select: 'Load *DATA* from an ARCHIVE'
- Select: 'Read from clipboard'
- [DON'T EVER: CTRL+V]
- Press Enter

{DATA_MARKER}
{payload}
{FENCE}"""


def suggested_filename(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"Gnizer_shared_modlist_{now.strftime('%Y%m%d%H%M%S')}.md"


# -------------------------
# region PARSE
# -------------------------
def looks_like_share(text: str) -> bool:
    """Cheap check for the clipboard getter, before we try to parse properly."""
    if not text:
        return False
    return "gnizer" in text.lower() and DATA_MARKER in text


def parse(raw_text: str) -> ShareManifest:
    """Turn a pasted block into a ShareManifest, or raise ManifestError."""
    if not raw_text or not raw_text.strip():
        raise ManifestError("Manifest text is empty.")

    text = raw_text.strip()

    if "gnizer" not in text.lower():
        raise ManifestError(
            "That doesn't look like a Gnizer share. Did you copy the whole block?"
        )

    install_type = _parse_install_type(text)
    links = _parse_links(text)

    if not links:
        raise ManifestError(
            "Found a Gnizer header but no tmpfiles.org links were detected."
        )

    size_match = _SIZE_RE.search(text)
    name_match = _NAME_RE.search(text)
    date_match = _DATE_RE.search(text)

    return ShareManifest(
        internal_name=name_match.group(1).strip() if name_match else None,
        size_bytes=int(size_match.group(1)) if size_match else None,
        timestamp=date_match.group(1).strip() if date_match else None,
        links=links,
        install_type=install_type,
    )


def _parse_install_type(text: str) -> str:
    match = _KIND_RE.search(text) or _KIND_FALLBACK_RE.search(text)
    kind = match.group(1).lower() if match else ""

    if kind in (INSTALL_MODLIST, INSTALL_SAVEFOLDER):
        return kind

    raise ManifestError(
        "Manifest doesn't say whether it's a modlist or a savefolder. "
        "Did you highlight and copy everything?"
    )


def _parse_links(text: str) -> list[str]:
    """Read the base64 payload that follows the **Data** marker."""
    lines = text.splitlines()

    try:
        start = next(
            i for i, line in enumerate(lines) if line.strip() == DATA_MARKER
        ) + 1
    except StopIteration:
        raise ManifestError(
            f"No '{DATA_MARKER}' section found -- the copied block is incomplete."
        ) from None

    payload_lines: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith(FENCE):
            break
        if stripped:
            payload_lines.append(stripped)

    if not payload_lines:
        raise ManifestError("The **Data** section is empty.")

    encoded = "".join(payload_lines)
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except Exception as exc:
        raise ManifestError(f"The share data is corrupted: {exc}") from exc

    return _validate_links(decoded)


def _validate_links(decoded: str) -> list[str]:
    """Require the decoded payload to be nothing but valid tmpfiles.org links.

    The base64 is an encoding, not a protection -- anyone can decode it, edit
    it and re-encode it. This does not make the block tamper-proof, and it is
    not meant to: a sender who is hostile to begin with controls every part of
    the share, and the content scan in py_secure is what stands between that
    and an install.

    What it does stop is the failure mode where tampering or truncation went
    through *quietly*. The parser used to run a findall for tmpfiles.org URLs
    over the decoded text, so anything that wasn't one -- an injected foreign
    host, a line mangled by Discord, a half-copied block -- was silently
    dropped, leaving fewer parts than the share actually had. Reassembly then
    produced a truncated archive with no warning at all.
    """
    lines = [line.strip() for line in decoded.splitlines()]
    lines = [line for line in lines if line]

    if not lines:
        raise ManifestError("The share data decoded to nothing.")

    rejected = [line for line in lines if not _STRICT_LINK_RE.match(line)]
    if rejected:
        preview = rejected[0]
        if len(preview) > 70:
            preview = preview[:67] + "..."
        raise ManifestError(
            "This share has been modified, or was not copied in full.\n"
            f"         {len(rejected)} of {len(lines)} entries are not valid "
            "Gnizer download links.\n"
            f"         First bad entry: {preview}\n"
            "         Ask your friend to re-send the whole block."
        )

    if len(lines) < MIN_LINKS:
        raise ManifestError(
            "This share is incomplete -- it has no payload parts, only a hash "
            "archive.\n         Ask your friend to rebundle."
        )

    if len(lines) > MAX_LINKS:
        raise ManifestError(
            f"This share claims {len(lines)} parts, which is beyond anything "
            f"Gnizer produces (max {MAX_LINKS})."
        )

    return lines


# -------------------------
# region ROUND-TRIP CHECK
# -------------------------
if __name__ == "__main__":
    sample_links = [
        "https://tmpfiles.org/abc123/pack_MD5.rar.zip",
        "https://tmpfiles.org/def456/pack.rar0.zip",
        "https://tmpfiles.org/ghi789/pack.rar1.zip",
    ]

    for kind in (INSTALL_MODLIST, INSTALL_SAVEFOLDER):
        block = render(
            sample_links,
            build_id=999,
            internal_name="pack.rar",
            size_bytes=123456,
            install_type=kind,
        )
        parsed = parse(block)

        assert parsed.links == sample_links, parsed.links
        assert parsed.install_type == kind, parsed.install_type
        assert parsed.internal_name == "pack.rar", parsed.internal_name
        assert parsed.size_bytes == 123456, parsed.size_bytes
        assert parsed.payload_links == sample_links[1:]
        assert looks_like_share(block)

    print("manifest round-trip OK for modlist and savefolder")

    # --- tampering must be refused loudly, not absorbed silently ---------
    def rebuild(links: list[str]) -> str:
        payload = base64.b64encode("\n".join(links).encode()).decode()
        head = block.rpartition(DATA_MARKER)[0]
        return f"{head}{DATA_MARKER}\n{payload}\n{FENCE}"

    tampered = {
        "foreign host injected": [
            sample_links[0], "https://evil.example.com/payload.exe", sample_links[2]
        ],
        "truncated link": [sample_links[0], "https://tmpfiles.org/def456/pack.ra"],
        "prose injected": [sample_links[0], "download this instead", sample_links[1]],
        "hash archive only": [sample_links[0]],
    }

    for label, links in tampered.items():
        try:
            parse(rebuild(links))
        except ManifestError:
            print(f"  refused: {label}")
        else:
            raise AssertionError(f"tampering NOT refused: {label}")

    print("tamper checks OK")
