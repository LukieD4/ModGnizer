"""The Gnizer share block: rendered and parsed in one file.

Previously the template lived in ``py_main.save_links_md_and_copy_to_clipboard``
and the parser lived in ``py_tmpfiles.parse_gnizer_manifest``, coupled by line
number -- ``text.splitlines()[17]`` for the payload and ``[2]`` for the share
type. Adding a single line to the template would have silently broken every
share, in a different file, with no error until a friend pasted one in.

Parsing is now structural: it looks for the ``**Data**`` marker rather than
counting lines, so blocks from older builds still parse even though the
template has since gained an expiry line -- exactly the change that would
have broken the old line-indexed parser. Run this module directly to check
the round-trip and the tamper refusals.

Validating the links is structural for the same reason. Checking each line
in isolation -- "is this a tmpfiles.org .zip URL" -- can only ever say that a
link is *a* Gnizer-shaped upload, never that it is one of *this* share's, so
appending an extra line to a block passed cleanly. What makes a set of links
one share is now checked as a set: see the LINK SET region, which is careful
to check only the parts of a link tmpfiles.org hands back unchanged.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime
from urllib.parse import unquote, urlparse

from py_models import (
    INSTALL_MODLIST,
    INSTALL_SAVEFOLDER,
    UPLOAD_CHUNK_SIZE,
    ShareManifest,
)

DATA_MARKER = "**Data**"
FENCE = "```"

# Every decoded line must match this exactly -- see _validate_links.
#
# The trailing .zip is not a guess: TmpFilesClient._masked_name forces a .zip
# suffix on every upload because tmpfiles.org rejects other extensions, so a
# link that doesn't end in .zip did not come out of Gnizer. The 1-3 path
# segments cover both the share form (/<token>/<file>) and the direct form
# (/dl/<id>/<token>/<file>).
#
# The filename itself is deliberately anything-but-a-slash. It is the archive
# name the user chose, after tmpfiles.org has had its way with it, and real
# instance names are full of spaces and brackets ("Better MC [FORGE] BMC4").
# Guessing which of those survive encoding is how this check would start
# rejecting perfectly good shares; what the name has to *end* with is pinned
# properly in _validate_link_set.
_STRICT_LINK_RE = re.compile(
    r"^https?://(?:www\.)?tmpfiles\.org/(?:[A-Za-z0-9._-]+/){1,3}"
    r"[^/]+\.zip$",
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

# The internal name is not just a label: TmpFilesClient._reassemble writes the
# joined payload to ``parts[0].parent / internal_name``, so a name carrying
# separators, a drive letter or ".." would let a pasted block choose where on
# disk the download lands. It must be a bare "<something>.<ext>" filename.
_INTERNAL_NAME_RE = re.compile(r'^[^\\/:*?"<>|\x00-\x1f]+\.[A-Za-z0-9]{1,8}$')
_MAX_NAME_LENGTH = 200


class ManifestError(ValueError):
    """The pasted text isn't a share block we can use."""


# -------------------------
# region RENDER
# -------------------------
def render(links: list[str], *, build_id: int, internal_name: str,
           size_bytes: int, install_type: str, expiry_seconds: int,
           now: datetime | None = None) -> str:
    """Build the block the user copies to a friend.

    ``links[0]`` must be the MD5 hash archive; the loader relies on that
    ordering to know which downloaded part is the payload.

    Both timestamps use Discord's ``<t:epoch:R>`` form so they render as
    "an hour ago" / "in an hour" in chat. They sit *outside* the code fence
    deliberately -- Discord does not expand timestamps inside one.
    """
    if not links:
        raise ManifestError("Cannot render a share with no links.")

    now = now or datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    discord_relative = f"<t:{int(now.timestamp())}:R>"
    expires_relative = f"<t:{int(now.timestamp()) + expiry_seconds}:R>"

    payload = base64.b64encode("\n".join(links).encode()).decode().strip()

    return f"""### Gnizer (v{build_id}) `{internal_name}`

A friend has shared their {install_type} with you {discord_relative}!
These links expire {expires_relative}, so grab them before then.
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

    # Read the header before the links: what the links are allowed to be is
    # derived from it. Both fields are required now -- render() has always
    # written them, and without them there is nothing to check the link set
    # against, which is the state that let an extra link through.
    internal_name = _parse_internal_name(text)
    size_bytes = _parse_size(text)

    links = _parse_links(text)

    if not links:
        raise ManifestError(
            "Found a Gnizer header but no tmpfiles.org links were detected."
        )

    _validate_link_set(links, internal_name=internal_name, size_bytes=size_bytes)

    date_match = _DATE_RE.search(text)

    return ShareManifest(
        internal_name=internal_name,
        size_bytes=size_bytes,
        timestamp=date_match.group(1).strip() if date_match else None,
        links=links,
        install_type=install_type,
    )


def _incomplete(label: str) -> ManifestError:
    return ManifestError(
        f"This share is missing its '{label}' line, so its links cannot be "
        "checked\n         against what it claims to contain.\n"
        "         Ask your friend to re-send the whole block."
    )


def _parse_internal_name(text: str) -> str:
    match = _NAME_RE.search(text)
    if not match:
        raise _incomplete("Internal name")

    name = match.group(1).strip()

    if (not name or len(name) > _MAX_NAME_LENGTH
            or ".." in name or not _INTERNAL_NAME_RE.match(name)):
        raise ManifestError(
            f"This share's internal name is not a plain filename: {name!r}\n"
            "         Ask your friend to re-send the whole block."
        )

    return name


def _parse_size(text: str) -> int:
    match = _SIZE_RE.search(text)
    if not match:
        raise _incomplete("Size of")

    return int(match.group(1))


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

    This is only the per-line gate -- it says each entry is a Gnizer-shaped
    upload URL, which is just as true of an attacker's own upload as of ours.
    Whether the entries are *this share's* is decided by _validate_link_set.

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
            "This share has been tampered, or was not copied in full.\n"
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
# region LINK SET
# -------------------------
# The filename in a link is not the filename we uploaded. Gnizer sends
# "BETTER MC_MD5.rar.zip" and tmpfiles.org hands back a link naming
# "better mc_md5.rar.zip" -- lowercased, with the space encoded, substituted
# or dropped depending on the server's mood. So deriving the expected filename
# from "Internal name" and comparing the two checks our guess about tmpfiles'
# rewriting rules, not the share, and every instance name that isn't already
# URL-safe -- "BETTER MC", "All the Mods 9", "Better MC [FORGE]" -- is a coin
# toss. Nothing below compares a server-side name to a client-side one.
#
# What it checks is the part of each filename Gnizer *appends*, which is plain
# ASCII and comes back untouched:
#
#     <prefix>_MD5.rar.zip     the hash listing        (py_actions)
#     <prefix>.rar0.zip        payload part 0          (_split_into_parts)
#     <prefix>.rar1.zip        payload part 1, ...
#
# Those suffixes are fixed by the archive format and the part count, which the
# header gives us as an extension and a number -- neither of them rewritable.
# And whatever tmpfiles made of <prefix>, it made the same thing of it every
# time, because the same name went up each time. So the links have to agree
# with each other on it.
#
# Between them that pins the order, the count, and that every part came from
# one archive, without assuming anything about the rewriting. Appending
#
#     https://tmpfiles.org/<id>/pack.rar4.zip
#
# to a genuine four-part share used to validate cleanly, and download_manifest
# concatenates parts in the order listed -- so the extra part was appended to
# the reassembled archive. For a .zip payload that is not harmless padding: a
# ZIP reader locates the central directory by scanning back from the end of
# the file, so a complete archive appended to another one is the one that gets
# extracted.
#
# The upload-id check is the other half. tmpfiles.org serves a file by its id,
# and the filename segment of the URL is decorative -- so the extra link can
# name itself "pack.rar4.zip" while pointing at part 3's id, which satisfies
# every name check above. Each part of a real share is its own upload, so an
# id appearing twice means a line was fabricated from another one.


def _mask(name: str) -> str:
    """Mirror of ``TmpFilesClient._masked_name``: everything ships as .zip."""
    return name if name.lower().endswith(".zip") else f"{name}.zip"


def _expected_suffixes(internal_name: str, size_bytes: int) -> list[str]:
    """What each part's filename must end with, in the order they must appear.

    Only the archive extension and the part index go into these -- both plain
    ASCII, both added by Gnizer after the name the user chose, so both survive
    whatever tmpfiles.org does to the rest.
    """
    stem, dot, fmt = internal_name.rpartition(".")
    if not dot or not stem:
        raise ManifestError(
            f"This share's internal name has no file extension: {internal_name!r}"
        )

    fmt = fmt.lower()
    suffixes = [_mask(f"_MD5.{fmt}").lower()]

    if size_bytes <= UPLOAD_CHUNK_SIZE:
        # Small enough that upload_in_chunks sent it whole, unnumbered.
        suffixes.append(_mask(f".{fmt}").lower())
    else:
        chunks = -(-size_bytes // UPLOAD_CHUNK_SIZE)  # ceil, no float rounding
        suffixes.extend(_mask(f".{fmt}{index}").lower() for index in range(chunks))

    return suffixes


def _split_upload_path(link: str) -> tuple[str, str]:
    """Return ``(upload id, filename)`` for a tmpfiles.org link.

    Handles both the share form (/<id>/<file>) and the direct form
    (/dl/<id>/<file>), so the same link written either way compares equal.
    """
    segments = [
        segment for segment in unquote(urlparse(link).path).split("/") if segment
    ]

    if segments and segments[0].lower() == "dl":
        segments = segments[1:]

    if len(segments) < 2:
        raise ManifestError(
            f"This share contains a link with no file on it: {link}"
        )

    return "/".join(segments[:-1]), segments[-1]


def _tampered(detail: str) -> ManifestError:
    return ManifestError(
        f"This share has been tampered since it was created.\n         {detail}\n"
        "         Ask your friend to re-send the whole block."
    )


def _validate_link_set(links: list[str], *, internal_name: str,
                       size_bytes: int) -> None:
    """Check the links are one consistent set, in the order Gnizer uploads."""
    expected = _expected_suffixes(internal_name, size_bytes)

    seen_ids: dict[str, int] = {}
    filenames: list[str] = []

    for position, link in enumerate(links):
        upload_id, filename = _split_upload_path(link)
        key = upload_id.lower()

        if key in seen_ids:
            raise _tampered(
                f"Parts {seen_ids[key] + 1} and {position + 1} are the same "
                f"upload (/{upload_id}/) under two different names.\n"
                "         Every part of a real share is a separate upload."
            )

        seen_ids[key] = position
        filenames.append(filename)

    if len(filenames) != len(expected):
        raise _tampered(
            f"It lists {len(filenames)} part(s), but '{internal_name}' at "
            f"{size_bytes:,} bytes is always sent as {len(expected)}.\n"
            "         A part has been added or removed."
        )

    # Where each distinct prefix first appeared, so a disagreement can name the
    # two parts that disagree rather than just reporting that one exists.
    prefixes: dict[str, int] = {}

    for position, (filename, suffix) in enumerate(zip(filenames, expected)):
        lowered = filename.lower()

        if not lowered.endswith(suffix):
            raise _tampered(
                f"Part {position + 1} is not the file it should be.\n"
                f"         Expected a name ending '{suffix}'\n"
                f"         Found:    {filename}"
            )

        prefixes.setdefault(lowered[: -len(suffix)], position)

    if len(prefixes) > 1:
        first, second = sorted(prefixes.values())[:2]
        raise _tampered(
            f"Parts {first + 1} and {second + 1} are named after different "
            "archives.\n         Every part of a share is named after the same one."
        )


# -------------------------
# region ROUND-TRIP CHECK
# -------------------------
if __name__ == "__main__":
    CHUNKED_SIZE = UPLOAD_CHUNK_SIZE + 1  # one byte over a chunk -> two parts
    WHOLE_SIZE = 123_456                  # under one chunk -> sent unnumbered

    chunked_links = [
        "https://tmpfiles.org/abc123/pack_MD5.rar.zip",
        "https://tmpfiles.org/def456/pack.rar0.zip",
        "https://tmpfiles.org/ghi789/pack.rar1.zip",
    ]
    whole_links = [
        "https://tmpfiles.org/abc123/pack_MD5.rar.zip",
        "https://tmpfiles.org/def456/pack.rar.zip",
    ]

    def build(links: list[str], size: int, kind: str = INSTALL_MODLIST) -> str:
        return render(
            links,
            build_id=999,
            internal_name="pack.rar",
            size_bytes=size,
            install_type=kind,
            expiry_seconds=3600,
        )

    for kind in (INSTALL_MODLIST, INSTALL_SAVEFOLDER):
        for links, size in ((chunked_links, CHUNKED_SIZE), (whole_links, WHOLE_SIZE)):
            block = build(links, size, kind)
            parsed = parse(block)

            assert parsed.links == links, parsed.links
            assert parsed.install_type == kind, parsed.install_type
            assert parsed.internal_name == "pack.rar", parsed.internal_name
            assert parsed.size_bytes == size, parsed.size_bytes
            assert parsed.payload_links == links[1:]
            assert looks_like_share(block)

    print("manifest round-trip OK for modlist and savefolder")

    # Real instance names are not URL-safe, and tmpfiles.org rewrites what it
    # is given -- the link for "BETTER MC_MD5.rar.zip" comes back naming
    # "better mc_md5.rar.zip". Which rewriting it picks is its business; every
    # one of these has to load, because the alternative is a tool that refuses
    # its own shares for anyone whose modpack has a space in the name.
    rewritings = {
        "left alone": ["BETTER MC_MD5.rar.zip", "BETTER MC.rar0.zip",
                       "BETTER MC.rar1.zip"],
        "lowercased": ["better mc_md5.rar.zip", "better mc.rar0.zip",
                       "better mc.rar1.zip"],
        "percent-encoded": ["BETTER%20MC_MD5.rar.zip", "BETTER%20MC.rar0.zip",
                            "BETTER%20MC.rar1.zip"],
        "spaces substituted": ["better_mc_md5.rar.zip", "better_mc.rar0.zip",
                               "better_mc.rar1.zip"],
        "spaces dropped": ["bettermc_md5.rar.zip", "bettermc.rar0.zip",
                           "bettermc.rar1.zip"],
        "punctuation stripped": ["better-mc-forge-bmc4_md5.rar.zip",
                                 "better-mc-forge-bmc4.rar0.zip",
                                 "better-mc-forge-bmc4.rar1.zip"],
        "name stripped entirely": ["_md5.rar.zip", ".rar0.zip", ".rar1.zip"],
    }

    ids = ["abc123", "def456", "ghi789"]

    for label, names in rewritings.items():
        block = render(
            [f"https://tmpfiles.org/{id_}/{name}" for id_, name in zip(ids, names)],
            build_id=999,
            internal_name="BETTER MC [FORGE] BMC4.rar",
            size_bytes=CHUNKED_SIZE,
            install_type=INSTALL_MODLIST,
            expiry_seconds=3600,
        )

        assert len(parse(block).links) == 3, label
        print(f"  accepted: filenames {label}")

    print("server-rewritten filenames still accepted")

    # ... but the links still have to agree with each other about what they
    # are named after, which is what catches parts spliced in from elsewhere.
    spliced = render(
        [
            "https://tmpfiles.org/abc123/better mc_md5.rar.zip",
            "https://tmpfiles.org/def456/better mc.rar0.zip",
            "https://tmpfiles.org/ghi789/some other pack.rar1.zip",
        ],
        build_id=999,
        internal_name="BETTER MC.rar",
        size_bytes=CHUNKED_SIZE,
        install_type=INSTALL_MODLIST,
        expiry_seconds=3600,
    )

    try:
        parse(spliced)
    except ManifestError:
        print("  refused: a part named after a different archive")
    else:
        raise AssertionError("splice from another share NOT refused")

    # --- tampering must be refused loudly, not absorbed silently ---------
    genuine = build(chunked_links, CHUNKED_SIZE)

    def rebuild(links: list[str]) -> str:
        """A genuine header with an attacker-chosen link list underneath."""
        payload = base64.b64encode("\n".join(links).encode()).decode()
        head = genuine.rpartition(DATA_MARKER)[0]
        return f"{head}{DATA_MARKER}\n{payload}\n{FENCE}"

    tampered = {
        # The reported case: one extra line, pointed at a part that is already
        # in the share, named as if it were the next one along.
        "extra part appended, reusing an upload id":
            chunked_links + ["https://tmpfiles.org/ghi789/pack.rar2.zip"],
        "extra part appended, freshly uploaded":
            chunked_links + ["https://tmpfiles.org/jkl012/pack.rar2.zip"],
        "payload part swapped for a foreign upload":
            chunked_links[:2] + ["https://tmpfiles.org/jkl012/notpack.rar1.zip"],
        "hash archive swapped":
            ["https://tmpfiles.org/jkl012/other_MD5.rar.zip"] + chunked_links[1:],
        "parts reordered":
            [chunked_links[0], chunked_links[2], chunked_links[1]],
        "part removed": chunked_links[:2],
        "foreign host injected":
            [chunked_links[0], "https://evil.example.com/payload.exe",
             chunked_links[2]],
        "truncated link":
            [chunked_links[0], "https://tmpfiles.org/def456/pack.ra"],
        "prose injected":
            [chunked_links[0], "download this instead", chunked_links[1]],
        "hash archive only": [chunked_links[0]],
    }

    for label, links in tampered.items():
        try:
            parse(rebuild(links))
        except ManifestError:
            print(f"  refused: {label}")
        else:
            raise AssertionError(f"tampering NOT refused: {label}")

    # Header edits: the link set is only checkable against a header that is
    # present and sane, so a block missing either field is refused rather than
    # parsed with the check quietly skipped.
    header_tampered = {
        "internal name line removed":
            genuine.replace('Internal name: "pack.rar"\n', ""),
        "size line removed":
            re.sub(r"Size of .*\n", "", genuine),
        "internal name escapes the download folder":
            genuine.replace('"pack.rar"', r'"..\..\Startup\evil.rar"'),
    }

    for label, block in header_tampered.items():
        try:
            parse(block)
        except ManifestError:
            print(f"  refused: {label}")
        else:
            raise AssertionError(f"tampering NOT refused: {label}")

    print("tamper checks OK")

    # Not checkable here, on purpose: an attacker who appends a part AND
    # inflates 'Size of' to match produces a header-consistent link set. What
    # gives it away is the bytes -- the part that used to be last is a short
    # tail, and py_secure.check_payload_sizes requires every part but the last
    # to be exactly one full chunk.
