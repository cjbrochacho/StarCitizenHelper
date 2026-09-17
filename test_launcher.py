"""What the launcher must do on a machine that has nothing but it:

    python test_launcher.py

The one test here that needs the network, and about half a minute. It is not
part of the quick set - run it before a release, after main has been pushed,
because what it installs is whatever is on main right now.

The case it exists for: the README says the .bat is the only thing you ever
run, and the .bat used to run the updater out of a folder it had not fetched
yet. A lone launcher printed "No module named helper.update" to a window that
then closed, and nothing else happened. So this copies the launcher, alone,
into an empty folder and runs it as a double-click would - with a hook that
stops short of opening the window - and then looks at what is there.
"""

import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LAUNCHER = ROOT / "StarCitizenHelper.bat"

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


def run_launcher(folder, timeout=300):
    """The .bat as a double-click runs it, minus the window at the end.

    stdin is closed so a `pause` or `timeout` on an error path returns at
    once rather than waiting for a key that is never coming.
    """
    env = dict(os.environ, SCH_NO_LAUNCH="1")
    # By full path: CreateProcess runs a .bat through cmd on its own, and a
    # bare name relies on cmd searching the working directory, which not
    # every environment lets it do.
    return subprocess.run(
        [str(folder / "StarCitizenHelper.bat")], cwd=str(folder), env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        errors="replace", timeout=timeout)


def fresh_folder(parent):
    """An empty folder with only the launcher in it.

    Under the home directory, not %TEMP%: the launcher refuses to install
    into a temp folder, because that is where Explorer puts a .bat opened
    from inside a zip. That refusal is tested separately below.
    """
    folder = parent / ("sch-smoke-" + uuid.uuid4().hex[:8])
    folder.mkdir()
    shutil.copy2(LAUNCHER, folder / "StarCitizenHelper.bat")
    # The launcher makes a desktop shortcut once, and marks that it has. The
    # mark alone is enough to stop it - this test should not leave a shortcut.
    (folder / "assets").mkdir()
    (folder / "assets" / ".shortcut-made").write_text("made")
    return folder


print("\n1. a lone launcher installs the app")

scratch = fresh_folder(Path.home())
try:
    result = run_launcher(scratch)
    transcript = (result.stdout or "") + (result.stderr or "")

    def _exit_clean():
        assert result.returncode == 0, "exit %d\n%s" % (result.returncode, transcript)

    def _tree():
        for relative in ("StarCitizenHelper.py", "helper/__init__.py",
                         "helper/update.py", "vendor/PresentMon.exe"):
            assert (scratch / relative).is_file(), "%s missing\n%s" % (relative, transcript)

    def _stamped():
        version = (scratch / "assets" / ".version")
        assert version.is_file(), "assets/.version missing - helper.update did not run\n" + transcript
        sha = version.read_text().strip()
        assert re.fullmatch(r"[0-9a-f]{40}", sha), "not a commit: %r" % sha
        assert (scratch / "assets" / ".manifest").is_file()

    def _launcher_swapped():
        assert not (scratch / "assets" / "pending.bat").exists(), "pending.bat was not swapped in"
        body = (scratch / "StarCitizenHelper.bat").read_bytes()
        assert body.startswith(b"@echo off"), "the launcher is not a launcher any more"

    def _tidy():
        temp = Path(os.environ["TEMP"])
        leftovers = [p.name for p in temp.glob("StarCitizenHelper-boot*")]
        assert not leftovers, "left in %%TEMP%%: %s" % ", ".join(leftovers)
        parts = [str(p.relative_to(scratch)) for p in scratch.rglob("*.part")]
        assert not parts, "half-written files: %s" % ", ".join(parts)

    def _no_settings():
        assert not (scratch / "settings.json").exists(), "an install wrote settings.json"

    check("and exits cleanly", _exit_clean)
    check("the tree is there", _tree)
    check("the updater ran and stamped it", _stamped)
    check("the newer launcher was swapped in on exit", _launcher_swapped)
    check("nothing was left behind in TEMP or half-written", _tidy)
    check("and no settings file was invented", _no_settings)
finally:
    shutil.rmtree(scratch, ignore_errors=True)


print("\n2. a launcher run from inside the zip is refused, not installed into a temp folder")

# Explorer extracts a double-clicked file to %TEMP%\Temp1_<zipname>\...
archive_like = Path(os.environ["TEMP"]) / ("Temp1_smoke-%s.zip" % uuid.uuid4().hex[:6]) / "StarCitizenHelper-main"
archive_like.mkdir(parents=True)
try:
    shutil.copy2(LAUNCHER, archive_like / "StarCitizenHelper.bat")
    refused = run_launcher(archive_like, timeout=60)

    def _refused():
        assert refused.returncode != 0, "it went ahead:\n" + (refused.stdout or "")
        assert "temporary folder" in (refused.stdout or ""), refused.stdout

    def _nothing_installed():
        assert not (archive_like / "helper").exists(), "it installed into the temp folder anyway"

    check("it says so and stops", _refused)
    check("and fetched nothing", _nothing_installed)
finally:
    shutil.rmtree(archive_like.parent, ignore_errors=True)


print("\n%s  (%d passed, %d failed)"
      % ("FAILED" if FAILED else "LAUNCHER VERIFIED", PASSED, FAILED))
sys.exit(1 if FAILED else 0)
