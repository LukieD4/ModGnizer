import re
import requests
from pathlib import Path
import sys
import subprocess
from urllib.parse import urlparse
import time

GITHUB_API = "https://api.github.com/repos/LukieD4/ModGnizer/releases/latest"

def clean_markdown(text: str) -> str:
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

    assets = data.get("assets", [])
    download_url = assets[0]["browser_download_url"] if assets else ""
    changelog = data.get("body", "No release notes provided.")

    # Extract asset filename (e.g., ModGnizer-288.exe)
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

def is_newer(local: str, remote: str) -> bool:
    return _normalize_version(remote) > _normalize_version(local)

def download_file(url: str, dest: Path):
    print(url,dest)
    input()
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

        print(clean_markdown(changelog))

        if is_newer(): return False

        if not consent_callback():
            return False

        if not url:
            print("Update check failed: release has no downloadable asset.")
            return False

        print("\nDownloading update...")

        # IMPORTANT: use the ORIGINAL EXE, not the temp one
        exe_path = Path(sys.argv[0]).resolve()

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

        sys.exit(0)

    except Exception as e:
        print(f"Update check failed: {e}")
        return False
