import os

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
