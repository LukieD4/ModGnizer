"""What each menu entry actually does.

One function per menu item, each reading top to bottom and returning a Result.
Cancellation propagates as an exception to the main loop, which is why there
are no ``if not x: return True`` ladders left.
"""

from __future__ import annotations

from pathlib import Path

import py_archive
import py_getters
import py_manifest
import py_paths
import py_secure
import py_ui
from py_imports import Fore
from py_models import (
    INSTALL_MODLIST,
    INSTALL_SAVEFOLDER,
    ArchivePrefs,
    Context,
    Result,
)

UPLOAD_CHUNK_SIZE = 90 * 1024 * 1024


# -------------------------
# region BUNDLE
# -------------------------
def bundle_mods(ctx: Context) -> Result:
    """Archive a profile's mods folder and offer to share it."""
    manager = py_getters.get_mod_manager()
    profile = py_getters.get_mod_profile(manager)

    print(f"\n{Fore.WHITE}Selected Profile: {profile.name}")
    print(f"{Fore.WHITE}Mods Directory: {profile.mods_dir}")

    if not profile.mods_dir.exists():
        return Result.error(f"This profile has no mods folder: {profile.mods_dir}")

    prefs = py_getters.get_archive_preferences()

    return _bundle_and_share(
        ctx,
        source_dir=profile.mods_dir,
        base_name=profile.folder,
        install_type=INSTALL_MODLIST,
        output_dir=py_paths.bundled_dir(),
        prefs=prefs,
        empty_message=f"Mod Profile '{profile.name}' contains no mods to bundle.",
    )


def bundle_world(ctx: Context) -> Result:
    """Archive a save folder and offer to share it.

    This whole path was dead: it constructed ``ArchiveBundler(world_path)``
    against a no-arg ``__init__``, then called ``bundle_zip``/``bundle_7z``/
    ``bundle_rar`` -- all three of which were commented out -- and finally
    passed the upload helper two positional arguments where it wanted three.
    """
    manager = py_getters.get_mod_manager()
    profile = py_getters.get_mod_profile(manager)

    print(f"\n{Fore.WHITE}Selected Profile: {profile.name}")

    world = py_getters.get_world(profile.saves_dir, allow_new=False)
    print(f"{Fore.WHITE}World Directory: {world.path}")

    prefs = py_getters.get_archive_preferences()

    return _bundle_and_share(
        ctx,
        source_dir=world.path,
        base_name=world.name,
        install_type=INSTALL_SAVEFOLDER,
        output_dir=py_paths.world_bundled_dir(),
        prefs=prefs,
        empty_message=f"World '{world.name}' contains no data to bundle.",
    )


def _bundle_and_share(ctx: Context, *, source_dir: Path, base_name: str,
                      install_type: str, output_dir: Path,
                      prefs: ArchivePrefs, empty_message: str) -> Result:
    """Bundle a folder, bundle its MD5 listing alongside, then offer to upload.

    Both archives land in ``output_dir``. The MD5 listing is written into a
    separate staging folder first: the old code put the hash archive *inside*
    the directory it was about to compress, so the output was an input to
    itself.
    """
    py_paths.ensure(output_dir)

    main_archive = output_dir / f"{base_name}.{prefs.format}"
    hash_archive = output_dir / f"{base_name}_MD5.{prefs.format}"

    if not py_getters.confirm_overwrite(main_archive):
        return Result.info("Cancelled.")

    try:
        py_archive.bundle_directory(
            source_dir, main_archive, prefs.format, prefs.password
        )

        staging = _stage_md5_listing(source_dir, main_archive.name)
        py_archive.bundle_directory(
            staging, hash_archive, prefs.format, prefs.password
        )

    except py_archive.ToolMissingError as exc:
        ctx.log.exception(exc)
        return Result.error(f"Required tool not found: {exc.message}")
    except py_archive.ArchiveError as exc:
        ctx.log.exception(exc)
        if exc.code == py_archive.EXIT_NO_FILES:
            return Result.error(empty_message)
        return Result.error(
            py_archive.explain_exit_code(exc, during="bundling")
        )

    py_ui.info(f"Archive created at: {main_archive}")

    return _offer_upload(ctx, main_archive, hash_archive, install_type)


def _stage_md5_listing(source_dir: Path, archive_name: str) -> Path:
    """Write ``<archive>.md5.txt`` into an emptied staging folder, return it."""
    staging = py_paths.ensure(py_paths.hash_staging_dir())

    for leftover in staging.iterdir():
        if leftover.is_dir():
            import shutil  # // Lazy import

            shutil.rmtree(leftover)
        else:
            leftover.unlink()

    py_archive.write_md5_file(
        py_archive.generate_md5_map(source_dir),
        staging / f"{archive_name}.md5.txt",
    )
    return staging


def _offer_upload(ctx: Context, main_archive: Path, hash_archive: Path,
                  install_type: str) -> Result:
    if not py_getters.confirm_upload([main_archive.name, hash_archive.name]):
        print(Fore.WHITE + "Skipping upload.")
        py_getters.reveal_in_explorer(main_archive)
        return Result.info("Archive bundled locally (upload skipped)")

    # // Lazy import -- pulls in requests
    from py_tmpfiles import TmpFilesClient, TmpFilesError

    client = TmpFilesClient(timeout=120)

    try:
        py_ui.info("Uploading to tmpfiles.org ...")

        # ORDER IS LOAD-BEARING: the hash archive must be uploaded first,
        # because the loader treats links[0] as the MD5 listing and every
        # link after it as a payload part. Announce each file so that
        # ordering is visible in the log rather than something you have to
        # take on trust -- the hash archive is only a few KB, so it finishes
        # before the main archive has even printed its first chunk.
        py_ui.note(f"\n[1/2] MD5 hashes -> {hash_archive.name} "
                   f"({py_ui.format_bytes(hash_archive.stat().st_size)})")
        links = client.upload_in_chunks(hash_archive, chunk_size=UPLOAD_CHUNK_SIZE)

        py_ui.note(f"\n[2/2] {install_type} -> {main_archive.name} "
                   f"({py_ui.format_bytes(main_archive.stat().st_size)})")
        links += client.upload_in_chunks(main_archive, chunk_size=UPLOAD_CHUNK_SIZE)

    except TmpFilesError as exc:
        ctx.log.exception(exc)
        return Result.error(f"Upload failed: {exc}")
    except Exception as exc:
        ctx.log.exception(exc)
        return Result.error(f"Unexpected error during upload: {exc}")

    if len(links) > 2:
        py_ui.success("Chunked upload successful!")
        print(Fore.WHITE + f"Parts uploaded: {len(links)}")
    else:
        py_ui.success("Upload successful!")

    _print_link_order(links)

    return _publish_share_block(ctx, links, main_archive, install_type)


def _print_link_order(links: list[str]) -> None:
    """Show the order the links were recorded in.

    This is the order they get encoded into the share block, and the order the
    loader reads them back in: [0] is the MD5 listing, [1:] are payload parts
    to be concatenated. If [0] is ever not the hash archive, the recipient's
    payload loses its first 90MB.
    """
    py_ui.note("\nLink order recorded in the share block:")
    for index, link in enumerate(links):
        role = "MD5 hashes" if index == 0 else f"payload part {index}"
        py_ui.note(f"  [{index}] {role:<16} {link}")


def _publish_share_block(ctx: Context, links: list[str], main_archive: Path,
                         install_type: str) -> Result:
    """Render the share block, save it, copy it, and show it to the user."""
    block = py_manifest.render(
        links,
        build_id=ctx.build_id,
        internal_name=main_archive.name,
        size_bytes=main_archive.stat().st_size if main_archive.exists() else 0,
        install_type=install_type,
    )

    md_path = py_paths.ensure(py_paths.gnizer_temp()) / py_manifest.suggested_filename()
    md_path.write_text(block, encoding="utf-8")

    py_getters.write_clipboard(block)
    py_getters.open_in_shell(md_path)

    py_ui.success("Share info saved & copied to clipboard!")
    print(Fore.WHITE + f"Markdown file: {md_path}")
    py_ui.note("\nTip: Send the copied text to your friend.")
    py_ui.note("They can paste it directly into Gnizer.")

    return Result.ok(
        f"Copied links to clipboard. Share it with your friends!\n{md_path}"
    )


# -------------------------
# region LOAD
# -------------------------
def load_data(ctx: Context) -> Result:
    """Download, verify and install a share block from the clipboard."""
    shared_text = py_getters.get_shared_text()

    try:
        manifest = py_manifest.parse(shared_text)
    except py_manifest.ManifestError as exc:
        ctx.log.exception(exc)
        return Result.error(str(exc))

    py_ui.info(f"Found {len(manifest.links)} part(s). Downloading to temp ...")

    # // Lazy import -- pulls in requests
    from py_tmpfiles import TmpFilesClient, TmpFilesError

    try:
        client = TmpFilesClient(timeout=120)
        _hash_archive, payload_archive = client.download_manifest(manifest)
    except TmpFilesError as exc:
        ctx.log.exception(exc)
        if "HTTP 404" in str(exc):
            return Result.error(
                "Download has expired, tell your friend to rebundle again!"
            )
        return Result.error(f"Download failed: {exc}")

    password = py_getters.get_archive_password()
    print("Loading archive ...")

    try:
        extracted_path = py_archive.extract_archive(payload_archive, password=password)
    except py_archive.ArchiveError as exc:
        ctx.log.exception(exc)
        return Result.error(
            "Extraction: " + py_archive.explain_exit_code(exc, during="extraction")
        )

    print("Nearly there ...")
    verdict = _verify_contents(extracted_path, manifest.install_type)
    if verdict is not None:
        return verdict

    manager = py_getters.get_mod_manager()
    profile = py_getters.get_mod_profile(manager)

    # // Lazy import
    import py_report

    if manifest.install_type == INSTALL_SAVEFOLDER:
        return _install_savefolder(profile, payload_archive, extracted_path, py_report)

    return _install_modlist(profile, payload_archive, extracted_path, py_report)


def _verify_contents(extracted_path: Path, declared_type: str) -> Result | None:
    """Tamper check, then a file-type scan. Returns a Result only to abort."""
    scanned_type = py_secure.auto_scan(extracted_path)

    if scanned_type != declared_type:
        py_ui.pause(
            Fore.RED
            + "\nIt looks like someone has sent you a potentially tampered copy/paste "
            "which could've harmed your Minecraft world or mod installation."
            "\n > Press ENTER to return back to the main menu"
        )
        return Result.error("Share contents did not match what the manifest declared.")

    is_safe, report, _results = py_secure.scan_extraction(extracted_path, declared_type)

    if not is_safe:
        print("\n" + py_ui.DIVIDER + "\n" + report)
        if not py_ui.ask_consent(Fore.RED + f"""
There are unusual file types inside the {declared_type} download, these could potentially harm your device.
This could be a false positive as this scan was made without mods in mind.\n-> Do you still want to continue"""):
            return Result.info("Installation cancelled.")

    return None


def _install_savefolder(profile, payload_archive: Path, extracted_path: Path,
                        py_report) -> Result:
    world = py_getters.get_world(profile.saves_dir, allow_new=True)

    if world.is_new:
        name = py_getters.get_new_world_name(profile.saves_dir)
        world.name = name
        world.path = profile.saves_dir / name
    else:
        print(f"{Fore.WHITE}World Directory: {world.path}")
        if world.name != payload_archive.stem and not py_ui.ask_consent(
            Fore.YELLOW
            + f"`{payload_archive.stem}` doesn't match world `{world.name}`, proceed anyway"
        ):
            return Result.info("Installation cancelled.")

    py_ui.divider()
    return py_report.install_world(extracted_path, world)


def _install_modlist(profile, payload_archive: Path, extracted_path: Path,
                     py_report) -> Result:
    expected_name = f"{profile.folder}"

    if payload_archive.stem != expected_name and not py_ui.ask_consent(
        Fore.YELLOW
        + f"`{payload_archive.stem}` doesn't match `{expected_name}`, proceed anyway"
    ):
        return Result.info("Installation cancelled.")

    py_ui.divider()
    return py_report.install_modlist(extracted_path, profile)


# -------------------------
# region MAINTENANCE
# -------------------------
def clear_temp_cache(ctx: Context) -> Result:
    temp_dir = py_paths.gnizer_temp()
    size = py_paths.directory_size(temp_dir)

    if size == 0:
        return Result.info("Temp cache is empty.")

    py_ui.warn(f"\nDelete: {temp_dir} ({py_ui.format_bytes(size)})")

    if not py_ui.ask_consent(
        "->This WILL DELETE BACKUPS and shared lists!"
        "\n->Are you sure you want to clear the Gnizer %temp%"
    ):
        return Result.info("Cancelled.")

    try:
        py_paths.clear_gnizer_temp()
    except OSError as exc:
        ctx.log.exception(exc)
        return Result.error(f"Failed to clear temp: {exc}")

    py_ui.success("Temp cache cleared.")
    return Result.ok("Temp cache & backups cleared successfully.")
