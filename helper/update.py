"""Keep the install current, so nobody has to visit GitHub to get a fix.

Run by the launcher before the app starts. That timing is the whole trick:
with nothing loaded yet, files can be replaced without fighting a running
program, and the app that starts a moment later is already the new one.

Nothing here is allowed to stop the app launching. No network, GitHub down, a
half-written zip - every one of them means "no update today", never "no app
today". What it does say, through its exit code, is whether the install it
leaves behind can be trusted: 1 when the app files are not all there, or an
update was attempted and did not fully apply - so the launcher can pause on
the message instead of closing over it. Offline with an intact install is 0.

An update that does not fully apply is not recorded as applied. A file that
could not be replaced - the one PresentMon holds open while an earlier
instance is still capturing is the usual one - leaves .version where it was,
so the next launch tries again rather than believing it is current.

The launcher itself is the exception. cmd.exe reads a .bat by file offset as it
goes, so overwriting one mid-run makes it execute whatever now happens to sit
at that offset. A new launcher is left in assets/ instead, and the launcher
swaps it in as the last thing it does.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import NamedTuple

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
    # HTTPException is not an OSError: a download cut off partway raises
    # IncompleteRead from read(), and without this it was the one failure
    # that escaped as a traceback rather than a quiet "no update today".
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
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


# --- versions -------------------------------------------------------------

#: The release number lives in StarCitizenHelper.py as `__version__ = '...'`,
#: because a zip install has no tags to read it from. Anchored to the start
#: of a line so a mention in a comment or docstring cannot match.
_VERSION_RE = re.compile(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", re.M)


def _parse_version(source: str) -> str | None:
    match = _VERSION_RE.search(source)
    return match.group(1) if match else None


def online_version(repo: str = REPO, branch: str = BRANCH) -> str | None:
    """The release number at the tip of the branch, or None if unreachable.

    Read from the raw file rather than the tags API: it is what the updater
    would actually install, and raw.githubusercontent.com has no per-hour
    request limit for an anonymous client the way api.github.com does. Its
    CDN can lag a push by a few minutes, which is fine for a freshness note.
    """
    raw = _get(f"https://raw.githubusercontent.com/{repo}/{branch}/StarCitizenHelper.py",
               SHA_TIMEOUT)
    if raw is None:
        return None
    return _parse_version(raw.decode("utf-8", "replace"))


def installed_version(root: Path = ROOT) -> str | None:
    """The release number of what is on disk, for the launcher's report."""
    try:
        return _parse_version((root / "StarCitizenHelper.py").read_text(encoding="utf-8"))
    except OSError:
        return None


def freshness(installed: str, online: str | None) -> str:
    """One of 'latest', 'update available', 'unable to verify'.

    Plain equality, not ordering: a checkout with a bumped, unpushed number
    reads as 'update available', which is odd but honest - it is not what is
    online. The third answer exists so that an unreachable GitHub is never
    mistaken for a check that has not happened yet.
    """
    if not online:
        return "unable to verify"
    return "latest" if installed == online else "update available"


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


class Applied(NamedTuple):
    """What apply_zip did: how many files it wrote, whether a new launcher
    is waiting in assets/, and the files it could not replace."""
    written: int
    staged: bool
    failed: list[str]


def _usable(root: Path) -> bool:
    """Whether the files the app cannot start without are all present."""
    return all((root / required).is_file() for required in _REQUIRED)


def apply_zip(data: bytes, root: Path = ROOT) -> Applied | None:
    """Write a downloaded archive over the install.

    Returns what was done, or None if the archive did not look like this
    project - in which case nothing has been touched. A file that could not
    be replaced is listed in `failed` rather than passed over: the caller
    decides what an incomplete update means, and it is not "done".
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
    failed: list[str] = []
    for name in names:
        relative = name[len(prefix):]
        # settings.json is the user's, and a zip entry climbing out of the
        # tree with .. would land anywhere it liked.
        if not relative or relative == SETTINGS or ".." in Path(relative).parts:
            continue
        # Recorded before the write, on purpose: the manifest is the list of
        # what is *shipped*, and a file that could not be replaced this time
        # is still there from last time - pruning it would be worse.
        installed.append(relative)
        try:
            body = archive.read(name)
        except (zipfile.BadZipFile, OSError, RuntimeError):
            failed.append(relative)
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

        temporary = target.with_name(target.name + ".part")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Write beside the target and rename, so a failure halfway leaves
            # the old file rather than a truncated new one.
            temporary.write_bytes(body)
            os.replace(temporary, target)
            written += 1
        except OSError:
            # Usually the target is held open - PresentMon.exe by an instance
            # still capturing. The rename is what fails, so the .part beside
            # it is complete and pointless; take it away again.
            failed.append(relative)
            try:
                temporary.unlink()
            except OSError:
                pass

    _prune(root, set(installed))
    try:
        manifest_file(root).parent.mkdir(parents=True, exist_ok=True)
        manifest_file(root).write_text(json.dumps(sorted(installed)),
                                       encoding="utf-8")
    except OSError:
        pass
    return Applied(written, staged, failed)


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

def update(root: Path = ROOT, repo: str = REPO, branch: str = BRANCH,
           ignore_settings: bool = False) -> tuple[str, bool]:
    """Bring the install up to date.

    Returns (one-line report, ok). `ok` is False only when an update was
    attempted and the install is not in the state it claims: the download
    or the archive failed, or a file could not be replaced. Nothing to do,
    or nothing reachable with an intact install, is True.

    `ignore_settings` is the launcher's /update switch: the user asked, so
    auto_update being off does not apply. A git checkout is still left
    alone - that is not a preference, it is somebody's working copy.
    """
    if is_git_checkout(root):
        return "", True
    if not ignore_settings and not auto_update_enabled(root):
        return "", True

    remote = latest_sha(repo, branch)
    if remote is None:
        return "Could not reach GitHub - carrying on with what is here.", _usable(root)
    if remote == installed_sha(root) and _usable(root):
        # Current, and really there. A .version that matches while the files
        # beneath it are missing is a half-applied install, and gets redone.
        return ("Already up to date." if ignore_settings else ""), True

    data = download(repo, branch)
    if data is None:
        return "An update is available but the download failed.", False

    result = apply_zip(data, root)
    if result is None:
        return "The downloaded update did not look right, so it was ignored.", False

    if result.failed:
        # Not stamped: .version still names what was there before, so the
        # next launch sees a difference and tries the whole thing again.
        return ("Updated %d file%s, but %d could not be written (first: %s). "
                "It will be retried next launch." % (
                    result.written, "" if result.written == 1 else "s",
                    len(result.failed), result.failed[0])), False

    try:
        version_file(root).parent.mkdir(parents=True, exist_ok=True)
        version_file(root).write_text(remote, encoding="utf-8")
    except OSError:
        pass
    return "Updated %d file%s to %s.%s" % (
        result.written, "" if result.written == 1 else "s", remote[:7],
        " The launcher updates itself on exit." if result.staged else ""), True


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    """Exit 0 when the install can be trusted, 1 when it cannot - see the
    module docstring. `--force` is the launcher's /update switch."""
    args = sys.argv[1:] if argv is None else argv
    message, ok = update(root, ignore_settings="--force" in args)
    if message:
        print("  " + message)
    return 0 if ok and _usable(root) else 1


if __name__ == "__main__":
    sys.exit(main())
