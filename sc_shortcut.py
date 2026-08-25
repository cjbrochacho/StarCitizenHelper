"""Create a desktop shortcut for Star Citizen Helper.

Run it once:   python sc_shortcut.py

A .lnk file stores absolute paths - to the target, its working directory and
its icon - so one cannot be shipped in the repository: it would point at
whatever machine built it. This builds the shortcut fresh against wherever the
project actually lives, so it is correct on every machine.

The icon is drawn here rather than committed as a binary, so there is nothing
to keep in sync and no image library to install.
"""

import math
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LAUNCHER = ROOT / "Run_StarCitizenHelper.bat"
ICON = ROOT / "assets" / "StarCitizenHelper.ico"
SHORTCUT_NAME = "Star Citizen Helper.lnk"

# Matches the app's own window colours.
BG = (0x10, 0x17, 0x22)
EDGE = (0x2E, 0x43, 0x5A)
ACCENT = (0x41, 0xB8, 0xF5)
ICON_SIZES = (16, 32, 48, 64, 128, 256)


def _rounded_box(x, y, half, radius):
    """Signed distance to a rounded square centred on the origin."""
    dx = abs(x) - (half - radius)
    dy = abs(y) - (half - radius)
    return math.hypot(max(dx, 0.0), max(dy, 0.0)) + min(max(dx, dy), 0.0) - radius


def _distance_to_segment(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    length = vx * vx + vy * vy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / length))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def _sample(x, y):
    """Colour and alpha at a point in a -1..1 square. Topmost shape wins."""
    distance = math.hypot(x, y)
    if distance < 0.10:                                    # centre blip
        return ACCENT, 1.0
    end = 0.62                                             # sweep arm
    if _distance_to_segment(x, y, 0.0, 0.0,
                            end * math.cos(-math.pi / 4),
                            end * math.sin(-math.pi / 4)) < 0.045:
        return ACCENT, 1.0
    if abs(distance - 0.34) < 0.042:                       # inner ring
        return ACCENT, 0.85
    if abs(distance - 0.62) < 0.05:                        # outer ring
        return ACCENT, 1.0
    box = _rounded_box(x, y, 0.98, 0.28)
    if box < 0:
        return (EDGE if box > -0.06 else BG), 1.0
    return BG, 0.0


def _render(size):
    """Draw the icon at one size and return it as PNG bytes."""
    supersample = 4 if size <= 64 else 2
    step = 1.0 / (size * supersample)
    pixels = bytearray(size * size * 4)

    for row in range(size):
        for col in range(size):
            r = g = b = a = 0.0
            for sy in range(supersample):
                for sx in range(supersample):
                    fx = (col * supersample + sx + 0.5) * step * 2 - 1
                    fy = (row * supersample + sy + 0.5) * step * 2 - 1
                    (cr, cg, cb), ca = _sample(fx, fy)
                    r += cr * ca                           # premultiplied, so
                    g += cg * ca                           # edges blend cleanly
                    b += cb * ca
                    a += ca
            taken = supersample * supersample
            index = (row * size + col) * 4
            if a > 0:
                pixels[index] = int(r / a)
                pixels[index + 1] = int(g / a)
                pixels[index + 2] = int(b / a)
            pixels[index + 3] = int(255 * a / taken)

    stride = size * 4
    raw = bytearray()
    for row in range(size):
        raw.append(0)                                      # no per-row filter
        raw += pixels[row * stride:(row + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def write_icon(path=ICON):
    """Write a multi-size .ico. Each entry is a PNG, which Vista onwards reads."""
    images = [_render(size) for size in ICON_SIZES]
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
    link = create_shortcut(icon=ICON)
    print("Shortcut created ->", link)
    print()
    print("It points at this folder, so keep the project where it is.")
    print("If you move the project, run this again.")
