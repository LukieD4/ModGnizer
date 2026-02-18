from py_imports import *
from PyQt5.QtWidgets import QApplication, QFileDialog
from py_archive import ArchiveBundler
from py_undbj import UnDBJ
from py_tmpfiles import TmpFilesClient, TmpFilesError
from py_report import review_and_install_mods, install_world
from py_secure import scan_extraction, auto_scan
from py_updater import check_for_updates
import winreg, send2trash, shutil

# -------------------------
# COLORAMA (UI Enhancements)
# -------------------------


class App:
    
    VERSION_FILE = "buildId.version"
    MENU_TITLE = "\n-x-x- { Main Menu } -x-x-\n"
    DIVIDER = "-- -x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x- --"

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

    @staticmethod
    def is_running_as_exe():
        # return True
        if "__compiled__" in globals():
            return True
        return False
    
    @staticmethod
    def get_runtime_base():
        if "__compiled__" in globals():
            return Path(sys.argv[0]).resolve().parent
        return Path(__file__).parent
    
    @staticmethod
    def get_temp_runtime_base():
        # Always points to the debundled folder when running as EXE
        return Path(__file__).resolve().parent



    def __init__(self):
        self.debug = False
        self.log = None
        self._cls_skip = 1
        
        # Check for updates and ask user consent
        user_accepted_update = check_for_updates(
            self.get_temp_runtime_base() / self.VERSION_FILE,
            lambda:self.get_consent("\n\n\n\n\nDo you want to update")
        ) if self.is_running_as_exe() else False
        if not user_accepted_update: self.cls()
        
        self.build_id, self.IS_FIRST_TIME_SETUP = self.load_or_init_build_id()
        
        self.operation_text = None
        self.refresh_main_menu()

    def refresh_main_menu(self):
        """Refresh the main menu with updated temp cache size"""
        _, temp_bytes = self.get_gnizer_temp_info()
        self.menu_main_definition = {
            "1": ("Load *DATA* from an ARCHIVE (or link)",                  "menu_load_data_from_archive"),
            "2": ("Bundle *MODS* to an ARCHIVE",                            "menu_bundle_mods_to_archive"),
            "3": ("Bundle *WORLD* to an ARCHIVE",                           "menu_bundle_world_to_archive"),
            "4": (f"Clear temp cache ({self.format_bytes(temp_bytes)})",    "menu_clear_temp_cache"),
            "5": ("Log errors",                                             "menu_toggle_error_logging"),
            "#": ("Quit",                                                   "menu_quit"),
        }
        self.menu_modes = {
            key: (label, getattr(self, handler))
            for key, (label, handler) in self.menu_main_definition.items()
        }
    
    def menu_toggle_error_logging(self):
        if self.debug: return True
        self.debug = True

        import logging

        logging.basicConfig(
            filename="err.log",
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(message)s",
            filemode="w"
        )

        self.log = logging.getLogger(f"Gnizer {self.build_id}")

        self._log("Started", "info")

        self.operation_text = "Error logging ENABLED."
        self.refresh_main_menu()
        return True

    def _log(self, msg, level="info"):
        if not self.log: return

        level = level.lower()

        if level == "info":
            self.log.info(msg)
        elif level == "warning":
            self.log.warning(msg)
        elif level == "error":
            self.log.error(msg)
        elif level == "debug":
            self.log.debug(msg)
        else:
            self.log.info(msg)


    # -------------------------
    # region CLEAR SCREEN
    # -------------------------
    def cls(self):
        if self._cls_skip > 0:
            self._cls_skip -= 1
        elif not self.debug:
            os.system("cls")
        

    # -------------------------
    # region VERSION HANDLING
    # -------------------------
    def load_or_init_build_id(self):
        self._log("IN -> load_or_init_build_id", "info")

        version_path = self.get_temp_runtime_base() / self.VERSION_FILE

        # DEV MODE (.py)
        if not version_path.exists() and not self.is_running_as_exe():
            print(Fore.BLUE + "First-time setup detected.\n (reserved)...")
            version_path.write_text("1", encoding="utf-8")
            return 1, True

        # EXE MODE (Nuitka onefile)
        if self.is_running_as_exe():
            try:
                return int(version_path.read_text().strip()), False
            except Exception:
                return 0, False

        # DEV MODE increment logic
        new_id = int(version_path.read_text().strip()) + 1
        version_path.write_text(str(new_id), encoding="utf-8")
        return new_id, False

    # -------------------------
    # region MENU HANDLERS
    # -------------------------
    def menu_clear_temp_cache(self):
        self._log("IN -> menu_clear_temp_cache","info")

        temp_path, temp_bytes = self.get_gnizer_temp_info()
        
        if temp_bytes == 0:
            self.operation_text = "Temp cache is empty."
            return True

        print(Fore.YELLOW + f"\nDelete:\n{temp_path}\nTotal size: {self.format_bytes(temp_bytes)}")
        
        if not self.get_consent("This WILL DELETE BACKUPS and shared lists!\nAre you sure you want to clear the %temp%\Gnizer"):
            return True

        if self.clear_gnizer_temp():
            print(Fore.GREEN + "Temp cache cleared.")
            self.refresh_main_menu()
        else:
            print(Fore.RED + "Failed to clear temp cache.")
        
        return True
    

    def menu_bundle_world_to_archive(self):
        self._log("IN -> menu_bundle_world_to_archive", "info")

        # Get mod manager and profile
        chosen_mod_manager = self.get_mod_managers()
        if not chosen_mod_manager:
            return True
        
        chosen_profile = self.get_mod_profiles(chosen_mod_manager)
        if not chosen_profile:
            return True

        profile_path = chosen_mod_manager["profiles_path"] / chosen_profile["folder"]
        print(f"\n{Fore.WHITE}Selected Profile: {chosen_profile['name']}")
        
        chosen_world = self.get_worlds_of_mod_profile(profile_path, add_world=False)
        if not chosen_world:
            return True
        print(f"{Fore.WHITE}World Directory: {chosen_world["path"]}")

        # Get archive preferences
        archive_prefs = self.get_archive_preferences()
        if not archive_prefs:
            return True

        # Create output directory
        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        bundled_dir = temp_root / "Gnizer" / "world_bundled"
        bundled_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = bundled_dir / f"{chosen_world['name']}.{archive_prefs['format']}"
        
        if not self.get_consent_delete_file(output_path):
            return True

        # Bundle the archive
        bundler = ArchiveBundler(chosen_world["path"])
        format_handlers = {
            "zip": lambda: bundler.bundle_zip(output_path),
            "7z": lambda: bundler.bundle_7z(output_path, archive_prefs["password"]),
            "rar": lambda: bundler.bundle_rar(output_path, archive_prefs["password"]),
        }
        
        try:
            format_handlers[archive_prefs["format"]]()
        except FileNotFoundError as e:
            self._log(e,"critical")
            self.operation_text = Fore.RED + f"Required tool not found: {e}"
            return True
        except Exception as e:
            self._log(e,"critical")
            # Keep original behavior but avoid crashing on unexpected string formatting
            try:
                err_arg = e.args[0]
            except Exception:
                err_arg = str(e)
            if isinstance(err_arg, str) and "10" in err_arg:
                self.operation_text = Fore.RED + f"Error: World '{chosen_world['name']}' contains no data to bundle."
            else:
                self.operation_text = Fore.RED + f"Unexpected error: {e}"
            return True

        print(Fore.BLUE + f"Archive created in %TEMP%: {output_path}")
        
        # Ask about upload
        try:
            self.get_consent_upload_to_fileio(output_path, "savefolder")
        except Exception as e:
            self._log(e,"critical")
            self.operation_text = Fore.RED + f"Upload step failed: {e}"
        
        return True

    def menu_load_data_from_archive(self):
        self._log("IN -> menu_load_data_from_archive","info")

        source = self.get_archive_source()
        if not source:
            return True

        kind, value = source
        archive_path = None
        manifest = None

        self._log(f"KIND -> {kind}", "info")
        if kind == "clipboard":
            try:
                manifest = TmpFilesClient.parse_gnizer_manifest(value)
            except Exception as e:
                self._log(e,"critical")
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
                self._log(e,"critical")
                http404_code = "HTTP 404" in e.args[0]

                self.operation_text = Fore.RED + f"Download has expired, tell your friend to rebundle again!" if http404_code else Fore.RED + f"Download failed: {e}"
                return True
        
        elif kind == "local":
            archive_path = value

        # Query user for password
        password = input(Fore.YELLOW + "\nEnter password for archive (leave blank if none): ").strip()

        # Extract archive
        extracted_path = None
        try:
        
            extracted_path = ArchiveBundler.extract_archive(archive_path, password=password)
            auto_scan_installation_type = auto_scan(extracted_path)

            # This should be reworked, it's a patch to prevent manifest from being NoneType
            manifest = {"type_of_install":auto_scan_installation_type} if not manifest else manifest  

            # Detect a tampered manifest
            if auto_scan_installation_type != manifest["type_of_install"]:
                input(Fore.RED + "\nIt looks like someone has sent you a potentially tampered copy/paste which could've harmed your Minecraft world or mod installation.\n > Press ENTER to return back to the main menu")
                return True

                      
            
            is_safe, final_message, scan_results = scan_extraction(extracted_path, manifest["type_of_install"])
            if not is_safe:
                print("\n" + self.DIVIDER + "\n" + final_message)
                if not self.get_consent(Fore.RED + f"""
There are unusual file types inside the {manifest["type_of_install"]} download, these could potentially harm your device.
This could be a false positive as this scan was made without mods in mind.\n-> Do you still want to continue"""):
                    return True
        
        
        except Exception as e:
            self._log(e,"critical")
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
        

        # HANDLE: SAVEFILE   /    MODLIST
        archive_name = archive_path.name
        archive_stem = archive_path.stem
        profile_folder_with_ext = f"{chosen_mod_profile['folder']}{archive_path.suffix}"
        self._log(f"HANDLE -> Type of install {manifest["type_of_install"]}", "info")

        match manifest["type_of_install"]:
            case "savefolder":

                chosen_world = self.get_worlds_of_mod_profile(chosen_mod_profile["path"], add_world=True)
                profile_saves_dir = chosen_world["path"].parent
                if not chosen_world:
                    return True
                print(f"{Fore.WHITE}World Directory: {chosen_world["path"]}")

                chosen_world_is_fake = chosen_world["fake_world"]
                if chosen_world_is_fake:
                    def _validate_world_name(raw: str):
                        raw = raw.strip()
                        if raw == "":
                            return None
                        # allow letters, numbers, spaces, hyphens and underscores only
                        if not re.match(r'^[A-Za-z0-9 _-]+$', raw):
                            raise ValueError("Name contains invalid characters")
                        candidate_dir = profile_saves_dir / raw
                        if candidate_dir.exists():
                            raise ValueError("A directory with that name already exists")
                        return raw

                    name = self.query_input_with_retry(
                        f"{Fore.WHITE}\nEnter a Name for your New World:\n > ",
                        _validate_world_name,
                        cancel_values=("c", "q"),
                        error_message="Invalid world name."
                    )
                    if name is None:
                        return True
                    # store the chosen name/path for later steps
                    chosen_world["name"] = name
                    chosen_world["path"] = profile_saves_dir / name


                # Review world name
                if not chosen_world_is_fake:
                    if chosen_world["name"] != archive_path.stem:
                        msg = Fore.YELLOW + f"`{archive_name}` doesn't match `{profile_folder_with_ext}`, proceed anyway"
                        if not self.get_consent(msg):
                            return True
                
                # Review and install
                print("\n" + self.DIVIDER)
                try:
                    install_world(
                        extracted_path,
                        chosen_world,
                        self.get_consent,
                        lambda text: setattr(self, "operation_text", text)
                    )
                except Exception as e:
                    self._log(e,"critical")
                    self.operation_text = Fore.RED + f"Review/install step failed: {e}"

        
            case "modlist":
                # Check if archive name matches profile
                if archive_name != profile_folder_with_ext:
                    msg = Fore.YELLOW + f"`{archive_name}` doesn't match `{profile_folder_with_ext}`, proceed anyway"
                    if not self.get_consent(msg):
                        return True

                # Review and install
                print("\n" + self.DIVIDER)
                try:
                    review_and_install_mods(
                        extracted_path,
                        chosen_mod_manager,
                        chosen_mod_profile,
                        self.get_consent,
                        lambda text: setattr(self, "operation_text", text)
                    )
                except Exception as e:
                    self._log(e,"critical")
                    self.operation_text = Fore.RED + f"Review/install step failed: {e}"
                
        return True

    def menu_bundle_mods_to_archive(self):
        self._log("IN -> menu_bundle_mods_to_archive", "info")

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
        bundled_dir = temp_root / "Gnizer" / "bundled"
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
            self._log(e,"critical")
            self.operation_text = Fore.RED + f"Required tool not found: {e}"
            return True
        except Exception as e:
            self._log(e,"critical")
            # Keep original behavior but avoid crashing on unexpected string formatting
            try:
                err_arg = e.args[0]
            except Exception:
                err_arg = str(e)
            if err_arg == 10:
                self.operation_text = Fore.RED + f"Error: Mod Profile '{chosen_profile['name']}' contains no mods to bundle."
            else:
                self.operation_text = Fore.RED + f"Unexpected error: {e}"
            return True

        print(Fore.BLUE + f"Archive created at: {output_path}")
        
        # Ask about upload
        try:
            self.get_consent_upload_to_fileio(output_path, "modlist")
        except Exception as e:
            self._log(e,"critical")
            self.operation_text = Fore.RED + f"Upload step failed: {e}"
        
        return True

    def menu_quit(self):
        self._log("IN -> menu_quit", "info")
        print(Fore.WHITE + "Goodbye.")
        return False

    # -------------------------
    # region GETTER MENUS
    # -------------------------
    def get_mod_managers(self):
        self._log("GET -> get_mod_managers", "info")

        print(Style.BRIGHT + "\n-x-x- { Select a **MOD MANAGER** } -x-x-\n")
        
        managers = self.detect_mod_managers()
        manager_list = list(managers.items())
        
        if not manager_list:
            self.operation_text = Fore.RED + "No supported mod managers were detected."
            print(Fore.WHITE + "Please install Modrinth or CurseForge and try again.")
            return None
        
        for i, (name, _) in enumerate(manager_list, 1):
            print(Fore.LIGHTBLACK_EX + f"{i}. {name}")
        
        # Use the improved numeric input which loops until valid or cancelled
        choice = self.query_numeric_input(len(manager_list))
        return None if choice is None else manager_list[choice - 1][1]

    def get_mod_profiles(self, chosen_mod_manager):
        self._log("GET -> get_mod_profiles", "info")

        undb = UnDBJ(chosen_mod_manager["db_path"])
        profiles = undb.get_internal_profiles()
        
        if not profiles:
            self.operation_text = Fore.RED + "No profiles were detected in this mod manager."
            return None
        
        print("\n" + self.DIVIDER)
        
        for i, p in enumerate(profiles, 1):
            print(Fore.LIGHTBLACK_EX + f"[{i}]. {p['display']}")
        
        print(Style.BRIGHT + "\n-x-x- { Select a **MOD PROFILE** } -x-x-\n")
        
        choice = self.query_numeric_input(len(profiles))
        return None if choice is None else profiles[choice - 1]
    
    def get_worlds_of_mod_profile(self, profile_path, add_world=False):
        self._log("GET -> get_worlds_of_mod_profile", "info")

        undb = UnDBJ(profile_path)
        worlds = undb.get_internal_worlds(add_world=add_world)
        
        if not worlds:
            self.operation_text = Fore.RED + "No worlds were detected in this mod profile."
            return None
        
        print("\n" + self.DIVIDER)
        
        for i, p in enumerate(worlds, 1):
            print(Fore.LIGHTBLACK_EX + f"[{i}]. {p["display"]}")
        
        print(Style.BRIGHT + "\n-x-x- { Select a **WORLD** } -x-x-\n")
        
        choice = self.query_numeric_input(len(worlds))
        return None if choice is None else worlds[choice - 1]

    def get_archive_preferences(self):
        self._log("GET -> get_archive_preferences", "info")

        bundler = ArchiveBundler(Path("."))
        available = {"1": "zip"}
        
        # Loop until user provides a valid selection or cancels
        while True:
            print(Style.BRIGHT + "\n-x-x- { Choose **ARCHIVE** Format } -x-x-\n")
            print(Fore.LIGHTBLACK_EX + "[1]. ZIP")
            
            if bundler.has_7z():
                print(Fore.LIGHTBLACK_EX + "[2]. 7Z (password supported)")
                available["2"] = "7z"
            
            if bundler.has_winrar():
                print(Fore.LIGHTBLACK_EX + "[3]. RAR (password supported) + recommended")
                available["3"] = "rar"
            
            choice = input(Fore.WHITE + "> ").strip()
            if choice.lower() in ("c", "q"):
                self.operation_text = Fore.RED + "Cancelled."
                return None

            if choice not in available:
                print(Fore.RED + "Invalid number for archive selection. Enter a valid number or 'c' to cancel.")
                continue
            
            fmt = available[choice]
            
            if fmt == "zip":
                print(Fore.WHITE + "Zipping...")
                return {"format": "zip", "password": None}
            
            # For 7z/rar require password
            while True:
                password = input("\n" + Fore.YELLOW + "Enter a password for the archive (required, or 'c' to cancel): ").strip()
                if password.lower() in ("c", "q"):
                    self.operation_text = Fore.RED + "Cancelled."
                    return None
                if not password:
                    print(Fore.RED + "Password is required for this format. Enter a password or 'c' to cancel.")
                    continue
                return {"format": fmt, "password": password}

    def get_archive_source(self):
        self._log("GET -> get_archive_source", "info")

        # Loop until user provides a valid selection or cancels
        while True:
            print(Style.BRIGHT + "\n-x-x- { Load *DATA* From? } -x-x-\n")
            print(Fore.LIGHTBLACK_EX + "[1]. Read from clipboard (Gnizer shared text)")
            print(Fore.LIGHTBLACK_EX + "[2]. Local archive file")
            print(Fore.LIGHTBLACK_EX + "\n or 'c' to cancel and return to the previous menu. ^\n")
            
            choice = input(Fore.WHITE + "> ").strip().lower()
            
            if choice in ("c", "q"):
                self.operation_text = Fore.RED + "Cancelled."
                return None

            if choice == "1":
                print(Fore.CYAN + "\nPress ENTER to read Gnizer data from your clipboard.\n" +
                      Fore.LIGHTBLACK_EX + "(Make sure you copied the entire shared block)")
                input()
                
                text = self.read_from_clipboard()
                if not text:
                    self.operation_text = Fore.RED + "Clipboard is empty or does not contain text."
                    # allow retry instead of returning to main menu
                    print(Fore.LIGHTBLACK_EX + "Clipboard empty or invalid. Try again or press 'c' to cancel.")
                    continue
                
                if "# Gnizer" not in text or "## Download Links" not in text:
                    self.operation_text = Fore.RED + "Clipboard content is not a valid Gnizer share."
                    print(Fore.LIGHTBLACK_EX + "Clipboard content invalid. Try again or press 'c' to cancel.")
                    continue
                
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
                        print(Fore.LIGHTBLACK_EX + "No file selected. Try again or press 'c' to cancel.")
                        continue
                    
                    return ("local", Path(file_path))
                except Exception as e:
                    self._log(e,"critical")
                    self.operation_text = Fore.RED + f"Failed to open file dialog: {e}"
                    print(Fore.LIGHTBLACK_EX + "File dialog failed. Try again or press 'c' to cancel.")
                    continue
            
            print(Fore.RED + "Invalid selection for archive source. Enter 1, 2, or 'c' to cancel.")

    def get_consent(self, message: str):
        self._log("GET (CONSENT) -> get_consent", "info")

        # Loop until user provides a valid y/n or cancels
        while True:
            print(Fore.YELLOW + f"{message}? (y/n)  (or 'c' to cancel)")
            choice = input(Fore.YELLOW + "> ").strip().lower()
            
            if choice in ("y", "yes"):
                return True
            elif choice in ("n", "no"):
                return False
            elif choice in ("c", "q"):
                self.operation_text = Fore.RED + "Cancelled."
                return False
            
            print(Fore.RED + "Invalid selection. Please enter 'y' or 'n', or 'c' to cancel.")

    def get_consent_delete_file(self, file_path: Path):
        self._log("GET (CONSENT) -> get_consent_delete_file", "info")

        if not file_path.exists():
            return True
        
        # Loop until user confirms or cancels
        while True:
            print(Fore.RED + f"\nThe file already exists:\n{file_path}")
            print(Fore.RED + "Delete it and continue? (y/n)  (or 'c' to cancel)")
            
            choice = input(Fore.RED + "> ").strip().lower()
            if choice in ("c", "q"):
                print(Fore.WHITE + "Cancelled.")
                self.operation_text = Fore.RED + "Cancelled."
                return False
            if choice not in ("y", "yes"):
                print(Fore.WHITE + "Cancelled.")
                return False
            
            try:
                send2trash.send2trash(str(file_path))
                print(Fore.BLUE + "Existing file moved to Recycle Bin.")
                return True
            except Exception as e:
                self._log(e,"critical")
                self.operation_text = Fore.RED + f"Failed to delete file: {e}"
                return False

    def get_consent_upload_to_fileio(self, archive_path: Path, type_of_upload: str):
        self._log("GET (CONSENT) -> get_consent_upload_to_fileio", "info")

        if not type_of_upload:
            self.operation_text = Fore.RED + "(internal error) type_of_upload was not declared, is it a modlist or savefolder?"

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
                self.save_links_md_and_copy_to_clipboard([links[0]], archive_path, type_of_upload)
                print(Fore.GREEN + "Upload successful!")
                print(Fore.WHITE + "A Gnizer share block has been copied to your clipboard.")
            else:
                print(Fore.GREEN + "Chunked upload successful!")
                print(Fore.WHITE + f"Parts uploaded: {len(links)}")
                self.save_links_md_and_copy_to_clipboard(links, archive_path, type_of_upload)
                print(Fore.LIGHTBLACK_EX + "\nTip: Send the copied text to your friend.")
                print(Fore.LIGHTBLACK_EX + "They can paste it directly into Gnizer.")
        except TmpFilesError as e:
            self._log(e,"critical")
            self.operation_text = Fore.RED + "Upload failed:" + Fore.LIGHTBLACK_EX + str(e)
        except Exception as e:
            self._log(e,"critical")
            self.operation_text = Fore.RED + f"Unexpected error during upload: {e}"

    def get_gnizer_temp_info(self):
        self._log("GET -> get_gnizer_temp_info", "info")

        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        temp_dir = temp_root / "Gnizer"
        
        if not temp_dir.exists():
            return temp_dir, 0
        
        total_size = sum(p.stat().st_size for p in temp_dir.rglob("*") if p.is_file())
        return temp_dir, total_size

    # -------------------------
    # region MAIN MENU
    # -------------------------
    def menu(self):
        self._log(" -> menu", "info")

        self.refresh_main_menu()
        self.cls()

        os.system(f"title Gnizer v{self.build_id}")

        print(Fore.WHITE + f" // (Version {self.build_id}) //\nby @LukieD4 on GitHub\n")
        print(Style.BRIGHT + self.MENU_TITLE)
        
        for key, (label, _) in self.menu_modes.items():
            print(Fore.LIGHTBLACK_EX + f"[{key}]. {label}")

        print("\n Enter a valid number from the list above ^")
        print(Fore.LIGHTBLACK_EX + " or 'c' to cancel current prompt when asked for input. ^\n")
        
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
        self._log("DETECT -> detect_installed_app", "info")
        for reg_path in registry_paths:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path):
                    return True, reg_path
            except (FileNotFoundError, OSError):
                continue
        return False, None

    def detect_mod_managers(self):
        self._log("DETECT -> detect_mod_managers", "info")

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

    def save_links_md_and_copy_to_clipboard(self, links: list[str], original_file: Path, type_of_upload:str):
        self._log("SAVE -> save_links_md_and_copy_to_clipboard", "info")

        if not links:
            return
        

        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        Gnizer_temp = temp_root / "Gnizer"
        Gnizer_temp.mkdir(parents=True, exist_ok=True)
        
        internal_name = original_file.name
        size_bytes = original_file.stat().st_size if original_file.exists() else 0
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        short_ts = datetime.now().strftime("%Y%m%d%H%M%S")
        
        md_content = f"""```# Gnizer (v{self.build_id})

A friend has shared their {type_of_upload} with you!

*Internal name: \"{internal_name}\"  
Size of {type_of_upload}: {size_bytes} bytes  
Date of {type_of_upload}: {timestamp}*

**Instructions**
- Highlight *this entire text*
    - CTRL + C
- Open Gnizer
    - Select: 'Load *DATA* from an ARCHIVE'
    - Select: 'Read from clipboard'
    - [DO NOT PASTE]
    - Press Enter

- Gnizer will automatically do the hard work :3

{self.DIVIDER}

## Download Links
"""
        md_content += "\n".join(links) + "\n```"
        
        # Save markdown file
        md_filename = f"Gnizer_shared_modlist_{short_ts}.md"
        md_path = Gnizer_temp / md_filename
        md_path.write_text(md_content, encoding="utf-8")
        
        # Copy to clipboard
        app = QApplication.instance() or QApplication(sys.argv)
        app.clipboard().setText(md_content)
        self.reveal_in_notepad(md_path)
        
        print(Fore.GREEN + "Share info saved & copied to clipboard!")
        print(Fore.WHITE + f"Markdown file: {md_path}")
        self.operation_text = f"Copied links to clipboard. Share it with your friends!\n{md_path}"

    def read_from_clipboard(self):
        self._log("READ -> read_from_clipboard", "info")

        app = QApplication.instance() or QApplication(sys.argv)
        text = app.clipboard().text()
        return text if text and text.strip() else None

    def reveal_in_explorer(self, path: Path):
        self._log("OPEN -> reveal_in_explorer", "info")
        try:
            os.startfile(path.parent if path.exists() else str(path))
        except Exception as e:
            self._log(e,"critical")
            print(Fore.RED + f"Failed to open File Explorer: {e}")
    
    def reveal_in_notepad(self, path: Path):
        self._log("OPEN -> reveal_in_notepad", "info")
        try:
            os.startfile(path)
        except Exception as e:
            self._log(e,"critical")
            print(Fore.RED + f"Failed to open Notepad: {e}")

    def clear_gnizer_temp(self):
        self._log("DELETE -> clear_gnizer_temp", "info")
        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        temp_dir = temp_root / "Gnizer"
        
        if not temp_dir.exists():
            return True
        
        try:
            shutil.rmtree(temp_dir)
            self.operation_text = "Temp cache cleared successfully."
            return True
        except Exception as e:
            self._log(e,"critical")
            self.operation_text = f"Failed to clear temp cache: {e}"
            return False

    def format_bytes(self, size: int):
        self._log("FORMAT -> format_bytes", "info")
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    # -------------------------
    # DRY INPUT HELPERS
    # -------------------------
    def query_input_with_retry(self, prompt: str, validator, *,
                               cancel_values=("c", "q"),
                               error_message: str = "Invalid input."):
        """
        Generic input prompt that retries until validator returns a non-None value
        or the user cancels by entering one of cancel_values.

        - prompt: text to show to the user (already includes color codes if desired)
        - validator: callable(str) -> any | None. Return parsed value on success, None on invalid.
        - cancel_values: tuple of strings that cancel the prompt
        - error_message: message to show when validator returns None
        """
        self._log("INPUT -> query_input_with_retry", "info")
        while True:
            raw = input(prompt).strip()
            if raw.lower() in cancel_values:
                self.operation_text = Fore.RED + "Cancelled."
                return None
            try:
                val = validator(raw)
            except Exception as e:
                # Validator raised; log and show error, then retry
                self._log(e, "warning")
                print(Fore.RED + f"{error_message} ({e})")
                continue
            if val is None:
                print(Fore.RED + error_message)
                continue
            return val

    def query_numeric_input(self, max_value: int, *,
                            prompt: str = None,
                            cancel_values=("c", "q")):
        """
        Query the user for a numeric selection between 1 and max_value inclusive.
        Returns the integer selection, or None if cancelled.
        """
        self._log("INPUT -> query_numeric_input", "info")
        if prompt is None:
            prompt = Fore.WHITE + "> "
        def validator(raw: str):
            if not raw.isdigit():
                return None
            n = int(raw)
            if not (1 <= n <= max_value):
                return None
            return n
        return self.query_input_with_retry(prompt, validator, cancel_values=cancel_values,
                                           error_message=f"Enter a number between 1 and {max_value}, or 'c' to cancel.")

    # Backwards-compatible alias for older code that used _get_numeric_input
    def _get_numeric_input(self, max_value: int):
        """Helper to get numeric input within range (keeps docstring for compatibility)."""
        return self.query_numeric_input(max_value)

    # -------------------------
    # region RUN
    # -------------------------
    def run(self):
        running = True
        while running:
            running = self.menu()


App().run()
