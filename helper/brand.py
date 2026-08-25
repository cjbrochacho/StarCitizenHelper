"""The app's radar mark - the single source of the artwork.

Both the header badge and the .ico come from `render_mark`, so they cannot
drift apart. It is drawn by supersampling, which matters: Tk's canvas draws
shapes with hard pixel edges, so a canvas version of the same mark looks
visibly jagged next to the smooth icon Windows shows in the taskbar.

Rendered in memory rather than loaded from disk, so the header badge appears
even in a fresh checkout where the installer has not written an icon yet.
"""

import base64
import math
import struct
import tkinter as tk
import zlib

from .theme import BG

# Matches the app's window colours.
BACKDROP = (0x10, 0x17, 0x22)
EDGE = (0x2E, 0x43, 0x5A)
ACCENT = (0x41, 0xB8, 0xF5)


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


def _sample(x, y, backdrop):
    """Colour and alpha at a point in a -1..1 square. Topmost shape wins."""
    distance = math.hypot(x, y)
    if distance < 0.10:                                     # centre blip
        return ACCENT, 1.0
    end = 0.62                                              # sweep arm
    if _distance_to_segment(x, y, 0.0, 0.0,
                            end * math.cos(-math.pi / 4),
                            end * math.sin(-math.pi / 4)) < 0.045:
        return ACCENT, 1.0
    if abs(distance - 0.34) < 0.042:                        # inner ring
        return ACCENT, 0.85
    if abs(distance - 0.62) < 0.05:                         # outer ring
        return ACCENT, 1.0
    box = _rounded_box(x, y, 0.98, 0.28)
    if box < 0:
        return (EDGE if box > -0.06 else backdrop), 1.0
    return backdrop, 0.0


def render_mark(size, backdrop=BACKDROP):
    """The mark at one size, as PNG bytes, antialiased by supersampling."""
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
                    (cr, cg, cb), ca = _sample(fx, fy, backdrop)
                    r += cr * ca                            # premultiplied, so
                    g += cg * ca                            # edges blend cleanly
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
        raw.append(0)                                       # no per-row filter
        raw += pixels[row * stride:(row + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


class BrandMark(tk.Label):
    """The mark as an image, so it gets the same smooth edges as the icon."""

    def __init__(self, parent, size=46, background=BG):
        # Tk 8.6 reads PNG from base64 data, so this needs no file on disk.
        self._image = tk.PhotoImage(
            master=parent, data=base64.b64encode(render_mark(size, _rgb(background))))
        super().__init__(parent, image=self._image, bg=background,
                         bd=0, highlightthickness=0)


def _rgb(colour):
    """'#101722' -> (16, 23, 34), so the mark blends into its background."""
    try:
        value = colour.lstrip("#")
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except (AttributeError, ValueError):
        return BACKDROP
