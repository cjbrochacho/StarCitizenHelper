"""The graphics settings the game is actually running with.

Frame rates mean very little without them: a 4090 at 40fps is a broken machine
or a 4K native run with clouds on ultra, and the number alone cannot tell you
which. So the settings travel with the measurement.

Two sources, and they answer different questions. attributes.xml is what the
player *chose* - quality tiers, upscaling mode, vsync. The log is what the
driver actually *did*, which is the one that settles whether DLSS really
engaged.

The settings file holds 143 attributes, including key bindings, so names are
allowlisted rather than the file being read wholesale. The SysSpec_ family is
taken by prefix because CIG adds to it every patch, but only where the value
is a number - a quality tier is always a number, and a name never is.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Settings worth having, named one at a time.
_WANTED = (
    "Upscaling", "UpscalingModel", "UpscalingTechnique",
    "VSync", "MotionBlur", "Sharpening", "ChromaticAberration", "FilmGrain",
    "FOV", "Gamma", "Resolution", "ScreenMode", "WindowMode", "HDR",
)

#: The quality tiers. Prefix-matched, but numbers only - see the module note.
_TIER_PREFIX = "SysSpec_"

_RE_ATTR = re.compile(r'<Attr\s+name="([^"]{1,64})"\s+value="([^"]{0,32})"')
_RE_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")

#: What the driver reported doing, which beats what the menu says was picked.
_UPSCALERS = (
    ("dlss", re.compile(rb"DLSS initialized successfully", re.I)),
    ("fsr", re.compile(rb"FSR\d?\s+initiali[sz]ed", re.I)),
    ("xess", re.compile(rb"XeSS\s+initiali[sz]ed", re.I)),
)
_RE_DLSS_SUPPORT = re.compile(rb"DLSS Support\s*=\s*(\w+)", re.I)


def profile_dir(live_dir: Path) -> Path | None:
    """The client profile the game last wrote to.

    Installs accumulate USER/client/0, /1 and so on; the freshest one is the
    profile in use.
    """
    root = Path(live_dir) / "USER" / "client"
    best, newest = None, -1.0
    try:
        for child in root.iterdir():
            candidate = child / "Profiles" / "default" / "attributes.xml"
            try:
                stamp = candidate.stat().st_mtime
            except OSError:
                continue
            if stamp > newest:
                best, newest = candidate, stamp
    except OSError:
        return None
    return best


def graphics_settings(live_dir: Path) -> dict:
    """The allowlisted graphics attributes, as numbers where they are numbers."""
    path = profile_dir(live_dir)
    if path is None:
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    out: dict = {}
    for name, value in _RE_ATTR.findall(text):
        tier = name.startswith(_TIER_PREFIX)
        if not tier and name not in _WANTED:
            continue
        if tier and not _RE_NUMBER.match(value):
            continue                     # a tier is a number; a name is not
        out[name] = _coerce(value)
    return out


def _coerce(value: str):
    if not _RE_NUMBER.match(value):
        return value[:32]
    return float(value) if "." in value else int(value)


def upscaler(log_path: Path | None) -> dict:
    """What the driver actually brought up, read from the log.

    "Available" is not "on": the support line appears whether or not the
    player enabled anything, so the initialised line is what counts.
    """
    if not log_path:
        return {"upscaler": "", "dlss_support": ""}
    try:
        body = Path(log_path).read_bytes()
    except OSError:
        return {"upscaler": "", "dlss_support": ""}

    active = ""
    for name, pattern in _UPSCALERS:
        if pattern.search(body):
            active = name
            break
    support = _RE_DLSS_SUPPORT.search(body)
    return {"upscaler": active,
            "dlss_support": support.group(1).decode("ascii", "replace").lower()
                            if support else ""}
