from py_imports import *
from PyQt5.QtWidgets import QApplication, QFileDialog
from py_archive import ArchiveBundler
from py_undbj import UnDBJ
from py_tmpfiles import TmpFilesClient, TmpFilesError
from py_report import review_and_install
import winreg, send2trash, shutil

# -------------------------
# COLORAMA (UI Enhancements)
# -------------------------
from colorama import init, Fore, Style
init(autoreset=True)


class App:
    DEBUG = False
    VERSION_FILE = "buildId.version"
    MENU_TITLE = "Main Menu"
    DIVIDER = "-- -x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x- --"

    # Registry paths for mod managers
    REGISTRY_MAP = {
        "Modrinth": [r"Software\ModrinthApp\Modrinth App"],
        "CurseForge (Overwolf)": [r"Software\Overwolf\CurseForge"],
        "CurseForge": [r"Software\OverwolfElectron"],
    }

    # Path mappings for mod managers
    PATH_MAP = {
        "Modrinth": {
            "db": Path(r"%appdata%\ModrinthApp\app.db"),
            "profiles": Path(r"%APPDATA%\ModrinthApp\profiles")
        },
        "CurseForge (Overwolf)": {
            "db": Path(r"%USERPROFILE%\curseforge\minecraft\Instances\\"),
            "profiles": Path(r"%USERPROFILE%\curseforge\minecraft\Instances")
        },
        "CurseForge": {
            "db": Path(r"%USERPROFILE%\curseforge\minecraft\Instances\\"),
            "profiles": Path(r"%USERPROFILE%\curseforge\minecraft\Instances")
        }
    }

    def __init__(self):
        self.build_id, self.IS_FIRST_TIME_SETUP = (
            self.load_or_init_build_id() if not self.DEBUG else (-1, True)
        )
        self.operation_text = None
        self.refresh_main_menu()

    def refresh_main_menu(self):
        """Refresh the main menu with updated temp cache size"""
        _, temp_bytes = self.get_modgnizer_temp_info()
        self.menu_main_definition = {
            "1": ("Load *MODS* from an ARCHIVE (or link)", "menu_load_mods_from_archive"),
            "2": ("Bundle *MODS* to an ARCHIVE", "menu_bundle_mods_to_archive"),
            "3": (f"Clear temp cache ({self.format_bytes(temp_bytes)})", "menu_clear_temp_cache"),
            "#": ("Quit", "menu_quit"),
        }
        self.menu_modes = {
            key: (label, getattr(self, handler))
            for key, (label, handler) in self.menu_main_definition.items()
        }

    # -------------------------
    # region CLEAR SCREEN
    # -------------------------
    def cls(self):
        if not self.DEBUG:
            os.system("cls")

    # -------------------------
    # region VERSION HANDLING
    # -------------------------
    def load_or_init_build_id(self):
        if not os.path.exists(self.VERSION_FILE):
            print(Fore.BLUE + "First‑time setup detected.\nRunning initial configuration...")
            with open(self.VERSION_FILE, "w") as f:
                f.write("1")
            return 1, True

        with open(self.VERSION_FILE) as f:
            new_id = int(f.read().strip()) + 1
        
        with open(self.VERSION_FILE, "w") as f:
            f.write(str(new_id))
        
        return new_id, False

    # -------------------------
    # region MENU HANDLERS
    # -------------------------
    def menu_clear_temp_cache(self):
        temp_path, temp_bytes = self.get_modgnizer_temp_info()
        
        if temp_bytes == 0:
            self.operation_text = "Temp cache is empty."
            return True

        print(Fore.YELLOW + f"\nDelete:\n{temp_path}\nTotal size: {self.format_bytes(temp_bytes)}")
        
        if not self.get_consent("Are you sure you want to clear the temp cache"):
            return True

        if self.clear_modgnizer_temp():
            print(Fore.GREEN + "Temp cache cleared.")
            self.refresh_main_menu()
        else:
            print(Fore.RED + "Failed to clear temp cache.")
        
        return True

    def menu_load_mods_from_archive(self):
        source = self.get_archive_source()
        if not source:
            return True

        kind, value = source
        archive_path = None

        if kind == "clipboard":
            try:
                manifest = TmpFilesClient.parse_modgnizer_manifest(value)
            except Exception as e:
                self.operation_text = Fore.RED + str(e)
                return True
            
            links = manifest.get("links", [])
            if not links:
                self.operation_text = Fore.RED + "No download links found in manifest."
                return True
            
            print(Fore.BLUE + f"Found {len(links)} part(s). Downloading to temp ...")
            
            try:
                client = TmpFilesClient(timeout=120)
                downloaded = client.download_from_paste(manifest)
                archive_path = downloaded[0]
            except Exception as e:
                self.operation_text = Fore.RED + f"Download failed: {e}"
                return True
        
        elif kind == "local":
            archive_path = value

        # Query user for password
        password = input(Fore.YELLOW + "\nEnter password for archive (leave blank if none): ").strip()

        # Extract archive
        extracted_path = None
        try:
            extracted_path = ArchiveBundler.extract_archive(archive_path, password=password)
        except Exception as e:
            exit_code = e.args[0]
            def get_conclusion():
                match exit_code:
                    case 10 | 11:
                        return "Incorrect password or archive is corrupted."
                    case 255:
                        return "User cancelled the operation"
                    case _:
                        return f"{e}"
            self.operation_text = Fore.RED + "Extraction: " + get_conclusion()
            return True
        finally:
            if not extracted_path:
                return True

        # Get mod manager and profile
        chosen_mod_manager = self.get_mod_managers()
        if not chosen_mod_manager:
            return True
        
        chosen_mod_profile = self.get_mod_profiles(chosen_mod_manager)
        if not chosen_mod_profile:
            return True

        # Check if archive name matches profile
        archive_name = archive_path.name
        profile_folder_with_ext = f"{chosen_mod_profile['folder']}{archive_path.suffix}"
        if archive_name != profile_folder_with_ext:
            msg = Fore.YELLOW + f"`{archive_name}` doesn't match `{profile_folder_with_ext}`, proceed anyway"
            if not self.get_consent(msg):
                return True

        # Review and install
        print(self.DIVIDER)
        try:
            review_and_install(
                extracted_path,
                chosen_mod_manager,
                chosen_mod_profile,
                self.get_consent,
                lambda text: setattr(self, "operation_text", text)
            )
        except Exception as e:
            self.operation_text = Fore.RED + f"Review/install step failed: {e}"
        
        return True

    def menu_bundle_mods_to_archive(self):
        # Get mod manager and profile
        chosen_mod_manager = self.get_mod_managers()
        if not chosen_mod_manager:
            return True
        
        chosen_profile = self.get_mod_profiles(chosen_mod_manager)
        if not chosen_profile:
            return True

        mod_profile_path = chosen_mod_manager["profiles_path"] / chosen_profile["folder"] / "mods"
        print(f"\n{Fore.WHITE}Selected Profile: {chosen_profile['name']}")
        print(f"{Fore.WHITE}Mods Directory: {mod_profile_path}")

        # Get archive preferences
        archive_prefs = self.get_archive_preferences()
        if not archive_prefs:
            return True

        # Create output directory
        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        bundled_dir = temp_root / "ModGnizer" / "bundled"
        bundled_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = bundled_dir / f"{chosen_profile['folder']}.{archive_prefs['format']}"
        
        if not self.get_consent_delete_file(output_path):
            return True

        # Bundle the archive
        bundler = ArchiveBundler(mod_profile_path)
        format_handlers = {
            "zip": lambda: bundler.bundle_zip(output_path),
            "7z": lambda: bundler.bundle_7z(output_path, archive_prefs["password"]),
            "rar": lambda: bundler.bundle_rar(output_path, archive_prefs["password"]),
        }
        
        try:
            format_handlers[archive_prefs["format"]]()
        except FileNotFoundError as e:
            self.operation_text = Fore.RED + f"Required tool not found: {e}"
            return True
        except Exception as e:
            self.operation_text = Fore.RED + f"Unexpected error: {e}" if not "10" in str(e.args[0]) else Fore.RED + f"Error: Mod Profile '{chosen_profile["name"]}' contains no mods to bundle."
            return True

        print(Fore.BLUE + f"Archive created on Desktop: {output_path}")
        
        # Ask about upload
        try:
            self.get_consent_upload_to_fileio(output_path)
        except Exception as e:
            self.operation_text = Fore.RED + f"Upload step failed: {e}"
        
        return True

    def menu_quit(self):
        print(Fore.WHITE + "Goodbye.")
        return False

    # -------------------------
    # region GETTER MENUS
    # -------------------------
    def get_mod_managers(self):
        print(Style.BRIGHT + "\n**Select your Mod Manager:**\n")
        
        managers = self.detect_mod_managers()
        manager_list = list(managers.items())
        
        if not manager_list:
            self.operation_text = Fore.RED + "No supported mod managers were detected."
            print(Fore.WHITE + "Please install Modrinth or CurseForge and try again.")
            return None
        
        for i, (name, _) in enumerate(manager_list, 1):
            print(Fore.LIGHTBLACK_EX + f"{i}. {name}")
        
        choice = self._get_numeric_input(len(manager_list))
        return None if choice is None else manager_list[choice - 1][1]

    def get_mod_profiles(self, chosen_mod_manager):
        undb = UnDBJ(chosen_mod_manager["db_path"])
        profiles = undb.get_internal_profiles()
        
        if not profiles:
            self.operation_text = Fore.RED + "No profiles were detected in this mod manager."
            return None
        
        print(self.DIVIDER)
        
        for i, p in enumerate(profiles, 1):
            print(Fore.LIGHTBLACK_EX + f"{i}. {p['display']}")
        
        print(Style.BRIGHT + "\n**Select a Profile**")
        
        choice = self._get_numeric_input(len(profiles))
        return None if choice is None else profiles[choice - 1]

    def get_archive_preferences(self):
        bundler = ArchiveBundler(Path("."))
        available = {"1": "zip"}
        
        print(Style.BRIGHT + "\n**Choose archive format:**")
        print(Fore.LIGHTBLACK_EX + "1. ZIP")
        
        if bundler.has_7z():
            print(Fore.LIGHTBLACK_EX + "2. 7Z (password supported)")
            available["2"] = "7z"
        
        if bundler.has_winrar():
            print(Fore.LIGHTBLACK_EX + "3. RAR (password supported) + recommended")
            available["3"] = "rar"
        
        choice = input(Fore.WHITE + "> ").strip()
        if choice not in available:
            self.operation_text = Fore.RED + "Invalid number for archive selection."
            return None
        
        fmt = available[choice]
        
        if fmt == "zip":
            print(Fore.WHITE + "Zipping...")
            return {"format": "zip", "password": None}
        
        password = input("\n" + Fore.YELLOW + "Enter a password for the archive (required): ").strip()
        if not password:
            self.operation_text = Fore.RED + "Password is required."
            return None
        
        return {"format": fmt, "password": password}

    def get_archive_source(self):
        print(Style.BRIGHT + "\n**Load MODS from:**\n")
        print(Fore.LIGHTBLACK_EX + "1. Read from clipboard (MODGNIZER shared text)")
        print(Fore.LIGHTBLACK_EX + "2. Local archive file")
        
        choice = input(Fore.WHITE + "> ").strip()
        
        if choice == "1":
            print(Fore.CYAN + "\nPress ENTER to read MODGNIZER data from your clipboard.\n" +
                  Fore.LIGHTBLACK_EX + "(Make sure you copied the entire shared block)")
            input()
            
            text = self.read_from_clipboard()
            if not text:
                self.operation_text = Fore.RED + "Clipboard is empty or does not contain text."
                return None
            
            if "# MODGNIZER" not in text or "## Download Links" not in text:
                self.operation_text = Fore.RED + "Clipboard content is not a valid MODGNIZER share."
                return None
            
            return ("clipboard", text)
        
        elif choice == "2":
            try:
                app = QApplication.instance() or QApplication(sys.argv)
                file_path, _ = QFileDialog.getOpenFileName(
                    None, "Select Mod Archive",
                    str(Path.home() / "Desktop"),
                    "Archives (*.zip *.7z *.rar);;All Files (*)"
                )
                
                if not file_path:
                    self.operation_text = Fore.RED + "No file selected."
                    return None
                
                return ("local", Path(file_path))
            except Exception as e:
                self.operation_text = Fore.RED + f"Failed to open file dialog: {e}"
                return None
        
        self.operation_text = Fore.RED + "Invalid selection for archive source."
        return None

    def get_consent(self, message: str):
        print("\n" + Fore.YELLOW + f"{message}? (y/n)")
        choice = input(Fore.YELLOW + "> ").strip().lower()
        
        if choice in ("y", "yes"):
            return True
        elif choice in ("n", "no"):
            return False
        
        self.operation_text = Fore.RED + "Invalid selection."
        return False

    def get_consent_delete_file(self, file_path: Path):
        if not file_path.exists():
            return True
        
        print(Fore.RED + f"\nThe file already exists:\n{file_path}")
        print(Fore.RED + "Delete it and continue? (y/n)")
        
        choice = input(Fore.RED + "> ").strip().lower()
        if choice not in ("y", "yes"):
            print(Fore.WHITE + "Cancelled.")
            return False
        
        try:
            send2trash.send2trash(str(file_path))
            print(Fore.BLUE + "Existing file moved to Recycle Bin.")
            return True
        except Exception as e:
            self.operation_text = Fore.RED + f"Failed to delete file: {e}"
            return False

    def get_consent_upload_to_fileio(self, archive_path: Path):
        if not archive_path.exists() or not archive_path.is_file():
            self.operation_text = Fore.RED + "Archive not found for upload."
            return
        
        print("\n" + Fore.YELLOW + "--> Note: tmpfiles.org automatically deletes uploads after 60 minutes. <-- ")
        print(Fore.RED + "--> [!] if your zip is NOT password protected, be careful that others could download! <--  [!] [!]")
        
        if not self.get_consent("Upload this archive to tmpfiles.org to share with friends"):
            print(Fore.WHITE + "Skipping upload.")
            self.reveal_in_explorer(archive_path)
            self.operation_text = "Archive bundled locally (upload skipped)"
            return
        
        client = TmpFilesClient(timeout=120)
        try:
            print(Fore.BLUE + "Uploading to tmpfiles.org ...")
            result = client.upload_in_chunks(archive_path, chunk_size=90 * 1024 * 1024)
            links = result.get("links", [])
            
            if not links:
                raise Exception("Upload completed but no links returned.")
            
            if len(links) == 1:
                self.save_links_md_and_copy_to_clipboard([links[0]], archive_path)
                print(Fore.GREEN + "Upload successful!")
                print(Fore.WHITE + "A MODGNIZER share block has been copied to your clipboard.")
            else:
                print(Fore.GREEN + "Chunked upload successful!")
                print(Fore.WHITE + f"Parts uploaded: {len(links)}")
                self.save_links_md_and_copy_to_clipboard(links, archive_path)
                print(Fore.LIGHTBLACK_EX + "\nTip: Send the copied text to your friend.")
                print(Fore.LIGHTBLACK_EX + "They can paste it directly into ModGnizer.")
        except TmpFilesError as e:
            self.operation_text = Fore.RED + "Upload failed:" + Fore.LIGHTBLACK_EX + str(e)
        except Exception as e:
            self.operation_text = Fore.RED + f"Unexpected error during upload: {e}"

    def get_modgnizer_temp_info(self):
        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        temp_dir = temp_root / "ModGnizer"
        
        if not temp_dir.exists():
            return temp_dir, 0
        
        total_size = sum(p.stat().st_size for p in temp_dir.rglob("*") if p.is_file())
        return temp_dir, total_size

    # -------------------------
    # region MAIN MENU
    # -------------------------
    def menu(self):
        self.refresh_main_menu()
        self.cls()
        
        print(Fore.WHITE + f" // ModGnizer (b{self.build_id}) //\nby @LukieD4 on GitHub\n")
        print(Style.BRIGHT + self.MENU_TITLE)
        
        for key, (label, _) in self.menu_modes.items():
            print(Fore.LIGHTBLACK_EX + f"{key}. {label}")
        
        if self.operation_text:
            print(f"\n{Fore.LIGHTBLACK_EX}[INFO: {self.operation_text}]")
        
        choice = input(Fore.WHITE + "> ").strip()
        
        if choice in self.menu_modes:
            _, handler = self.menu_modes[choice]
            return handler()
        else:
            self.operation_text = Fore.RED + "Invalid choice."
            return True

    # -------------------------
    # region UTILS
    # -------------------------
    def detect_installed_app(self, registry_paths):
        for reg_path in registry_paths:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path):
                    return True, reg_path
            except (FileNotFoundError, OSError):
                continue
        return False, None

    def detect_mod_managers(self):
        mod_managers = {}
        
        for name, registry_paths in self.REGISTRY_MAP.items():
            found, _ = self.detect_installed_app(registry_paths)
            mod_managers[name] = {
                "name": name,
                "installed": found,
                "profiles_path": Path(os.path.expandvars(self.PATH_MAP[name]["profiles"])),
                "db_path": Path(os.path.expandvars(self.PATH_MAP[name]["db"])),
            }
        
        # Handle CurseForge/Overwolf duplicates
        if (mod_managers.get("CurseForge", {}).get("installed") and 
            mod_managers.get("CurseForge (Overwolf)", {}).get("installed")):
            mod_managers.pop("CurseForge (Overwolf)", None)
            mod_managers["CurseForge"]["profiles_path"] = Path(os.path.expandvars(
                self.PATH_MAP["CurseForge (Overwolf)"]["profiles"]))
        else:
            mod_managers.pop("CurseForge", None)
        
        return {k: v for k, v in mod_managers.items() if v["installed"]}

    def save_links_md_and_copy_to_clipboard(self, links: list[str], original_file: Path):
        if not links:
            return
        
        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        modgnizer_temp = temp_root / "ModGnizer"
        modgnizer_temp.mkdir(parents=True, exist_ok=True)
        
        internal_name = original_file.name
        size_bytes = original_file.stat().st_size if original_file.exists() else 0
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        short_ts = datetime.now().strftime("%Y%m%d%H%M%S")
        
        md_content = f"""```# MODGNIZER

A friend has shared their modlist with you!

*Internal name: "{internal_name}"  
Size of modlist: {size_bytes} bytes  
Date of modlist: {timestamp}*

**Instructions**
- Highlight *this entire text*
    - CTRL + C
- Open ModGnizer
    - Select: 'Load *MODS* ...'
    - Select: 'Read from clipboard'
    - [DO NOT PASTE]
    - Press Enter

- ModGnizer will automatically do the hard work :3

{self.DIVIDER}

## Download Links
"""
        md_content += "\n".join(links) + "\n```"
        
        # Save markdown file
        md_filename = f"MODGNIZER_shared_modlist_{short_ts}.md"
        md_path = modgnizer_temp / md_filename
        md_path.write_text(md_content, encoding="utf-8")
        
        # Copy to clipboard
        app = QApplication.instance() or QApplication(sys.argv)
        app.clipboard().setText(md_content)
        self.reveal_in_notepad(md_path)
        
        print(Fore.GREEN + "Share info saved & copied to clipboard!")
        print(Fore.WHITE + f"Markdown file: {md_path}")
        self.operation_text = f"Copied links to clipboard. Share it with your friends!\n{md_path}"

    def read_from_clipboard(self):
        app = QApplication.instance() or QApplication(sys.argv)
        text = app.clipboard().text()
        return text if text and text.strip() else None

    def reveal_in_explorer(self, path: Path):
        try:
            os.startfile(path.parent if path.exists() else str(path))
        except Exception as e:
            print(Fore.RED + f"Failed to open File Explorer: {e}")
    
    def reveal_in_notepad(self, path: Path):
        try:
            os.startfile(path)
        except Exception as e:
            print(Fore.RED + f"Failed to open Notepad: {e}")

    def clear_modgnizer_temp(self):
        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        temp_dir = temp_root / "ModGnizer"
        
        if not temp_dir.exists():
            return True
        
        try:
            shutil.rmtree(temp_dir)
            self.operation_text = "Temp cache cleared successfully."
            return True
        except Exception as e:
            self.operation_text = f"Failed to clear temp cache: {e}"
            return False

    def format_bytes(self, size: int):
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def _get_numeric_input(self, max_value: int):
        """Helper to get numeric input within range"""
        choice = input(Fore.WHITE + "> ").strip()
        if not choice.isdigit() or not (1 <= int(choice) <= max_value):
            self.operation_text = Fore.RED + "Invalid selection."
            return None
        return int(choice)

    # -------------------------
    # region RUN
    # -------------------------
    def run(self):
        running = True
        while running:
            running = self.menu()


App().run()