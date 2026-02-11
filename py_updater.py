import re
import requests
from pathlib import Path
import sys
import subprocess

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
    # version_file should be absolute path to buildId.version (or your chosen local source)
    if not version_file.exists():
        return "0.0.0"
    return version_file.read_text().strip()

def get_latest_release():
    resp = requests.get(GITHUB_API, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    tag = data.get("tag_name", "") or ""
    # defensive: try name if tag_name missing
    if not tag:
        tag = data.get("name", "") or ""

    # assets may be missing; guard against IndexError
    assets = data.get("assets", [])
    download_url = assets[0]["browser_download_url"] if assets else ""
    changelog = data.get("body", "No release notes provided.")

    return tag, download_url, changelog

_VERSION_PARTS = 3  # compare using 3-part versions (major.minor.patch)

def _normalize_version(v: str):
    """
    Return a tuple (major, minor, patch) of ints extracted from string v.
    Strategy:
      - Find first match of digits or digits.digits.digits: r'(\d+(?:\.\d+)*)'
      - If found parse that into ints.
      - If not, fall back to last single integer found anywhere.
      - If nothing found -> (0,0,0)
    """
    if not v:
        return (0,) * _VERSION_PARTS

    s = str(v).strip()
    # first try to find a dotted numeric substring like "1.2.3" or "15" or "1.2"
    m = re.search(r'(\d+(?:\.\d+)*)', s)
    if m:
        nums = [int(x) for x in m.group(1).split(".")]
    else:
        # find any integer anywhere and use the last one (helps "release-15" -> 15)
        digs = re.findall(r'(\d+)', s)
        if digs:
            nums = [int(digs[-1])]
        else:
            nums = [0]

    # pad/truncate to desired length
    if len(nums) < _VERSION_PARTS:
        nums = nums + [0] * (_VERSION_PARTS - len(nums))
    elif len(nums) > _VERSION_PARTS:
        nums = nums[:_VERSION_PARTS]

    return tuple(nums)

def is_newer(local: str, remote: str) -> bool:
    """
    Compare two version strings (local and remote). Returns True if remote > local.
    """
    return _normalize_version(remote) > _normalize_version(local)

def download_file(url: str, dest: Path):
    r = requests.get(url, stream=True)
    r.raise_for_status()
    with dest.open("wb") as f:
        for chunk in r.iter_content(8192):
            if chunk:
                f.write(chunk)

def check_for_updates(version_file: Path, consent_callback=None):
    try:
        local = get_local_version(version_file)
        remote_tag, url, changelog = get_latest_release()

        # debug print (optional) — helps see what GitHub actually returned
        # print(f"[DEBUG] local='{local}', remote_tag='{remote_tag}', url='{url}'")

        if not is_newer(local, remote_tag):
            return False

        print(clean_markdown(changelog))

        if consent_callback and not consent_callback():
            return False

        if not url:
            print("Update check failed: release has no downloadable asset.")
            return False

        print("\nDownloading update...")

        exe_path = Path(sys.executable)
        tmp_new = exe_path.with_suffix(".new.exe")

        download_file(url, tmp_new)

        # On Windows, move new file into place after a short delay
        subprocess.Popen([
            "cmd", "/c",
            f"timeout 2 && move /Y \"{tmp_new}\" \"{exe_path}\""
        ], shell=False)

        sys.exit(0)

    except Exception as e:
        print(f"Update check failed: {e}")
        return False