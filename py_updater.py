import os, re, requests, sys, subprocess
from pathlib import Path
from urllib.parse import urlparse
from py_imports import Fore
from typing import List, Tuple, Dict, Optional

_VERSION_PARTS = 3

# Base API endpoint for releases (used to list releases)
GITHUB_REPO_API = "https://api.github.com/repos/LukieD4/ModGnizer/releases"

# If you set GITHUB_TOKEN env var, we'll use it to increase GitHub rate limits
_GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

def _gh_headers() -> Dict[str, str]:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if _GITHUB_TOKEN:
        # either "token <token>" or "Bearer <token>" works; using token for compatibility
        headers["Authorization"] = f"token {_GITHUB_TOKEN}"
    return headers

def get_releases(n: int = 10, include_prereleases: bool = False) -> List[Dict]:
    """
    Fetch up to `n` recent releases from the repository, skipping drafts.
    Returns list of release dicts (in the same shape the GitHub API provides).
    """
    releases: List[Dict] = []
    params = {"per_page": n}
    try:
        resp = requests.get(GITHUB_REPO_API, params=params, headers=_gh_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Filter out drafts; optionally exclude prereleases unless requested
        for r in data:
            if r.get("draft"):
                continue
            if (not include_prereleases) and r.get("prerelease"):
                continue
            releases.append(r)
            if len(releases) >= n:
                break
    except Exception:
        # let caller handle printing/logging — return what we have (maybe empty)
        return releases
    return releases

def get_latest_release() -> Tuple[str, str, str, str]:
    """
    Backwards-compatible: return (tag, download_url, changelog, asset_name)
    based on the most recent (non-draft, non-prerelease) release.
    """
    rels = get_releases(1, include_prereleases=False)
    if not rels:
        return ("", "", "No release notes provided.", "")
    data = rels[0]
    tag = data.get("tag_name", "") or data.get("name", "") or ""
    tag = tag.removeprefix("v")
    assets = data.get("assets", []) or []
    download_url = assets[0].get("browser_download_url", "") if assets else ""
    changelog = data.get("body", "No release notes provided.") or "No release notes provided."
    asset_name = ""
    if download_url:
        asset_name = Path(urlparse(download_url).path).name
    return tag, download_url, changelog, asset_name


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
    print(url, dest)
    r = requests.get(url, stream=True, headers=_gh_headers())
    r.raise_for_status()
    with dest.open("wb") as f:
        for chunk in r.iter_content(8192):
            if chunk:
                f.write(chunk)

# NEW: helper to collect changelogs for the past N releases
def get_past_changelogs(n: int = 10, include_prereleases: bool = False) -> List[Dict[str, Optional[str]]]:
    """
    Returns a list of dicts with keys:
      - tag (string)
      - published_at (string, ISO timestamp) or None
      - changelog (raw body string)
      - cleaned_changelog (cleaned plain text)
      - asset_name (string or empty)
      - download_url (string or empty)
    """
    results = []
    rels = get_releases(n, include_prereleases=include_prereleases)
    for r in rels:
        tag = (r.get("tag_name") or r.get("name") or "").removeprefix("v")
        body = r.get("body") or ""
        pub = r.get("published_at")
        assets = r.get("assets", []) or []
        download_url = assets[0].get("browser_download_url", "") if assets else ""
        asset_name = Path(urlparse(download_url).path).name if download_url else ""
        results.append({
            "tag": tag,
            "published_at": pub,
            "changelog": body,
            "cleaned_changelog": clean_markdown(body),
            "asset_name": asset_name,
            "download_url": download_url,
        })
    return results

# Updated check_for_updates to optionally print past N changelogs first
def check_for_updates(version_file: Path, consent_callback=None, past_n: int = 10, include_prereleases: bool = False):
    try:
        # // Past CHANGELOGS //
        changelogs = get_past_changelogs(past_n, include_prereleases=include_prereleases)
        changelogs = list(reversed(changelogs))        

        if changelogs:
            print(Fore.WHITE + f"--> Showing changelogs for the most recent {len(changelogs)} releases: <--\n")
            for i, c in enumerate(changelogs, start=1):
                hdr = f"v{c['tag']}" + (f" — published {c['published_at']}" if c['published_at'] else "")
                print(Fore.YELLOW + hdr)
                print(Fore.WHITE + c['cleaned_changelog'] + "\n" + ("-" * 40) + "\n")
        else:
            print(Fore.LIGHTBLACK_EX + "No recent changelogs found.\n")



        # // Latest CHANGELOGS //
        local = get_local_version(version_file)
        remote_tag, url, changelog, asset_name = get_latest_release()

        # Print latest changelog (generic text)
        print("\n\n\n\n\n\n\n\n" + Fore.WHITE + "--> Latest release changelog: <--\n\n" + clean_markdown(changelog))

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

        if consent_callback is None or not consent_callback():
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