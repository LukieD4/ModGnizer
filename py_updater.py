import re
import requests
from pathlib import Path
import sys
import subprocess
from urllib.parse import urlparse
from py_imports import Fore

GITHUB_API = "https://api.github.com/repos/LukieD4/ModGnizer/releases/latest"

def clean_markdown(text: str) -> str:
    text = re.sub(r"<[^>]*>", "", text)

    lines = text.splitlines()
    cleaned = []
    for line in lines:
        line = line.replace("###", "").replace("##", "").replace("#", "")
        line = line.replace("**", "").replace("*", "")
        cleaned.append(line)
    return "\n".join(cleaned)


def get_local_version(version_file: Path) -> str:
    if not version_file.exists():
        return "0.0.0"
    return version_file.read_text().strip()

def get_latest_release():
    resp = requests.get(GITHUB_API, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    tag = data.get("tag_name", "") or data.get("name", "") or ""
    tag = tag.removeprefix("v")

    assets = data.get("assets", [])
    download_url = assets[0]["browser_download_url"] if assets else ""
    changelog = data.get("body", "No release notes provided.")

    # Extract asset filename (e.g., Gnizer-288.exe)
    asset_name = ""
    if download_url:
        asset_name = Path(urlparse(download_url).path).name

    return tag, download_url, changelog, asset_name

_VERSION_PARTS = 3

def _normalize_version(v: str):
    if not v:
        return (0, 0, 0)

    s = str(v).strip()

    m = re.search(r"(\d+(?:\.\d+)*)", s)
    if m:
        nums = [int(x) for x in m.group(1).split(".")]
    else:
        digs = re.findall(r"(\d+)", s)
        nums = [int(digs[-1])] if digs else [0]

    if len(nums) < _VERSION_PARTS:
        nums += [0] * (_VERSION_PARTS - len(nums))
    elif len(nums) > _VERSION_PARTS:
        nums = nums[:_VERSION_PARTS]

    return tuple(nums)

def is_latest_update(local: str, remote: str) -> bool:
    return _normalize_version(local) >= _normalize_version(remote)

def download_file(url: str, dest: Path):
    print(url,dest)
    r = requests.get(url, stream=True)
    r.raise_for_status()
    with dest.open("wb") as f:
        for chunk in r.iter_content(8192):
            if chunk:
                f.write(chunk)

def check_for_updates(version_file: Path, consent_callback=None):
    try:
        local = get_local_version(version_file)
        remote_tag, url, changelog, asset_name = get_latest_release()

        # Print changelog (generic text)
        print(Fore.WHITE + "--> Changelog: <--\n"+clean_markdown(changelog))

        print(
            Fore.WHITE
            + f"\nYour version: v{local}"
            + f"\nLatest version: v{remote_tag}"
            + f"\nIs latest? {is_latest_update(local, remote_tag)}"
        )

        if is_latest_update(local, remote_tag):
            input(
                Fore.YELLOW
                + "\nYou are currently up-to-date."
                + Fore.LIGHTBLACK_EX
                + "\n > Press ENTER to continue."
            )
            return False
        else:
            input(
                Fore.YELLOW
                + "\nA new update is available!"
                + Fore.LIGHTBLACK_EX
                + "\n > Press ENTER to continue."
            )

        if not consent_callback():
            return False

        if not url:
            print(Fore.RED + "Update check failed: release has no downloadable asset.")
            return False

        print(Fore.YELLOW + "\nDownloading update...")

        # IMPORTANT: use the ORIGINAL EXE, not the temp one
        exe_path = Path(sys.argv[0]).resolve()

        # If running from a .py file, pretend the exe exists for testing
        if exe_path.suffix == ".py":
            print(Fore.LIGHTBLACK_EX + "Running in test mode: simulating .exe path")
            exe_path = exe_path.with_suffix(".exe")

        # New EXE name from GitHub asset
        new_exe_path = exe_path.with_name(asset_name)
        tmp_new = new_exe_path.with_suffix(".exe")

        download_file(url, tmp_new)

        updater_bat = exe_path.with_suffix(".update.bat")

        updater_bat.write_text(
            "@echo off\n"
            "timeout /t 2 /nobreak >nul\n"
            f'move /Y "{tmp_new}" "{new_exe_path}"\n'
            f'del \"{exe_path}\" >nul 2>&1\n'
            f'start \"\" \"{new_exe_path}\"\n'
            "del \"%~f0\"\n",
            encoding="utf-8"
        )

        subprocess.Popen(
            ["cmd", "/c", str(updater_bat)],
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        while True:
            input(
                Fore.GREEN
                + "\n\nUpdate complete!"
                + Fore.WHITE
                + "\nYou can now close this app."
                + Fore.LIGHTBLACK_EX
                + "\nYou are free to delete the old version."
            )

    except Exception as e:
        print(Fore.RED + f"Update check failed: {e}")
        return False
