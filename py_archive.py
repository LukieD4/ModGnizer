from py_imports import *
import subprocess, shutil, zipfile

class ArchiveBundler:
    def __init__(self, source_folder: Path):
        self.source_folder = Path(source_folder)
        self.winrar_path = Path(r"C:\Program Files\WinRAR\WinRAR.exe")
        self.sevenz_path = Path(r"C:\Program Files\7-Zip\7z.exe")

    def has_winrar(self):
        return self.winrar_path.exists()

    def has_7z(self):
        return self.sevenz_path.exists()
    

    @staticmethod
    def extract_archive(archive_path: Path, password: str | None = None) -> Path | None:
        archive_path = Path(archive_path)
        if not archive_path.exists():
            return None

        # Output directory
        temp_root = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
        base = archive_path.stem
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        out_dir = temp_root / "ModGnizer" / "extracted_reassembled" / f"{base}_{ts}"

        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        ext = archive_path.suffix.lower()

        # ZIP → Python built‑in
        if ext == ".zip":
            with zipfile.ZipFile(archive_path, "r") as zf:
                if password:
                    zf.extractall(out_dir, pwd=password.encode())
                else:
                    zf.extractall(out_dir)
            return out_dir

        # External tools
        sevenz = Path(r"C:\Program Files\7-Zip\7z.exe")
        winrar = Path(r"C:\Program Files\WinRAR\WinRAR.exe")

        if ext == ".7z":

            if not sevenz.exists(): raise FileNotFoundError("7z.exe not found at expected path.")
            
            cmd = [str(sevenz), "x", str(archive_path), f"-o{out_dir}", "-y"]
            if password:
                cmd.append(f"-p{password}")
            subprocess.run(cmd, check=True)
            return out_dir


        if ext == ".rar":
            
            if not winrar.exists(): raise FileNotFoundError("WinRAR.exe not found at expected path.")
            
            cmd = [str(winrar), "x", "-y"]
            if password:
                cmd.append(f"-p{password}")
            cmd += [str(archive_path), str(out_dir)]
            subprocess.run(cmd, check=True)
            return out_dir

        return None

    
    
    def bundle_7z(self, output_file: Path, password: str = None):
        if not self.has_7z():
            raise FileNotFoundError("7z.exe not found at expected path.")

        source = str(self.source_folder / "*")

        cmd = [str(self.sevenz_path), "a", str(output_file), source]

        if password:
            cmd.insert(2, f"-p{password}")
            cmd.insert(3, "-mhe=on")  # encrypt file list

        subprocess.run(cmd, check=True)
        return output_file

    

    def bundle_rar(self, output_file: Path, password: str = None):
        if not self.has_winrar():
            raise FileNotFoundError("WinRAR.exe not found at expected path.")

        source = str(self.source_folder / "*")

        cmd = [
            str(self.winrar_path),
            "a",
            "-ep1",  # flatten paths
            str(output_file),
            source
        ]

        if password:
            cmd.insert(3, f"-hp{password}")  # full encryption (file list too)

        subprocess.run(cmd, check=True)
        return output_file




    def bundle_zip(self, output_file: Path):
        compression = zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(output_file, "w", compression=compression) as zipf:
            for root, _, files in os.walk(self.source_folder):
                for file in files:
                    full_path = Path(root) / file
                    arcname = full_path.relative_to(self.source_folder)
                    zipf.write(full_path, arcname)
        return output_file
