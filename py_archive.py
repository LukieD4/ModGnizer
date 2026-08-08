from py_imports import *
from pathlib import Path
from datetime import datetime
import subprocess, shutil, zipfile, os, hashlib

winrar_path = Path(r"C:\Program Files\WinRAR\WinRAR.exe")
sevenz_path = Path(r"C:\Program Files\7-Zip\7z.exe")


class ArchiveBundler:
    def __init__(self):
        pass

    def has_winrar(self):
        return winrar_path.exists()

    def has_7z(self):
        return sevenz_path.exists()
    
    @staticmethod
    def generate_MD5_hashes(output_path: Path | str) -> dict[str, str] | None:
        """
        Uses a standard folder (not an archive file) to compute MD5 hashes for all relative files,
        and returns a mapping of relative paths → MD5 hex digest.

        Accepts either:
        - Path("folder")
        - "folder/*"
        """

        if not output_path.exists() or not output_path.is_dir():
            return None

        md5_map = {}

        for root, _, files in os.walk(output_path):
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(output_path)

                # Compute MD5
                hasher = hashlib.md5()
                with open(full_path, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        hasher.update(chunk)

                md5_map[str(rel_path)] = hasher.hexdigest()

        return md5_map



    @staticmethod
    def extract_archive(archive_path: Path, password: str | None = None) -> Path | None:
        archive_path = Path(archive_path)
        if not archive_path.exists():
            return None

        # Output directory
        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        base = archive_path.stem
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        out_dir = temp_root / "Gnizer" / "extracted_reassembled" / f"{base}_{ts}"

        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        ext = archive_path.name.lower()

        # ZIP → Python built‑in
        if "zip" in ext:
            with zipfile.ZipFile(archive_path, "r") as zf:
                if password:
                    zf.extractall(out_dir, pwd=password.encode())
                else:
                    zf.extractall(out_dir)
            return out_dir

        # External tools
        sevenz = Path(r"C:\Program Files\7-Zip\7z.exe")
        winrar = Path(r"C:\Program Files\WinRAR\WinRAR.exe")

        if "7z" in ext:

            if not sevenz.exists(): raise FileNotFoundError("7z.exe not found at expected path.")
            
            cmd = [str(sevenz), "x", str(archive_path), f"-o{out_dir}", "-y"]
            if password:
                cmd.append(f"-p{password}")
            subprocess.run(cmd, check=True)
            return out_dir


        if "rar" in ext:
            
            if not winrar.exists(): raise FileNotFoundError("WinRAR.exe not found at expected path.")
            
            cmd = [str(winrar), "x", "-y"]
            if password:
                cmd.append(f"-p{password}")
            cmd += [str(archive_path), str(out_dir)]
            subprocess.run(cmd, check=True)
            return out_dir

        return None
    

    def bundle_content(self,
                       data_directory_path:Path,
                       output_target_file: Path,
                       format:str,
                       password:str = None,
                       mod_compat_path: Path = None,
                       skip_hash_generation: bool = False
                       ):
        
        source = str(data_directory_path / "*") # // Apply wildcard for recursive archive referencing

        match format:

            case "zip":
                with zipfile.ZipFile(
                    output_target_file,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=9  # max compression level for ZIP
                ) as zipf:
                    for root, _, files in os.walk(self.source_folder):
                        for file in files:
                            full_path = Path(root) / file
                            arcname = full_path.relative_to(self.source_folder)
                            zipf.write(full_path, arcname)

            case "7z":
                cmd = [
                    str(sevenz_path),
                    "a",
                    "-t7z",          # use 7z format (best compression)
                    "-r",            # recurse into subdirectories
                    "-m0=lzma2",     # LZMA2 algorithm
                    "-mx=9",         # ultra compression
                    "-mmt=on",       # multithreading
                    "-ms=on",        # solid archive
                    "-mfb=273",      # max fast bytes
                    "-md=64m",     # max dictionary size (1GB)
                    str(output_target_file),
                    source
                ]
                if password:
                    cmd.insert(3, f"-p{password}")  # set password
                    cmd.insert(4, "-mhe=on")        # encrypt headers
                

            case "rar":
                cmd = [
                    str(winrar_path),
                    "a",
                    "-r",          # recurse into subdirectories
                    "-ep1",        # strip base path (prevents absolute paths)
                    "-ma5",        # use RAR5 format (better compression)
                    "-m5",         # best compression level
                    "-s",          # solid archive (major compression boost)
                    "-md64",     # max dictionary size (1GB)
                    str(output_target_file),
                    source
                ]
                if password:
                    # full encryption + hide file list
                    cmd.insert(3, f"-hp{password}")
        
        # Bundle
        subprocess.run(cmd, check=True)

        # Generate hashlist
        hash_strings = None
        if not skip_hash_generation:
            hash_strings = self.generate_MD5_hashes(data_directory_path)

        return output_target_file, hash_strings

        

    
    # def bundle_7z(self, mod_profile_path:Path, output_target_file: Path, password:str = None, mod_compat_path: Path = None):
    #     if not self.has_7z():
    #         raise FileNotFoundError("7z.exe not found at expected path.")

    #     source = str(self.source_folder / "*")

    #     cmd = [
    #         str(self.sevenz_path),
    #         "a",
    #         "-t7z",          # use 7z format (best compression)
    #         "-r",            # recurse into subdirectories
    #         "-m0=lzma2",     # LZMA2 algorithm
    #         "-mx=9",         # ultra compression
    #         "-mmt=on",       # multithreading
    #         "-ms=on",        # solid archive
    #         "-mfb=273",      # max fast bytes
    #         "-md=64m",     # max dictionary size (1GB)
    #         str(output_target_file),
    #         source
    #     ]

    #     if password:
    #         cmd.insert(3, f"-p{password}")  # set password
    #         cmd.insert(4, "-mhe=on")        # encrypt headers

    #     # Bundle
    #     subprocess.run(cmd, check=True)

    #     # Generate hashlist
    #     hashlist = self.generate_MD5_hashes(source)

    #     return output_target_file, hashlist



    

    # def bundle_rar(self, mod_profile_path:Path, output_target_file: Path, password:str = None, mod_compat_path: Path = None):
    #     if not self.has_winrar():
    #         raise FileNotFoundError("WinRAR.exe not found at expected path.")

    #     source = str(self.source_folder / "*")

    #     cmd = [
    #         str(self.winrar_path),
    #         "a",
    #         "-r",          # recurse into subdirectories
    #         "-ep1",        # strip base path (prevents absolute paths)
    #         "-ma5",        # use RAR5 format (better compression)
    #         "-m5",         # best compression level
    #         "-s",          # solid archive (major compression boost)
    #         "-md64",     # max dictionary size (1GB)
    #         str(output_file),
    #         source
    #     ]

    #     if password:
    #         # full encryption + hide file list
    #         cmd.insert(3, f"-hp{password}")

    #     # Bundle
    #     subprocess.run(cmd, check=True)

    #     # Generate hashlist
    #     hashlist = self.generate_MD5_hashes(source)

    #     return output_file, hashlist







    # def bundle_zip(self, mod_profile_path:Path, output_target_file: Path, password:str = None, mod_compat_path: Path = None):
    #     # ZIP_DEFLATED = standard DEFLATE compression
    #     # compresslevel=9 = maximum compression for DEFLATE
    #     compression = zipfile.ZIP_DEFLATED

    #     with zipfile.ZipFile(
    #         output_target_file,
    #         "w",
    #         compression=compression,
    #         compresslevel=9  # max compression level for ZIP
    #     ) as zipf:
    #         for root, _, files in os.walk(self.source_folder):
    #             for file in files:
    #                 full_path = Path(root) / file
    #                 arcname = full_path.relative_to(self.source_folder)
    #                 zipf.write(full_path, arcname)

    #     # Generate hashlist
    #     hashlist = self.generate_MD5_hashes(self.source_folder)

    #     return output_target_file, hashlist


archiveBundler = ArchiveBundler() # Summon singleton