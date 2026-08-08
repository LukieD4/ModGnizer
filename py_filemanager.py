import shutil
from pathlib import Path

class FileOperation:

    def __init__(self):
        pass
    
    @staticmethod
    def delete_directory(path: Path):
        if path.exists() and path.is_dir():
            shutil.rmtree(path)
        else:
            raise NotADirectoryError(f"{path} was NOT a directory")

    @staticmethod
    def delete_directory_contents(path: Path):
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist")
        if not path.is_dir():
            raise NotADirectoryError(f"{path} is not a directory")

        for item in path.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    @staticmethod
    def delete_file(path: Path, suppress:bool = False):
        if not path.exists() and suppress: return

        if not path.exists() and not suppress:
            raise FileNotFoundError(f"{path} does not exist")
        
        if not path.is_file():
            raise IsADirectoryError(f"{path} is not a file")

        path.unlink()

    @staticmethod
    def replace_file(src: Path, dst: Path):
        if not src.exists():
            raise FileNotFoundError(f"Source file {src} does not exist")
        if not src.is_file():
            raise IsADirectoryError(f"Source {src} is not a file")

        shutil.copy2(src, dst)
