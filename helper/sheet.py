"""The key binding sheet at any size, rendered from its PDF on demand.

Tk can scale a photo only by dropping or repeating pixels, and at anything
under 100% the sheet's small text falls apart. The sheet is a vector PDF,
though, and Windows has had a PDF renderer of its own since 10 - the one
Edge and the Photos app use - reachable from Windows PowerShell. So a zoom
level is rendered at exactly that width, by data/keybinds/render_sheet.ps1,
in a child process with no window, and kept under assets/ for next time.
The first time a size is asked for costs a second or two; after that it is
a file read.

Nothing here touches Tk. The app hands the path back to the main thread
and loads it there.

The cache is keyed on the PDF's contents, not its timestamp: the updater
and git both rewrite files with a fresh mtime even when not a byte changed,
and throwing away every render on every update would be a poor trade for a
2 ms hash.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import threading
from pathlib import Path

#: The shipped PNG is 2000 wide; 250% of it is 5000, and a 5000x3863 RGBA
#: photo is 77 MB. 300% would be 111 MB, and for a moment there are two.
MIN_WIDTH = 400
MAX_WIDTH = 5000

TIMEOUT_S = 60
CREATE_NO_WINDOW = 0x08000000

#: Renders kept per page. Every distinct width is a file of a megabyte or
#: two, and a slider offers two hundred of them; the newest few are the ones
#: anybody comes back to.
KEEP_PER_PAGE = 8

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: Named in full: a `powershell` on PATH could be a shim to PowerShell 7,
#: which cannot load the WinRT PDF types. The 5.1 that ships with Windows
#: is always here.
POWERSHELL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
if not os.path.isfile(POWERSHELL):
    POWERSHELL = "powershell"


def _run(cmd: list[str], log) -> int:
    """The default runner: the script, quietly. -1 means "could not run at all"."""
    try:
        result = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            errors="replace", timeout=TIMEOUT_S, creationflags=CREATE_NO_WINDOW)
    except OSError as exc:
        log("Sheet render could not start: %s" % exc)
        return -1
    except subprocess.SubprocessError as exc:
        log("Sheet render gave up: %s" % exc)
        return -2
    if result.returncode != 0:
        lines = [line for line in (result.stderr or "").splitlines() if line.strip()]
        log("Sheet render failed: %s" % (lines[-1] if lines else "exit %d" % result.returncode))
    return result.returncode


def png_width(path: Path) -> int | None:
    """The width in a PNG's header, or None if it is not a PNG."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != PNG_SIGNATURE:
        return None
    return struct.unpack(">I", head[16:20])[0]


class SheetRenderer:
    """Renders one page of the PDF at a width, once, and remembers where."""

    def __init__(self, pdf, cache_dir, script, log=lambda line: None, runner=None) -> None:
        self.pdf = Path(pdf)
        self.cache_dir = Path(cache_dir)
        self.script = Path(script)
        self.log = log
        self._runner = runner or (lambda cmd: _run(cmd, log))
        self._lock = threading.Lock()
        self._signature = ""
        self._stat = None
        self._disabled = ""
        self._said_missing = False

    # -- state --------------------------------------------------------------

    def usable(self) -> bool:
        """Whether asking for a render is worth anything right now."""
        if self._disabled:
            return False
        if not (self.pdf.is_file() and self.script.is_file()):
            if not self._said_missing:
                self._said_missing = True
                self.log("Sheet zoom is preview-only: %s is missing." % (
                    self.pdf.name if not self.pdf.is_file() else self.script.name))
            return False
        return True

    def _directory(self) -> Path | None:
        """The cache folder for this exact PDF, made and tidied on first use."""
        try:
            stat = self.pdf.stat()
        except OSError:
            return None
        current = (stat.st_size, stat.st_mtime_ns)
        if current != self._stat:
            with open(self.pdf, "rb") as handle:
                self._signature = hashlib.sha1(handle.read()).hexdigest()[:12]
            self._stat = current
            self._tidy()
        return self.cache_dir / self._signature

    def _tidy(self) -> None:
        folder = self.cache_dir / self._signature
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for sibling in self.cache_dir.iterdir():
                if sibling.is_dir() and sibling.name != self._signature:
                    shutil.rmtree(sibling, ignore_errors=True)
            for stray in folder.glob("*.part.png"):
                stray.unlink()
        except OSError:
            pass

    @staticmethod
    def _name(page: int, width: int) -> str:
        return "page%d-%d" % (page, width)

    # -- the work -----------------------------------------------------------

    def available(self, page: int, width: int) -> Path | None:
        """The render if it is already on disk; never starts one."""
        folder = self._directory()
        if folder is None:
            return None
        path = folder / (self._name(page, width) + ".png")
        try:
            return path if path.stat().st_size > 0 else None
        except OSError:
            return None

    def render(self, page: int, width: int, cancelled=None) -> Path | None:
        """The page at that width, rendering it if it is not cached.

        `cancelled()` is asked just before the child process starts: a zoom
        the user has already moved on from is not worth a second of CPU.
        """
        if page < 1 or not MIN_WIDTH <= width <= MAX_WIDTH or not self.usable():
            return None
        hit = self.available(page, width)
        if hit is not None:
            return hit
        with self._lock:
            hit = self.available(page, width)           # another thread may have just done it
            if hit is not None:
                return hit
            if cancelled is not None and cancelled():
                return None
            folder = self._directory()
            if folder is None:
                return None
            name = self._name(page, width)
            partial = folder / (name + ".part.png")     # the script appends .png
            final = folder / (name + ".png")
            code = self._runner([
                POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(self.script), "-Pdf", str(self.pdf), "-OutDir", str(folder),
                "-Width", str(width), "-Page", str(page), "-Names", name + ".part",
            ])
            if code == -1:
                # Could not even start, or Windows PowerShell 5.1 is not what
                # answered. That will not change this session; stop asking.
                self._disabled = "renderer unavailable"
                self.log("Sheet zoom is preview-only from here on.")
            if code != 0:
                self._discard(partial)
                return None
            got = png_width(partial)
            # The script corrects for display DPI by re-rendering, and that
            # can land a pixel or two off the request.
            if got is None or abs(got - width) > 2:
                self.log("Sheet render came back wrong (%s px for %d); ignored." % (got, width))
                self._discard(partial)
                return None
            try:
                os.replace(partial, final)
            except OSError as exc:
                self.log("Sheet render could not be kept: %s" % exc)
                self._discard(partial)
                return None
            self._prune(folder, page, keep=final)
            return final

    def _prune(self, folder: Path, page: int, keep: Path) -> None:
        """Drop this page's oldest renders beyond KEEP_PER_PAGE; never `keep`."""
        try:
            renders = [p for p in folder.glob("page%d-*.png" % page)
                       if p != keep and not p.name.endswith(".part.png")]
            renders.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
        for old in renders[KEEP_PER_PAGE - 1:]:
            self._discard(old)

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass
