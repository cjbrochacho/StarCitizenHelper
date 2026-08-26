"""Keep the install current, so nobody has to visit GitHub to get a fix.

Run by the launcher before the app starts. That timing is the whole trick:
with nothing loaded yet, files can be replaced without fighting a running
program, and the app that starts a moment later is already the new one.

Nothing here is allowed to stop the app launching. No network, GitHub down, a
half-written zip - every one of them means "no update today", never "no app
today", so every failure path returns quietly.

The launcher itself is the exception. cmd.exe reads a .bat by file offset as it
goes, so overwriting one mid-run makes it execute whatever now happens to sit
at that offset. A new launcher is left in assets/ instead, and the launcher
swaps it in as the last thing it does.
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "cjbrochacho/StarCitizenHelper"
BRANCH = "main"

ROOT = Path(__file__).resolve().parent.parent

LAUNCHER = "StarCitizenHelper.bat"
SETTINGS = "settings.json"

#: Long enough for a slow connection, short enough that a dead network costs a
#: couple of seconds at launch rather than a hang.
SHA_TIMEOUT = 6
ZIP_TIMEOUT = 30

#: GitHub rejects requests with no user agent.
_HEADERS = {"User-Agent": "StarCitizenHelper-Updater"}

#: Proof the archive is this project, checked before anything is overwritten.
_REQUIRED = ("StarCitizenHelper.py", "helper/__init__.py")


# Everything the updater keeps for itself lives in assets/, which is
# git-ignored - so an update can never overwrite the record of itself.
def _assets(root: Path) -> Path:
    return root / "assets"


def version_file(root: Path) -> Path:
    """Which commit is installed."""
    return _assets(root) / ".version"


def manifest_file(root: Path) -> Path:
    """What the last update put on disk, so dropped files can be removed."""
    return _assets(root) / ".manifest"


def pending_launcher(root: Path) -> Path:
    """A launcher that cannot be written while it is running."""
    return _assets(root) / "pending.bat"


# --- fetching -------------------------------------------------------------

def _get(url: str, timeout: int, accept: str | None = None) -> bytes | None:
    headers = dict(_HEADERS)
    if accept:
        headers["Accept"] = accept
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None


def latest_sha(repo: str = REPO, branch: str = BRANCH) -> str | None:
    """The newest commit on the branch, as a bare SHA.

    Asking for the sha media type returns forty characters rather than the
    whole commit as JSON, which is a lot of response to parse for one field.
    """
    raw = _get(f"https://api.github.com/repos/{repo}/commits/{branch}",
               SHA_TIMEOUT, accept="application/vnd.github.sha")
    if raw is None:
        return None
    sha = raw.decode("ascii", "replace").strip()
    if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
        return sha
    return None


def download(repo: str = REPO, branch: str = BRANCH) -> bytes | None:
    return _get(f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}",
                ZIP_TIMEOUT)


# --- local state ----------------------------------------------------------

def installed_sha(root: Path = ROOT) -> str:
    try:
        return version_file(root).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _manifest(root: Path) -> set[str]:
    try:
        return set(json.loads(manifest_file(root).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def auto_update_enabled(root: Path = ROOT) -> bool:
    """Off only if the settings file says so - absent or broken means on."""
    try:
        with open(root / SETTINGS, encoding="utf-8") as handle:
            return bool(json.load(handle).get("auto_update", True))
    except (OSError, ValueError):
        return True


def is_git_checkout(root: Path = ROOT) -> bool:
    """Whether this install is somebody's working copy rather than a download.

    Overwriting one with the tip of main would throw away whatever they had
    not committed yet, which is a rude way to treat the person writing the
    thing. Git is already handling updates there.
    """
    return (root / ".git").exists()


# --- applying -------------------------------------------------------------

def _payload(archive: zipfile.ZipFile) -> tuple[str, list[str]]:
    """The archive's single top-level folder, and the files beneath it."""
    names = [n for n in archive.namelist() if not n.endswith("/")]
    if not names:
        return "", []
    prefix = names[0].split("/", 1)[0] + "/"
    if not all(n.startswith(prefix) for n in names):
        return "", []
    return prefix, names


def apply_zip(data: bytes, root: Path = ROOT) -> tuple[int, bool] | None:
    """Write a downloaded archive over the install.

    Returns (files written, launcher staged), or None if the archive did not
    look like this project - in which case nothing has been touched.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        return None

    prefix, names = _payload(archive)
    if not prefix:
        return None
    relatives = {n[len(prefix):] for n in names}
    if not all(required in relatives for required in _REQUIRED):
        return None

    written, staged = 0, False
    installed: list[str] = []
    for name in names:
        relative = name[len(prefix):]
        # settings.json is the user's, and a zip entry climbing out of the
        # tree with .. would land anywhere it liked.
        if not relative or relative == SETTINGS or ".." in Path(relative).parts:
            continue
        installed.append(relative)
        try:
            body = archive.read(name)
        except (zipfile.BadZipFile, OSError, RuntimeError):
            continue

        if relative == LAUNCHER:
            try:
                if (root / LAUNCHER).read_bytes() == body:
                    continue                      # unchanged, nothing to swap
            except OSError:
                pass
            target, staged = pending_launcher(root), True
        else:
            target = root / relative

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Write beside the target and rename, so a failure halfway leaves
            # the old file rather than a truncated new one.
            temporary = target.with_name(target.name + ".part")
            temporary.write_bytes(body)
            os.replace(temporary, target)
            written += 1
        except OSError:
            continue

    _prune(root, set(installed))
    try:
        manifest_file(root).parent.mkdir(parents=True, exist_ok=True)
        manifest_file(root).write_text(json.dumps(sorted(installed)),
                                       encoding="utf-8")
    except OSError:
        pass
    return written, staged


def _prune(root: Path, keep: set[str]) -> None:
    """Delete files a previous update installed that are no longer shipped.

    Only ever touches paths this updater wrote itself, so nothing of the
    user's - settings, icon, shortcut marker - is at risk.
    """
    for relative in _manifest(root) - keep:
        if relative in (SETTINGS, LAUNCHER) or relative.startswith("assets/"):
            continue
        if ".." in Path(relative).parts:
            continue
        try:
            (root / relative).unlink()
        except OSError:
            pass


# --- the whole job --------------------------------------------------------

def update(root: Path = ROOT, repo: str = REPO, branch: str = BRANCH) -> str:
    """Bring the install up to date. The return value is a one-line report."""
    if is_git_checkout(root):
        return ""
    if not auto_update_enabled(root):
        return ""

    remote = latest_sha(repo, branch)
    if remote is None:
        return "Could not reach GitHub - carrying on with what is here."
    if remote == installed_sha(root):
        return ""                                 # current; say nothing

    data = download(repo, branch)
    if data is None:
        return "An update is available but the download failed."

    result = apply_zip(data, root)
    if result is None:
        return "The downloaded update did not look right, so it was ignored."

    written, staged = result
    try:
        version_file(root).parent.mkdir(parents=True, exist_ok=True)
        version_file(root).write_text(remote, encoding="utf-8")
    except OSError:
        pass
    return "Updated %d file%s to %s.%s" % (
        written, "" if written == 1 else "s", remote[:7],
        " The launcher updates itself on exit." if staged else "")


def main() -> int:
    message = update()
    if message:
        print("  " + message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
