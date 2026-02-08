from py_imports import *
import hashlib
import shutil
from typing import Callable
from colorama import Fore

def _md5_of_file(p: Path, chunk_size: int = 8192) -> str:
    h = hashlib.md5()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()

def review_and_install(
    extracted_path: Path,
    chosen_mod_manager: dict,
    chosen_mod_profile: dict,
    get_consent,
    set_operation_text,
) -> bool:

    # Resolve profile mods directory
    try:
        profile_folder_name = chosen_mod_profile["folder"]
        profile_root = chosen_mod_manager["profiles_path"] / profile_folder_name
        profile_mods_dir = profile_root / "mods"
    except Exception:
        set_operation_text(Fore.RED + "Unable to resolve profile mods directory.")
        return True

    if not profile_mods_dir.exists():
        set_operation_text(Fore.RED + f"Mods folder not found: {profile_mods_dir}")
        return True

    extracted_files = [p for p in Path(extracted_path).rglob("*") if p.is_file()]
    profile_files = [p for p in profile_mods_dir.iterdir() if p.is_file()]

    # Build name maps
    def build_map(files):
        m = {}
        for p in files:
            m.setdefault(p.name, []).append(p)
        return m

    extracted_map = build_map(extracted_files)
    profile_map = build_map(profile_files)

    identical, differing, only_in_extracted, only_in_profile = [], [], [], []

    # Compare MD5s
    for name, ex_list in extracted_map.items():
        prof_list = profile_map.get(name)
        if not prof_list:
            only_in_extracted.append(name)
            continue

        matched = False
        for ex in ex_list:
            ex_md5 = _md5_of_file(ex)
            for pf in prof_list:
                if ex_md5 == _md5_of_file(pf):
                    identical.append(name)
                    matched = True
                    break
            if matched:
                break
        if not matched:
            differing.append(name)

    for name in profile_map.keys():
        if name not in extracted_map:
            only_in_profile.append(name)

    # Print report

    def print_section(title_color, title, items):
        print(title_color + f"{title} ({len(items)}):")
        for item in items:
            print(Fore.LIGHTBLACK_EX + f"  - {item}")
        print()  # blank line after each section

    if identical:
        print_section(Fore.GREEN, "Identical", identical)

    if differing:
        print_section(Fore.YELLOW, "Differing", differing)

    if only_in_extracted:
        print_section(Fore.CYAN, "New (in archive only)", only_in_extracted)

    if only_in_profile:
        print_section(Fore.MAGENTA, "Removed (in profile only)", only_in_profile)

    # Determine if ANY mismatch exists
    mismatches = len(differing) + len(only_in_extracted) + len(only_in_profile)

    if mismatches == 0:
        print(Fore.GREEN + "\nNo differences detected. Nothing to install.")
        set_operation_text("No changes detected.")
        return True

    print(Fore.YELLOW + f"\nDetected {mismatches} mismatched or new files.")

    # First confirmation
    if not get_consent(Fore.YELLOW + "Proceed with installation (this will replace your mods)"):
        set_operation_text("Installation cancelled.")
        return True

    # Second confirmation (opposite wording)
    print("\n" + Fore.RED + "WARNING: This will DELETE ALL existing mods in this profile.")
    if not get_consent(Fore.RED + "Are you absolutely sure you want to continue"):
        set_operation_text("Installation cancelled at final confirmation.")
        return True

    # Backup + wipe + install
    short_ts = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_root = Path(shutil.os.environ.get("TEMP", Path.home() / "AppData/Local/Temp")) / "ModGnizer" / f"backup_{short_ts}"
    backup_root.mkdir(parents=True, exist_ok=True)

    try:
        # Backup existing mods
        for f in profile_files:
            shutil.copy2(f, backup_root / f.name)

        # Wipe mods folder
        for f in profile_files:
            f.unlink()

        # Install all extracted files
        for src in extracted_files:
            dest = profile_mods_dir / src.name
            shutil.copy2(src, dest)

        set_operation_text(f"Installed fresh modlist. Backup saved to: {backup_root}")
        print(Fore.GREEN + f"\nInstallation complete. Backup saved to: {backup_root}")
        return True

    except Exception as e:
        set_operation_text(Fore.RED + f"Installation failed: {e}")
        return True
