import os
from dataclasses import dataclass

SAVEFOLDER_EXT_WHITELIST = [
    ".json", ".png", ".dat", ".dat_old", ".lock", ".mca", ".toml", ".txt", ".nbt"
]

MOD_EXT_WHITELIST = [
    ".jar", ".input"
]

JAVA_MAGIC = "504B030414"   # ZIP/JAR magic header
EXE_MAGIC  = "4D5A"         # Windows EXE header


def _get_extension(path):
    return os.path.splitext(path)[1].lower()


# -------------------------
# region UPLOAD ORDER
# -------------------------
# tmpfiles.org shows "File expires in N minutes" on every share page, and
# expiry is always upload_time + EXPIRY_SECONDS. That makes remaining time a
# proxy for upload time: a file uploaded earlier has less of it left.
#
# Gnizer always uploads in a fixed order -- the MD5 archive, then payload part
# 0, part 1, and so on -- so a genuine share's parts must get progressively
# YOUNGER down the list. If part 2 turns out to be older than part 1, someone
# put it there after the fact.
#
# Compare absolute expiry instants (fetch_time + remaining), never the raw
# minute counts. We read each page sequentially, so a later part is read later
# in wall-clock terms; comparing raw counts makes a perfectly ordinary share
# look out of order purely because of the delay between reads.
#
# The page rounds to whole minutes, so two parts uploaded seconds apart can
# read the same. The tolerance absorbs that quantisation. It also sets the
# floor on what this can detect: a part slipped in under two minutes out of
# order will pass.
UPLOAD_ORDER_TOLERANCE_SECONDS = 120


@dataclass(frozen=True)
class UploadOrderVerdict:
    ok: bool
    compared: int
    unknown: int
    problems: list

    def summary(self):
        if self.problems:
            return "\n".join(self.problems)
        if self.unknown:
            return (f"Upload order could not be checked for {self.unknown} "
                    "part(s) -- tmpfiles.org did not report an expiry time.")
        return f"Upload order looks correct across {self.compared + 1} parts."


def check_upload_order(parts):
    """Verify parts were uploaded in the order the share lists them.

    ``parts`` is a list of ``(label, expiry_instant)``, in share order, where
    ``expiry_instant`` is a monotonic timestamp or None when unknown.

    Catches a payload part that was uploaded BEFORE the ones listed above it
    -- the signature of a link swapped in from a file staged ahead of time. It
    does not catch a part uploaded fresh and appended last, and it cannot say
    anything at all about a share where every part came from the same hostile
    sender.
    """
    problems = []
    compared = 0
    unknown = sum(1 for _, instant in parts if instant is None)

    previous = None
    for label, instant in parts:
        if instant is None:
            continue

        if previous is not None:
            prev_label, prev_instant = previous
            drift = prev_instant - instant
            if drift > UPLOAD_ORDER_TOLERANCE_SECONDS:
                problems.append(
                    f"'{label}' was uploaded about {int(drift // 60)} minute(s) "
                    f"BEFORE '{prev_label}', but the share lists it afterwards."
                )
            compared += 1

        previous = (label, instant)

    return UploadOrderVerdict(
        ok=not problems, compared=compared, unknown=unknown, problems=problems
    )


def _scan_extracted_folder(root_path, whitelist):
    """
    Scans folder and returns:
    {
        "unexpected": [...],
        "java_magic": [...],   # non-jar files with JAR magic
        "exe_magic": [...],    # files with EXE header
        "all": [...]
    }
    """
    results = {
        "unexpected": [],
        "java_magic": [],
        "exe_magic": [],
        "all": []
    }

    for dirpath, _, filenames in os.walk(root_path):
        for filename in filenames:
            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, root_path)
            results["all"].append(rel_path)

            ext = _get_extension(filename)

            # Whitelist check
            if ext not in whitelist:
                results["unexpected"].append(rel_path)

            # Magic byte checks
            try:
                with open(full_path, "rb") as f:
                    magic = f.read(5).hex().upper()

                    # EXE header (MZ)
                    if magic.startswith(EXE_MAGIC):
                        results["exe_magic"].append(rel_path)

                    # JAR/ZIP magic — but only suspicious if NOT a .jar
                    if magic.startswith(JAVA_MAGIC) and ext != ".jar":
                        results["java_magic"].append(rel_path)

            except OSError:
                results["unexpected"].append(rel_path)

    return results



def auto_scan(extracted_path):
    has_level_dat = False
    has_icon_png = False
    has_region_dir = False
    has_jar = False

    for dirpath, dirnames, filenames in os.walk(extracted_path):
        if any(d.lower() == "region" for d in dirnames):
            has_region_dir = True

        for filename in filenames:
            lower = filename.lower()

            if lower == "level.dat":
                has_level_dat = True
            if lower == "icon.png":
                has_icon_png = True
            if _get_extension(lower) == ".jar":
                has_jar = True

    if has_level_dat and (has_region_dir or has_icon_png):
        return "savefolder"
    if has_jar:
        return "modlist"
    return "unknown"



def scan_extraction(extracted_path, type_of_install):
    TARGET_WHITELIST = (
        SAVEFOLDER_EXT_WHITELIST
        if type_of_install == "savefolder"
        else MOD_EXT_WHITELIST
    )

    results = _scan_extracted_folder(extracted_path, TARGET_WHITELIST)

    conclusions = []

    # Unexpected extensions
    for path in results["unexpected"]:
        ext = _get_extension(path)
        conclusions.append(f"Unexpected file type '{ext}' found: {path}")

    # Non-jar files containing JAR magic
    for path in results["java_magic"]:
        ext = _get_extension(path)
        conclusions.append(
            f"[~] Suspicious: file '{path}' has JAR magic header but is '{ext}'"
        )

    # Files containing EXE header
    for path in results["exe_magic"]:
        ext = _get_extension(path)
        conclusions.append(
            f"[!] Danger: file '{path}' contains EXE header (MZ) but is '{ext}'"
        )

    # If no issues, return success
    if not conclusions:
        return True, "All files passed validation.", results

    # Otherwise return failure with combined message
    final_message = "\n".join(conclusions)
    return False, final_message, results
