"""What the sheet renderer must not get wrong:

    python test_sheet.py

No framework and no dependencies, like the rest of this project. The
PowerShell script is never run: a fake runner stands in for it and writes
whatever PNG the test wants, so every case runs in milliseconds with no
PDF and no Windows PowerShell. One live render at the end is skipped when
either is missing.

The case it exists for: a render is a second of CPU and a file under
assets/, and the app asks for one on every zoom change. Getting the cache
wrong means either rendering the same size forever, or serving a file that
was half-written when the app closed.
"""

import os
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helper import sheet
from helper.sheet import MAX_WIDTH, PNG_SIGNATURE, SheetRenderer, png_width

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

def png_stub(width, height=100):
    """Enough of a PNG for png_width: signature and an IHDR chunk."""
    ihdr = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    return PNG_SIGNATURE + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\0\0\0\0"


class Fake:
    """Stands in for the script. Records the command, writes the PNG."""

    def __init__(self, code=0, body=None, offset=0):
        self.code = code
        self.body = body            # bytes to write instead of a real stub
        self.offset = offset        # width error to introduce
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        args = dict(zip(cmd, cmd[1:]))
        out = Path(args["-OutDir"]) / (args["-Names"] + ".png")
        width = int(args["-Width"]) + self.offset
        out.write_bytes(self.body if self.body is not None else png_stub(width))
        return self.code


class Log(list):
    def __call__(self, line):
        self.append(line)


class Setup:
    """A temp tree with a fake PDF, an empty script, a renderer and a log."""

    def __init__(self, fake=None, pdf=b"%PDF-1.4 fake"):
        self.root = Path(tempfile.mkdtemp())
        self.pdf = self.root / "sheet.pdf"
        self.script = self.root / "render_sheet.ps1"
        self.cache = self.root / "cache"
        if pdf is not None:
            self.pdf.write_bytes(pdf)
        self.script.write_text("")
        self.fake = fake or Fake()
        self.log = Log()
        self.renderer = SheetRenderer(self.pdf, self.cache, self.script,
                                      log=self.log, runner=self.fake)

    def files(self):
        return sorted(str(p.relative_to(self.cache)) for p in self.cache.rglob("*") if p.is_file())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)


# --- 1 ---------------------------------------------------------------------

print("\n1. a render is asked for once, then read back")


def _miss():
    with Setup() as s:
        path = s.renderer.render(1, 1000)
        assert path is not None and path.is_file(), s.log
        assert path.name == "page1-1000.png" and path.parent.parent == s.cache
        assert png_width(path) == 1000
        assert len(s.fake.calls) == 1


def _hit():
    with Setup() as s:
        first = s.renderer.render(1, 1000)
        second = s.renderer.render(1, 1000)
        assert first == second and len(s.fake.calls) == 1, "rendered twice"
        assert s.renderer.available(1, 1000) == first
        assert s.renderer.available(1, 999) is None
        assert s.renderer.available(2, 1000) is None


def _command():
    with Setup() as s:
        s.renderer.render(2, 1500)
        cmd = s.fake.calls[0]
        assert cmd[0] == sheet.POWERSHELL
        for flag in ("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"):
            assert flag in cmd, cmd
        args = dict(zip(cmd, cmd[1:]))
        assert args["-File"] == str(s.script) and args["-Pdf"] == str(s.pdf)
        assert args["-Width"] == "1500" and args["-Page"] == "2"
        assert args["-Names"] == "page2-1500.part", args["-Names"]
        assert Path(args["-OutDir"]).parent == s.cache


check("a miss runs the script and returns the file", _miss)
check("a hit does not run it again", _hit)
check("the script is called the way it expects", _command)


# --- 2 ---------------------------------------------------------------------

print("\n2. when the render goes wrong")


def _script_fails():
    with Setup(Fake(code=1)) as s:
        assert s.renderer.render(1, 1000) is None
        assert s.files() == [], "something was left behind: %s" % s.files()
        assert s.renderer.usable(), "one bad render should not switch it off"
        assert s.renderer.render(1, 1000) is None and len(s.fake.calls) == 2


def _cannot_start():
    with Setup(Fake(code=-1)) as s:
        assert s.renderer.render(1, 1000) is None
        assert not s.renderer.usable()
        assert s.renderer.render(1, 1200) is None
        assert len(s.fake.calls) == 1, "kept trying after it could not start"
        assert any("preview-only" in line for line in s.log), s.log


def _garbage():
    with Setup(Fake(body=b"this is not a png")) as s:
        assert s.renderer.render(1, 1000) is None
        assert s.files() == []
        assert any("wrong" in line for line in s.log), s.log


def _off_by_two():
    with Setup(Fake(offset=2)) as s:
        assert s.renderer.render(1, 1000) is not None, "2 px off should be accepted"
    with Setup(Fake(offset=10)) as s:
        assert s.renderer.render(1, 1000) is None, "10 px off should not"


def _no_pdf():
    with Setup(pdf=None) as s:
        assert not s.renderer.usable()
        assert s.renderer.render(1, 1000) is None
        assert s.fake.calls == []
        assert len([line for line in s.log if "missing" in line]) == 1, s.log
        s.renderer.usable()
        assert len([line for line in s.log if "missing" in line]) == 1, "said it twice"


def _bounds():
    with Setup() as s:
        assert s.renderer.render(1, MAX_WIDTH + 1) is None
        assert s.renderer.render(1, 100) is None
        assert s.renderer.render(0, 1000) is None
        assert s.fake.calls == []


def _cancelled():
    with Setup() as s:
        assert s.renderer.render(1, 1000, cancelled=lambda: True) is None
        assert s.fake.calls == []


check("a failing script leaves nothing and is retried next time", _script_fails)
check("a script that cannot start switches rendering off for the session", _cannot_start)
check("garbage output is thrown away", _garbage)
check("a pixel or two of DPI drift is fine; ten is not", _off_by_two)
check("no PDF: no render, said once", _no_pdf)
check("width and page outside the bounds are refused", _bounds)
check("a request already abandoned never starts", _cancelled)


# --- 3 ---------------------------------------------------------------------

print("\n3. the cache follows the PDF")


def _new_pdf():
    with Setup() as s:
        old = s.renderer.render(1, 1000)
        s.pdf.write_bytes(b"%PDF-1.4 a different sheet")
        os.utime(s.pdf, (time.time() + 5, time.time() + 5))
        new = s.renderer.render(1, 1000)
        assert new is not None and new.parent != old.parent, "same folder for a different PDF"
        assert not old.parent.exists(), "the old renders were kept"
        assert len(s.fake.calls) == 2


def _same_bytes_new_mtime():
    with Setup() as s:
        first = s.renderer.render(1, 1000)
        os.utime(s.pdf, (time.time() + 5, time.time() + 5))
        second = s.renderer.render(1, 1000)
        assert first == second and len(s.fake.calls) == 1, "a touched file threw the cache away"


def _stray_part():
    with Setup() as s:
        folder = s.renderer._directory()
        stray = folder / "page1-1000.part.png"
        stray.write_bytes(b"half")
        s2 = SheetRenderer(s.pdf, s.cache, s.script, log=s.log, runner=s.fake)
        s2.render(1, 1200)
        assert not stray.exists(), "a half-written file from last time survived"


check("a different PDF gets its own folder and the old one goes", _new_pdf)
check("the same PDF with a new timestamp keeps its renders", _same_bytes_new_mtime)
check("a half-written file from a previous session is cleared", _stray_part)


# --- 4 ---------------------------------------------------------------------

print("\n4. the real thing, if it is here")

ROOT = Path(__file__).resolve().parent
REAL_PDF = ROOT / "data" / "keybinds" / "sheet.pdf"
REAL_SCRIPT = ROOT / "data" / "keybinds" / "render_sheet.ps1"


def _live():
    if not (os.path.isfile(sheet.POWERSHELL) and REAL_PDF.is_file() and REAL_SCRIPT.is_file()):
        print("         (skipped: needs Windows PowerShell 5.1 and data/keybinds/sheet.pdf)")
        return
    cache = Path(tempfile.mkdtemp())
    try:
        log = Log()
        renderer = SheetRenderer(REAL_PDF, cache, REAL_SCRIPT, log=log)
        started = time.perf_counter()
        path = renderer.render(1, 400)
        assert path is not None, log
        assert png_width(path) == 400, png_width(path)
        print("         (rendered page 1 at 400 px in %.1f s)" % (time.perf_counter() - started))
    finally:
        shutil.rmtree(cache, ignore_errors=True)


check("page 1 renders at 400 px through PowerShell", _live)


print("\n%s  (%d passed, %d failed)"
      % ("FAILED" if FAILED else "SHEET VERIFIED", PASSED, FAILED))
sys.exit(1 if FAILED else 0)
