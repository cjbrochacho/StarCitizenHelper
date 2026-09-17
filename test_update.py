"""What the updater must not get wrong:

    python test_update.py

No framework and no dependencies, like the rest of this project. Nothing here
touches the network: every fetch goes through helper.update._get, and that is
swapped for a fake keyed on the URL. Every install is a temporary directory.

The case it exists for: a first launch that has only the launcher, or an
update that got halfway. The updater used to stamp .version with the new
commit even when a file could not be replaced - PresentMon.exe held open by
an instance still capturing is the usual one - and from then on the install
called itself current and never tried again. It also returned 0 from every
path, so the launcher could not tell a finished install from a broken one.
"""

import contextlib
import http.client
import io
import json
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helper import update
from helper.update import (Applied, apply_zip, freshness, installed_version,
                           online_version, update as run_update)

PASSED = 0
FAILED = 0


def check(what, fn):
    global PASSED, FAILED
    try:
        fn()
        PASSED += 1
        print("  [PASS] %s" % what)
    except AssertionError as exc:
        FAILED += 1
        print("  [FAIL] %s\n         %s" % (what, exc))


# --- fixtures ---------------------------------------------------------------

PREFIX = "StarCitizenHelper-main/"
SHA = "a" * 40
OTHER_SHA = "b" * 40

#: A believable archive: the two files _REQUIRED insists on, the updater
#: itself, the launcher, and the one binary.
PAYLOAD = {
    "StarCitizenHelper.py": b"__version__ = '2026.09.16'\n",
    "helper/__init__.py": b"",
    "helper/update.py": b"# new\n",
    "StarCitizenHelper.bat": b"@echo off\r\nrem new\r\n",
    "vendor/PresentMon.exe": b"MZ",
}


def make_zip(files, prefix=PREFIX):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, body in files.items():
            archive.writestr(prefix + relative, body)
    return buf.getvalue()


class Routed:
    """A stand-in for _get: a substring of the URL picks the answer.

    Records every URL asked for, so a test can say "and it did not download"
    rather than only "and nothing changed".
    """

    def __init__(self, **routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, timeout, accept=None):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                return value
        return None

    def asked(self, key):
        return any(key in url for url in self.calls)


def github(sha=SHA, zip_=None, raw=None):
    """GitHub as the updater sees it: the sha endpoint, codeload, raw."""
    return Routed(api=sha.encode() if sha else None,
                  codeload=make_zip(PAYLOAD) if zip_ is None else zip_,
                  raw=raw)


@contextlib.contextmanager
def patched(getter):
    original = update._get
    update._get = getter
    try:
        yield getter
    finally:
        update._get = original


@contextlib.contextmanager
def install():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def populate(root, files=PAYLOAD, sha=None):
    """An install that already has the files, as after a successful update."""
    for relative, body in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    if sha:
        (root / "assets").mkdir(exist_ok=True)
        (root / "assets" / ".version").write_text(sha)


def version_of(root):
    try:
        return (root / "assets" / ".version").read_text()
    except OSError:
        return None


def lock(root, relative):
    """Make a target impossible to replace, on every OS: a directory where the
    file should go, so os.replace(file, directory) raises."""
    target = root / relative
    if target.is_file():
        target.unlink()
    target.mkdir(parents=True, exist_ok=True)


def exit_code(args, root):
    """main() as the launcher runs it, with its console line kept out of ours."""
    with contextlib.redirect_stdout(io.StringIO()):
        return update.main(args, root)


# --- 1 ---------------------------------------------------------------------

print("\n1. a fetch that fails is None, never a traceback")


class _Response:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        raise self.exc


@contextlib.contextmanager
def urlopen_raising(exc, on_open=False):
    original = urllib.request.urlopen

    def fake(request, timeout=None):
        if on_open:
            raise exc
        return _Response(exc)

    urllib.request.urlopen = fake
    try:
        yield
    finally:
        urllib.request.urlopen = original


def _incomplete_read():
    with urlopen_raising(http.client.IncompleteRead(b"half")):
        assert update._get("https://example/x", 1) is None


def _url_error():
    with urlopen_raising(urllib.error.URLError("no route"), on_open=True):
        assert update._get("https://example/x", 1) is None


check("a download cut off partway is swallowed", _incomplete_read)
check("and so is a network that is not there", _url_error)


# --- 2 ---------------------------------------------------------------------

print("\n2. a fresh install gets everything, and is stamped")


def _fresh():
    with install() as root, patched(github()) as gh:
        message, ok = run_update(root)
        assert ok, message
        for relative in PAYLOAD:
            if relative == "StarCitizenHelper.bat":
                continue
            assert (root / relative).read_bytes() == PAYLOAD[relative], relative
        # The launcher is staged for the .bat to swap in, never written live.
        assert not (root / "StarCitizenHelper.bat").exists()
        assert (root / "assets" / "pending.bat").read_bytes() == PAYLOAD["StarCitizenHelper.bat"]
        assert version_of(root) == SHA
        manifest = json.loads((root / "assets" / ".manifest").read_text())
        assert set(manifest) == set(PAYLOAD), manifest
        assert "Updated" in message and "launcher" in message, message
        assert gh.asked("codeload")


def _fresh_exit():
    with install() as root, patched(github()):
        assert exit_code([], root) == 0


check("every shipped file lands, the launcher via assets/pending.bat", _fresh)
check("and the launcher is told the install is good", _fresh_exit)


# --- 3 ---------------------------------------------------------------------

print("\n3. GitHub unreachable")


def _unreachable_fresh():
    with install() as root, patched(github(sha=None)):
        message, ok = run_update(root)
        assert "GitHub" in message, message
        assert not ok
        assert version_of(root) is None
        assert exit_code([], root) == 1


def _unreachable_intact():
    with install() as root, patched(github(sha=None)):
        populate(root, sha=OTHER_SHA)
        message, ok = run_update(root)
        assert "GitHub" in message, message
        assert ok, "an intact install offline is fine, not a failure"
        assert version_of(root) == OTHER_SHA
        assert exit_code([], root) == 0


check("with nothing installed, that is a failure the launcher hears about", _unreachable_fresh)
check("with an intact install, it is just no update today", _unreachable_intact)


# --- 4 ---------------------------------------------------------------------

print("\n4. the download itself fails")


def _download_fails():
    with install() as root, patched(Routed(api=SHA.encode())):
        message, ok = run_update(root)
        assert "download failed" in message, message
        assert not ok
        assert version_of(root) is None
        assert exit_code([], root) == 1


check("nothing is stamped and the launcher hears about it", _download_fails)


# --- 5 ---------------------------------------------------------------------

print("\n5. an archive that is not this project is refused untouched")


def _missing_required():
    files = dict(PAYLOAD)
    del files["helper/__init__.py"]
    with install() as root, patched(github(zip_=make_zip(files))):
        message, ok = run_update(root)
        assert "did not look right" in message, message
        assert not ok
        assert not (root / "StarCitizenHelper.py").exists()
        assert version_of(root) is None
        assert exit_code([], root) == 1


def _two_roots():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("one/StarCitizenHelper.py", b"")
        archive.writestr("two/helper/__init__.py", b"")
    with install() as root:
        assert apply_zip(buf.getvalue(), root) is None
        assert not list(root.iterdir())


def _not_a_zip():
    with install() as root:
        assert apply_zip(b"this is not a zip", root) is None


check("a required file missing", _missing_required)
check("two top-level folders", _two_roots)
check("not a zip at all", _not_a_zip)


# --- 6 ---------------------------------------------------------------------

print("\n6. a file that cannot be replaced is reported, and the install is not called current")


def _locked():
    with install() as root, patched(github()):
        populate(root, sha=OTHER_SHA)
        lock(root, "vendor/PresentMon.exe")
        result = apply_zip(make_zip(PAYLOAD), root)
        assert isinstance(result, Applied)
        assert result.failed == ["vendor/PresentMon.exe"], result.failed
        assert result.written == len(PAYLOAD) - 1 - 1, result   # minus the lock, minus the launcher (staged)
        assert (root / "helper" / "update.py").read_bytes() == b"# new\n"
        assert not (root / "vendor" / "PresentMon.exe.part").exists(), "a .part was left behind"


def _locked_not_stamped():
    with install() as root, patched(github()):
        populate(root, sha=OTHER_SHA)
        lock(root, "vendor/PresentMon.exe")
        message, ok = run_update(root)
        assert not ok
        assert "could not be written" in message and "PresentMon.exe" in message, message
        assert version_of(root) == OTHER_SHA, "stamped despite a failure"
        assert exit_code([], root) == 1


def _locked_then_retried():
    with install() as root, patched(github()):
        populate(root, sha=OTHER_SHA)
        lock(root, "vendor/PresentMon.exe")
        run_update(root)
        (root / "vendor" / "PresentMon.exe").rmdir()
        message, ok = run_update(root)
        assert ok, message
        assert version_of(root) == SHA
        assert (root / "vendor" / "PresentMon.exe").read_bytes() == b"MZ"


check("the file is named, the rest are written, no .part is left", _locked)
check(".version stays where it was and the launcher hears about it", _locked_not_stamped)
check("so the next launch tries again, and finishes the job", _locked_then_retried)


# --- 7 ---------------------------------------------------------------------

print("\n7. a git checkout is never touched")


def _git():
    with install() as root, patched(github()) as gh:
        (root / ".git").mkdir()
        assert run_update(root) == ("", True)
        assert run_update(root, ignore_settings=True) == ("", True)
        assert gh.calls == [], "it went to the network for a checkout"


check("not even when forced", _git)


# --- 8 ---------------------------------------------------------------------

print("\n8. auto_update off is a preference, and /update overrides it")


def _auto_off():
    with install() as root, patched(github()) as gh:
        populate(root, sha=OTHER_SHA)
        (root / "settings.json").write_text('{"auto_update": false}')
        assert run_update(root) == ("", True)
        assert gh.calls == []


def _forced():
    settings = '{"auto_update": false, "keepalive_key": "tab"}'
    files = dict(PAYLOAD)
    files["settings.json"] = b'{"planted": true}'     # a zip must never carry the user's settings
    with install() as root, patched(github(zip_=make_zip(files))):
        populate(root, sha=OTHER_SHA)
        (root / "settings.json").write_text(settings)
        message, ok = run_update(root, ignore_settings=True)
        assert ok, message
        assert version_of(root) == SHA
        assert (root / "settings.json").read_text() == settings, "settings.json was touched"


def _forced_flag():
    with install() as root, patched(github()):
        populate(root, sha=OTHER_SHA)
        (root / "settings.json").write_text('{"auto_update": false}')
        assert exit_code(["--force"], root) == 0
        assert version_of(root) == SHA


check("off means no network at all", _auto_off)
check("forced means updated, with settings.json left alone", _forced)
check("and --force is how the launcher says so", _forced_flag)


# --- 9 ---------------------------------------------------------------------

print("\n9. already current")


def _current():
    with install() as root, patched(github()) as gh:
        populate(root, sha=SHA)
        assert run_update(root) == ("", True)
        assert not gh.asked("codeload"), "downloaded although current"


def _current_forced_says_so():
    with install() as root, patched(github()):
        populate(root, sha=SHA)
        message, ok = run_update(root, ignore_settings=True)
        assert ok and "up to date" in message, message


def _current_but_hollow():
    with install() as root, patched(github()) as gh:
        populate(root, sha=SHA)
        (root / "StarCitizenHelper.py").unlink()
        message, ok = run_update(root)
        assert ok, message
        assert gh.asked("codeload"), "a .version with no files beneath it was believed"
        assert (root / "StarCitizenHelper.py").exists()


check("a matching .version means no download", _current)
check("but the user who asked is told", _current_forced_says_so)
check("and a matching .version over missing files is redone", _current_but_hollow)


# --- 10 --------------------------------------------------------------------

print("\n10. the launcher")


def _launcher_same():
    with install() as root:
        populate(root)
        result = apply_zip(make_zip(PAYLOAD), root)
        assert not result.staged
        assert not (root / "assets" / "pending.bat").exists()


def _launcher_differs():
    with install() as root:
        populate(root)
        old = b"@echo off\r\nrem old\r\n"
        (root / "StarCitizenHelper.bat").write_bytes(old)
        result = apply_zip(make_zip(PAYLOAD), root)
        assert result.staged
        assert (root / "StarCitizenHelper.bat").read_bytes() == old, "the running launcher was overwritten"
        assert (root / "assets" / "pending.bat").read_bytes() == PAYLOAD["StarCitizenHelper.bat"]


check("unchanged: nothing staged", _launcher_same)
check("changed: staged in assets/, the live one untouched", _launcher_differs)


# --- 11 --------------------------------------------------------------------

print("\n11. pruning")


def _prune():
    with install() as root:
        populate(root)
        (root / "helper" / "old.py").write_bytes(b"gone next time")
        (root / "settings.json").write_text("{}")
        (root / "assets").mkdir(exist_ok=True)
        (root / "assets" / "StarCitizenHelper.ico").write_bytes(b"icon")
        manifest = sorted(PAYLOAD) + ["helper/old.py", "settings.json", "assets/StarCitizenHelper.ico"]
        (root / "assets" / ".manifest").write_text(json.dumps(manifest))
        apply_zip(make_zip(PAYLOAD), root)
        assert not (root / "helper" / "old.py").exists(), "a dropped file survived"
        assert (root / "settings.json").exists(), "settings.json was pruned"
        assert (root / "assets" / "StarCitizenHelper.ico").exists(), "assets/ was pruned"


check("a file no longer shipped goes; settings and assets never do", _prune)


# --- 12 --------------------------------------------------------------------

print("\n12. freshness")


def _freshness():
    cases = [
        (("2026.09.16", "2026.09.16"), "latest"),
        (("2026.09.16", "2026.09.17"), "update available"),
        (("2026.09.16", "2026.09.16.1"), "update available"),
        (("3.1.0", "2026.09.16"), "update available"),
        (("2026.09.16", None), "unable to verify"),
        (("2026.09.16", ""), "unable to verify"),
    ]
    for args, expected in cases:
        got = freshness(*args)
        assert got == expected, "%r -> %r, expected %r" % (args, got, expected)


check("the three answers, and when each is given", _freshness)


# --- 13 --------------------------------------------------------------------

print("\n13. reading the version number")


def _online():
    with patched(github(raw=b"import os\n\n__version__ = '2026.09.16'\n")) as gh:
        assert online_version() == "2026.09.16"
        assert gh.calls == ["https://raw.githubusercontent.com/cjbrochacho/StarCitizenHelper/main/StarCitizenHelper.py"], gh.calls


def _online_shapes():
    with patched(github(raw=b'__version__ = "2026.09.16"\n')):
        assert online_version() == "2026.09.16", "double quotes"
    with patched(github(raw=b"# __version__ = '9.9.9'\n__version__ = '2026.09.16'\n")):
        assert online_version() == "2026.09.16", "a comment was matched"
    with patched(github(raw=b"    __version__ = '9.9.9'\n")):
        assert online_version() is None, "an indented one is not the module's"
    with patched(github(raw=b"nothing here\n")):
        assert online_version() is None
    with patched(github(raw=None)):
        assert online_version() is None


def _installed():
    with install() as root:
        assert installed_version(root) is None
        populate(root)
        assert installed_version(root) == "2026.09.16"


check("online: fetched from the raw file on main", _online)
check("only a top-level assignment counts", _online_shapes)
check("installed: read from the file on disk", _installed)


print("\n%s  (%d passed, %d failed)"
      % ("FAILED" if FAILED else "UPDATER VERIFIED", PASSED, FAILED))
sys.exit(1 if FAILED else 0)
