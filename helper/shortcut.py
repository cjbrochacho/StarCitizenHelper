"""Create a desktop shortcut for Star Citizen Helper.

Run it once:   python sc_shortcut.py

A .lnk file stores absolute paths - to the target, its working directory and
its icon - so one cannot be shipped in the repository: it would point at
whatever machine built it. This builds the shortcut fresh against wherever the
project actually lives, so it is correct on every machine.

The icon is drawn here rather than committed as a binary, so there is nothing
to keep in sync and no image library to install.
"""

import struct
import subprocess
import sys
from pathlib import Path

from .brand import render_mark

# The project root is one level up now that this lives in the package.
ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "StarCitizenHelper.bat"
ICON = ROOT / "assets" / "StarCitizenHelper.ico"
SHORTCUT_NAME = "Star Citizen Helper.lnk"

#: Sizes Windows picks between for the title bar, taskbar and Explorer.
ICON_SIZES = (16, 32, 48, 64, 128, 256)


def write_icon(path=ICON):
    """Write a multi-size .ico. Each entry is a PNG, which Vista onwards reads."""
    images = [render_mark(size) for size in ICON_SIZES]
    offset = 6 + 16 * len(images)
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for size, data in zip(ICON_SIZES, images):
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                           len(data), offset)
        offset += len(data)
    for data in images:
        out += data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


def create_shortcut(icon=None, minimised=True):
    """Put the shortcut on this user's desktop, wherever that actually is."""
    if not LAUNCHER.exists():
        raise FileNotFoundError(LAUNCHER)

    icon_line = f"$s.IconLocation = '{icon},0'" if icon else ""
    script = f"""
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop '{SHORTCUT_NAME}'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
$s.TargetPath = '{LAUNCHER}'
$s.WorkingDirectory = '{ROOT}'
$s.Description = 'Star Citizen Helper - automations, frame rate and server info'
$s.WindowStyle = {7 if minimised else 1}
{icon_line}
$s.Save()
Write-Output $link
"""
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or "PowerShell could not create the shortcut")
    return Path(done.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.exit("Desktop shortcuts are a Windows thing.")

    print("Drawing icon    ->", write_icon())
    if "--icon-only" in sys.argv:
        raise SystemExit(0)
    link = create_shortcut(icon=ICON)
    print("Shortcut created ->", link)
    print()
    print("It points at this folder, so keep the project where it is.")
    print("If you move the project, run this again.")
