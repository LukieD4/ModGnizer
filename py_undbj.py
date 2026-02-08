import sqlite3, json
from py_imports import *

class UnDBJ:
    def __init__(self, source_path):
        self.source_path = Path(source_path)

    # -------------------------
    # PUBLIC API
    # -------------------------

    def get_internal_profiles(self):
        """
        Returns a list of unified profile objects:
        {
            "path": Path(...),
            "folder": "...",
            "name": "...",
            "game_version": "...",
            "mod_loader": "...",
            "last_played": "...",
            "display": "...",
        }
        """

        if self.source_path.is_file() and self.source_path.suffix == ".db":
            profiles = self._get_modrinth_profiles()
            return self._format_profiles(profiles)
        
        if not self.source_path.is_file():
            profiles = self._get_curseforge_profiles()
            return self._format_profiles(profiles)

        print(f"Unsupported profile source: {self.source_path}")
        return []

    # -------------------------
    # MODRINTH (SQLite)
    # -------------------------

    def _get_modrinth_profiles(self):
        profiles = []

        try:
            conn = sqlite3.connect(self.source_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT path, name, game_version, mod_loader, last_played FROM profiles"
            )
            rows = cursor.fetchall()
            conn.close()

            for internal_name, name, game_version, mod_loader, last_played in rows:
                profiles.append({
                    "path": Path(os.environ["APPDATA"]) / "ModrinthApp" / "profiles" / internal_name,
                    "folder": internal_name,
                    "name": name,
                    "game_version": game_version,
                    "mod_loader": mod_loader,
                    "last_played": last_played,
                })

        except Exception as e:
            print(f"Error reading Modrinth DB: {e}")

        return profiles
    
    # -------------------------
    # CURSEFORGE
    # -------------------------

    def _get_curseforge_profiles(self):
        """
        Scan a CurseForge Instances directory for instance folders and extract
        profile info from minecraftinstance.json embedded inside each instance.

        Returns a list of dicts with the same keys used by _get_modrinth_profiles:
            {
                "path": Path(...),        # path to the instance's mods folder (best-effort)
                "folder": "...",          # instance folder name
                "name": "...",            # friendly instance name
                "game_version": "...",    # minecraft version string
                "mod_loader": "...",      # mod loader string (forge/fabric/vanilla/etc)
                "last_played": int|None,  # epoch seconds (best-effort)
            }
        """
        profiles = []

        try:
            base = Path(self.source_path)
            if not base.exists() or not base.is_dir():
                return profiles

            # Each instance is typically a directory under the Instances folder
            for inst in sorted(base.iterdir()):
                try:
                    if not inst.is_dir():
                        continue

                    # Candidate locations for minecraftinstance.json
                    candidates = [
                        inst / "minecraftinstance.json",
                        inst / "instance" / "minecraftinstance.json",
                        inst / "config" / "minecraftinstance.json",
                    ]

                    # Also search shallowly if not found in common spots
                    if not any(p.exists() for p in candidates):
                        found = None
                        for p in inst.rglob("minecraftinstance.json"):
                            # prefer files not buried too deep
                            if len(p.relative_to(inst).parts) <= 4:
                                found = p
                                break
                        if found:
                            candidates.insert(0, found)

                    json_path = next((p for p in candidates if p.exists()), None)
                    if not json_path:
                        # No manifest found; still add a minimal profile using folder name
                        profiles.append({
                            "path": inst / "mods",
                            "folder": inst.name,
                            "name": inst.name,
                            "game_version": "unknown",
                            "mod_loader": "unknown",
                            "last_played": int(inst.stat().st_mtime) if inst.exists() else None,
                        })
                        continue

                    with json_path.open("r", encoding="utf-8") as fh:
                        try:
                            data = json.load(fh)
                        except Exception:
                            # If JSON is malformed, fall back to folder-based profile
                            profiles.append({
                                "path": inst / "mods",
                                "folder": inst.name,
                                "name": inst.name,
                                "game_version": "unknown",
                                "mod_loader": "unknown",
                                "last_played": int(inst.stat().st_mtime) if inst.exists() else None,
                            })
                            continue

                    # Extract fields with multiple fallbacks to be robust across versions
                    name = (
                        data.get("name")
                        or data.get("instanceName")
                        or data.get("displayName")
                        or inst.name
                    )

                    # Common keys for Minecraft version vary; try several
                    game_version = (
                        data.get("minecraftVersion")
                        or data.get("version")
                        or data.get("mcVersion")
                        or data.get("minecraft_version")
                        or "unknown"
                    )

                    # Mod loader info can be stored under different keys
                    mod_loader = (
                        data.get("modLoader")
                        or data.get("modLoaderType")
                        or data.get("loader")
                        or data.get("modloader")
                        or data.get("mod_loader")
                        or "unknown"
                    )

                    # last_played: try explicit field, else use folder mtime as epoch seconds
                    last_played = None
                    for key in ("lastPlayed", "last_played", "lastLaunch", "lastLaunchTime"):
                        if key in data and data[key]:
                            try:
                                # some manifests store ms, some seconds — normalize heuristically
                                val = int(data[key])
                                if val > 10**12:  # milliseconds
                                    val = val // 1000
                                last_played = val
                                break
                            except Exception:
                                pass
                    if last_played is None:
                        try:
                            last_played = int(inst.stat().st_mtime)
                        except Exception:
                            last_played = None

                    # Best-effort mods path (typical CurseForge instance layout)
                    mods_path = inst / "mods"
                    if not mods_path.exists():
                        # some instances use 'minecraft/mods' or 'instance/mods'
                        alt = inst / "minecraft" / "mods"
                        if alt.exists():
                            mods_path = alt
                        else:
                            mods_path = inst / "mods"  # keep default even if missing

                    profiles.append({
                        "path": mods_path,
                        "folder": inst.name,
                        "name": name,
                        "game_version": str(game_version),
                        "mod_loader": str(mod_loader),
                        "last_played": last_played,
                    })

                except Exception:
                    # Skip problematic instance but continue scanning others
                    continue

        except Exception as e:
            print(f"Error reading Instances: {e}")

        return profiles


    # -------------------------
    # FORMATTER
    # -------------------------

    def _format_profiles(self, profiles):
        """
        Adds padding, converts timestamps, and builds display strings.
        """

        # Convert epoch → formatted date
        for p in profiles:
            if p["last_played"]:
                dt = datetime.fromtimestamp(p["last_played"])
                month = dt.strftime("%B")        # January, February, ...
                day = dt.strftime("%d")          # 01, 02, ...
                year = dt.strftime("%Y")         # 2025
                p["last_played"] = f"{day} {month} {year}"
            else:
                p["last_played"] = "never"



        # Determine padding widths
        max_name = max(len(p["name"]) for p in profiles)
        max_version = max(len(p["game_version"]) for p in profiles)
        max_loader = max(len(p["mod_loader"]) for p in profiles)

        # Build display strings
        for p in profiles:
            p["display"] = (
                f"{p['name']:<{max_name}}   "
                f"v{p['game_version']:<{max_version}}   "
                f"{p['mod_loader']:<{max_loader}}   "
                f"Last Played: {p['last_played']}"
            )

        return profiles
