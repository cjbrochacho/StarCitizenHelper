"""The player's own key bindings, read from the game's profile.

Star Citizen keeps two things apart. The defaults for every action live in
defaultProfile.xml inside Data.p4k - a 150 GB archive in CIG's own flavour of
ZIP64 that the standard library refuses to open, and whose contents are
compressed and partly encrypted besides. What the player has *changed* lives
in a small plain file beside attributes.xml:

    LIVE\\USER\\client\\<n>\\Profiles\\default\\actionmaps.xml

Only the changes. An action that is still at its default is not in the file
at all, so this module can say "these are your rebinds" and cannot, on its
own, say "these are all your bindings". The Key Bindings tab pairs it with a
rendered reference sheet of the defaults for that reason.

There is a way to the full list without opening the archive. The game can
export its own bindings - in the console, `pp_rebindkeys export all <name>`,
or Options > Keybindings > Advanced > Export - to

    LIVE\\USER\\client\\<n>\\Controls\\Mappings\\layout_<name>_exported.xml

in the very same format, with every action listed and the unbound ones as
"kb1_ ". `load_full_export` finds such a file, `merge` lays the player's
rebinds over it, and the result is what the game is actually using - as of
the export, which is the one caveat the status line has to keep saying.

`describe_action` is the one place display names come from; the game's own
localised strings could slot in there and nowhere else.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from helper.gamecfg import profile_dir


@dataclass(frozen=True)
class Rebind:
    """One <rebind> in actionmaps.xml. `input` is '' when the player cleared it.

    `device` survives the clearing: "kb1_ " means the keyboard binding was
    removed, "mo1_ " the mouse one, and a merge has to tell them apart.
    """
    actionmap: str
    action: str
    input: str
    activation: str = ""
    multitap: int = 1
    device: str = ""


@dataclass(frozen=True)
class Binding:
    """One row of the full table: a default from the export, or the player's."""
    actionmap: str
    action: str
    input: str
    source: str                     # 'default' or 'rebind'
    activation: str = ""
    multitap: int = 1
    device: str = ""


# --- finding and reading ----------------------------------------------------

def find_actionmaps(live_dir: Path | None) -> Path | None:
    """actionmaps.xml for the profile the game last wrote to, if it exists."""
    if live_dir is None:
        return None
    attributes = profile_dir(live_dir)
    if attributes is None:
        return None
    candidate = attributes.with_name("actionmaps.xml")
    return candidate if candidate.is_file() else None


#: A bare device with no key after it - "kb1_" - is how the game writes a
#: binding the player removed. So is an empty string.
_RE_BARE_DEVICE = re.compile(r"^[a-z]{2}\d+_?$")


def read_actionmaps(path: Path | None) -> list[Rebind]:
    """Every <rebind> in an action-maps file, in file order; unreadable is [].

    The same reader serves actionmaps.xml (the player's changes) and a
    layout_*_exported.xml (everything): the format is one and the same.
    """
    if path is None:
        return []
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    found: list[Rebind] = []
    for actionmap in root.iter("actionmap"):
        map_name = actionmap.get("name", "")
        for action in actionmap.iter("action"):
            action_name = action.get("name", "")
            try:
                multitap = int(action.get("multiTap") or 1)
            except ValueError:
                multitap = 1
            for rebind in action.findall("rebind"):
                raw = (rebind.get("input") or "").strip()
                kind = device(raw)
                if _RE_BARE_DEVICE.match(raw):
                    raw = ""
                found.append(Rebind(
                    actionmap=map_name,
                    action=action_name,
                    input=raw,
                    activation=rebind.get("activationMode") or action.get("activationMode") or "",
                    multitap=multitap,
                    device=kind,
                ))
    return found


read_rebinds = read_actionmaps


# --- the full export ---------------------------------------------------------

#: A rebinds-only file has a handful of actions; a full export has well over
#: a thousand. Anything in between is not a file this app wrote or wants.
FULL_EXPORT_MIN_ACTIONS = 150

EXPORT_COMMAND = "pp_rebindkeys export all sch"


def mappings_dir(live_dir: Path | None) -> Path | None:
    """Where the game writes exports: USER\\client\\<n>\\Controls\\Mappings."""
    if live_dir is None:
        return None
    attributes = profile_dir(live_dir)
    if attributes is None:
        return None
    # attributes.xml sits in Profiles/default; the client folder is two up.
    return attributes.parents[2] / "Controls" / "Mappings"


def find_exports(live_dir: Path | None) -> list[Path]:
    """Every layout_*_exported.xml for the current client, newest first."""
    folder = mappings_dir(live_dir)
    if folder is None:
        return []
    try:
        found = [p for p in folder.glob("layout_*_exported.xml") if p.is_file()]
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return found
    except OSError:
        return []


def is_full_export(rows: list[Rebind]) -> bool:
    return len({(r.actionmap, r.action) for r in rows}) >= FULL_EXPORT_MIN_ACTIONS


def load_full_export(live_dir: Path | None) -> tuple[Path | None, list[Rebind]]:
    """The newest export that lists everything, and its rows; (None, []) if none."""
    for path in find_exports(live_dir):
        rows = read_actionmaps(path)
        if is_full_export(rows):
            return path, rows
    return None, []


def merge(base: list[Rebind], rebinds: list[Rebind]) -> list[Binding]:
    """The export with the player's changes laid over it.

    Keyed on (actionmap, action, device): rebinding the keyboard leaves a
    mouse binding for the same action alone. A rebind replaces its row in
    place, so the export's order - the game's own grouping - is kept; one
    for an action the export does not know is appended. A cleared binding
    stays as a row with no input rather than vanishing, so the table can
    still be searched for it and can say the player cleared it.
    """
    rows: list[Binding | None] = [
        Binding(r.actionmap, r.action, r.input, "default", r.activation, r.multitap, r.device)
        for r in base]
    slot: dict[tuple[str, str, str], int] = {}
    for index, r in enumerate(base):
        slot[(r.actionmap, r.action, r.device)] = index      # a repeat in the export: last wins
    for r in rebinds:
        key = (r.actionmap, r.action, r.device)
        row = Binding(r.actionmap, r.action, r.input, "rebind", r.activation, r.multitap, r.device)
        if key in slot:
            rows[slot[key]] = row
        else:
            slot[key] = len(rows)
            rows.append(row)
    return [row for row in rows if row is not None]


def conflicts(bindings: list[Binding]) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Keys bound to more than one action in the same mode, where the player
    is involved.

    The game's own defaults share keys on purpose - mining and salvage are
    modifier sub-modes of flight, press and hold on one key are two actions -
    so a group made only of defaults is not a conflict. One the player made,
    or made worse, is. Keyed on (mode, input): action maps within a mode are
    active together, so a clash across two of them is still a clash.
    """
    groups: dict[tuple[str, str], dict[tuple[str, str], str]] = {}
    for b in bindings:
        if not b.input or b.device not in ("keyboard", "mouse"):
            continue
        members = groups.setdefault((mode_of(b.actionmap), b.input), {})
        members[(b.actionmap, b.action)] = b.source
    return {
        key: list(members)
        for key, members in groups.items()
        if len(members) >= 2 and "rebind" in members.values()
    }


# --- naming what is bound ----------------------------------------------------

_DEVICES = {"kb": "keyboard", "mo": "mouse", "js": "joystick", "gp": "gamepad"}

_RE_INPUT = re.compile(r"^(kb|mo|js|gp)(\d+)_(.+)$")

_MODIFIERS = {
    "lalt": "Alt", "ralt": "RAlt",
    "lctrl": "Ctrl", "rctrl": "RCtrl",
    "lshift": "Shift", "rshift": "RShift",
}

_KEYS = {
    "np_add": "Num +", "np_subtract": "Num -", "np_multiply": "Num *",
    "np_divide": "Num /", "np_period": "Num .", "np_enter": "Num Enter",
    "numlock": "Num Lock",
    "slash": "/", "backslash": "\\", "comma": ",", "period": ".",
    "semicolon": ";", "apostrophe": "'", "lbracket": "[", "rbracket": "]",
    "minus": "-", "equals": "=", "grave": "`",
    "space": "Space", "enter": "Enter", "escape": "Esc", "tab": "Tab",
    "backspace": "Backspace", "capslock": "Caps Lock",
    "insert": "Insert", "delete": "Delete", "home": "Home", "end": "End",
    "pgup": "Page Up", "pgdn": "Page Down",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "print": "Print Screen", "scrolllock": "Scroll Lock", "pause": "Pause",
}

_MOUSE = {
    "mwheel_up": "Wheel Up", "mwheel_down": "Wheel Down",
    "maxis_x": "Mouse X", "maxis_y": "Mouse Y",
}

_RE_NP_DIGIT = re.compile(r"^np_(\d)$")
_RE_FKEY = re.compile(r"^f(\d{1,2})$")
_RE_MOUSE_BUTTON = re.compile(r"^(?:mouse|button)(\d+)$")
_RE_JS_BUTTON = re.compile(r"^button(\d+)$")
_RE_JS_AXIS = re.compile(r"^(x|y|z|rotx|roty|rotz|slider\d)$")
_RE_JS_HAT = re.compile(r"^hat(\d)_(up|down|left|right)$")


_RE_DEVICE = re.compile(r"^(kb|mo|js|gp)\d+(?:_|$)")


def device(raw: str) -> str:
    """'keyboard', 'mouse', 'joystick', 'gamepad', or '' for anything else.

    A bare "kb1_" still names its device: that is the game's way of writing
    a keyboard binding the player removed.
    """
    match = _RE_DEVICE.match((raw or "").strip())
    return _DEVICES[match.group(1)] if match else ""


def _plain(token: str) -> str:
    return token.replace("_", " ").title()


def _keyboard_part(token: str) -> str:
    if token in _MODIFIERS:
        return _MODIFIERS[token]
    if token in _KEYS:
        return _KEYS[token]
    if len(token) == 1:
        return token.upper()
    match = _RE_NP_DIGIT.match(token)
    if match:
        return "Num " + match.group(1)
    match = _RE_FKEY.match(token)
    if match:
        return "F" + match.group(1)
    return _plain(token)


def _mouse_part(token: str) -> str:
    if token in _MODIFIERS:                       # kb modifiers appear on mouse chords
        return _MODIFIERS[token]
    if token in _MOUSE:
        return _MOUSE[token]
    match = _RE_MOUSE_BUTTON.match(token)
    if match:
        return "Mouse " + match.group(1)
    return _plain(token)


def _joystick_part(token: str, instance: str) -> str:
    prefix = "Joy%s " % instance
    match = _RE_JS_BUTTON.match(token)
    if match:
        return prefix + "B" + match.group(1)
    if _RE_JS_AXIS.match(token):
        # x -> X, rotx -> RotX, slider1 -> Slider1
        if token.startswith("rot"):
            axis = "Rot" + token[3:].upper()
        else:
            axis = token.capitalize() if len(token) > 1 else token.upper()
        return prefix + axis
    match = _RE_JS_HAT.match(token)
    if match:
        return "%sHat%s %s" % (prefix, match.group(1), match.group(2).capitalize())
    return prefix + _plain(token)


def describe_input(raw: str) -> str:
    """'kb1_lalt+k' -> 'Alt+K'; 'mo1_mouse1' -> 'Mouse 1'; '' -> ''.

    Anything not in the tables comes back title-cased rather than raising,
    so an input the game invents next patch still reads as something.
    """
    raw = (raw or "").strip()
    if not raw or _RE_BARE_DEVICE.match(raw):
        return ""
    match = _RE_INPUT.match(raw)
    if not match:
        return _plain(raw)
    kind, instance, rest = match.groups()
    parts = [p for p in rest.split("+") if p]
    if kind == "kb":
        named = [_keyboard_part(p) for p in parts]
    elif kind == "mo":
        named = [_mouse_part(p) for p in parts]
    elif kind == "js":
        named = [_joystick_part(p, instance) for p in parts]
    else:
        named = ["Pad " + _plain(p) for p in parts]
    return "+".join(named)


# --- naming the action -------------------------------------------------------

#: Names the heuristic gets wrong or awkward. Phase 2 replaces the whole
#: function with the game's own strings; until then, add to this as they turn
#: up in real files.
_OVERRIDES = {
    "v_toggle_all_doors": "Toggle all doors",
    "v_toggle_all_doorlocks": "Lock/unlock all doors",
    "v_enter_remote_turret_1": "Enter remote turret 1",
    "v_enter_remote_turret_2": "Enter remote turret 2",
    "v_enter_remote_turret_3": "Enter remote turret 3",
    "v_toggle_landing_system": "Landing gear",
    "v_toggle_vtol": "VTOL toggle",
    "v_toggle_qdrive_engagement": "Quantum drive engage",
    "v_afterburner": "Afterburner",
    "v_boost": "Boost",
    "v_ifcs_toggle_cruise_control": "Cruise control",
    "v_toggle_scan_mode": "Scan mode",
    "v_toggle_mining_mode": "Mining mode",
    "v_toggle_salvage_mode": "Salvage mode",
    "pl_exit": "Exit seat",
    "v_eject": "Eject",
    "v_self_destruct": "Self-destruct",
}

_PREFIXES = ("v_", "pl_", "ui_", "mfd_", "eva_")
_KEEP_UPPER = {"hud", "mfd", "qt", "vtol", "esp", "ifcs", "eva", "atc", "ui"}


def describe_action(name: str) -> str:
    """A readable label for an action id, until the game's own strings arrive."""
    if name in _OVERRIDES:
        return _OVERRIDES[name]
    stripped = name
    for prefix in _PREFIXES:
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    words = [w for w in stripped.split("_") if w]
    if not words:
        return name
    out = []
    for i, word in enumerate(words):
        if word.lower() in _KEEP_UPPER:
            out.append(word.upper())
        elif i == 0:
            out.append(word.capitalize())
        else:
            out.append(word.lower())
    return " ".join(out)


# --- which sheet an actionmap belongs to ------------------------------------

#: Prefixes as they appear in community exports of actionmaps.xml; not
#: checked against defaultProfile.xml, which this module cannot read.
_FLIGHT = ("spaceship_", "seat_", "vehicle_", "turret_", "tractor_", "lights_",
           "ifcs", "mining", "salvage", "quantum", "scanning", "radar")
_FPS = ("player", "prone", "zero_gravity", "mobiglas", "mapui", "incapacitated", "ea_")


def mode_of(actionmap: str) -> str:
    """'Flight', 'FPS' or 'Other' - the page of the reference sheet it is on."""
    name = (actionmap or "").lower()
    if name.startswith(_FLIGHT):
        return "Flight"
    if name.startswith(_FPS):
        return "FPS"
    return "Other"


def describe_export_status(path: Path, actions: int, yours: int, stale: bool) -> str:
    """The one line under the full table."""
    try:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
    except OSError:
        when = "?"
    line = "%s - %s actions, %d yours, exported %s" % (
        path.name, format(actions, ","), yours, when)
    if stale:
        line += " - actionmaps.xml changed since; re-export for an exact list"
    return line


def describe_status(path: Path | None, rebinds: list[Rebind]) -> str:
    """The one line under the table: where it looked, and what it found."""
    if path is None:
        return "Star Citizen not found - no LIVE\\USER profile on this machine."
    if not rebinds:
        return "%s - no custom bindings (everything is at the game default)." % path
    return "%s - %d rebind%s" % (path, len(rebinds), "" if len(rebinds) == 1 else "s")
