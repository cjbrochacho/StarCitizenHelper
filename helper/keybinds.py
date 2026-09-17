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

The full list comes from the game's own defaultProfile.xml after all - not
read at runtime, but pulled out of Data.p4k once per patch by the maintainer
(data/keybinds/extract_defaults.py: the file is plain zstd inside a ZIP64,
readable with the standard library from Python 3.14) and shipped as
data/keybinds/defaults.xml in the actionmaps format, with the game's own
English names beside it in labels.json. `merge` lays the player's rebinds
over that, and the result is what the game is using - for the build the
defaults were taken from, which the status line says.

The game's own export (console: `pp_rebindkeys export all <name>`, into
USER\\client\\<n>\\Controls\\Mappings\\) writes only the rebinds in 4.x,
not the whole list; `load_full_export` still looks for one that is complete,
in case a later build does better, and the shipped defaults are the fallback.

`describe_action` and `mode_of` read labels.json first and guess only where
it has nothing to say.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from helper.gamecfg import profile_dir

#: What the maintainer's build_defaults.py writes, and this reads.
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "keybinds"
SHIPPED_DEFAULTS = DATA_DIR / "defaults.xml"
SHIPPED_LABELS = DATA_DIR / "labels.json"


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


def load_shipped_defaults(path: Path = SHIPPED_DEFAULTS) -> tuple[Path | None, list[Rebind], str]:
    """The defaults that ship with the app, and the game build they came from."""
    rows = read_actionmaps(path)
    if not is_full_export(rows):
        return None, [], ""
    game = ""
    try:
        for _event, element in ET.iterparse(path, events=("start",)):
            game = element.get("game", "")
            break
    except (OSError, ET.ParseError):
        pass
    return path, rows, game


def installed_game_version(live_dir: Path | None) -> str:
    """The build the game folder says it is, from build_manifest.id; '' if unknown."""
    if live_dir is None:
        return ""
    try:
        with open(Path(live_dir) / "build_manifest.id", encoding="utf-8") as handle:
            return str(json.load(handle).get("Data", {}).get("Version", ""))
    except (OSError, ValueError, AttributeError):
        return ""


_labels_cache: dict | None = None


def labels(path: Path = SHIPPED_LABELS) -> dict:
    """labels.json, read once. {} when it is missing or unreadable."""
    global _labels_cache
    if _labels_cache is None:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            _labels_cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _labels_cache = {}
    return _labels_cache


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
_RE_MOUSE_BUTTON = re.compile(r"^(?:mouse|button)(\d+)(?:_(\d+))?$")   # mouse1_2: double click
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
        return "Mouse " + match.group(1) + (" x" + match.group(2) if match.group(2) else "")
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
    """A readable label for an action id: the game's own, else a guess."""
    entry = labels().get("actions", {}).get(name)
    if entry and entry.get("label"):
        return entry["label"]
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
    """'Flight', 'FPS' or 'Other' - the page of the reference sheet it is on.

    The game's own category for the map decides where it has one; the
    prefix table stands in where it does not (mining, salvage, a few more
    carry no category in the profile).
    """
    entry = labels().get("maps", {}).get(actionmap or "")
    if entry and entry.get("mode") in ("Flight", "FPS"):
        return entry["mode"]
    name = (actionmap or "").lower()
    if name.startswith(_FLIGHT):
        return "Flight"
    if name.startswith(_FPS):
        return "FPS"
    return "Other"


def describe_export_status(path: Path, actions: int, yours: int, stale: bool) -> str:
    """The one line under the full table, when the list came from a game export."""
    try:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
    except OSError:
        when = "?"
    line = "%s - %s actions, %d yours, exported %s" % (
        path.name, format(actions, ","), yours, when)
    if stale:
        line += " - actionmaps.xml changed since; re-export for an exact list"
    return line


def describe_shipped_status(game: str, installed: str, actions: int, yours: int) -> str:
    """The one line under the full table, when the list is the shipped defaults."""
    line = "Game defaults for %s - %s actions, %d yours" % (
        game or "an unknown build", format(actions, ","), yours)
    if game and installed and not installed.startswith(game.rsplit(".", 1)[0]):
        line += " - the installed game is %s; some defaults may have moved" % installed
    return line


def describe_status(path: Path | None, rebinds: list[Rebind]) -> str:
    """The one line under the table: where it looked, and what it found."""
    if path is None:
        return "Star Citizen not found - no LIVE\\USER profile on this machine."
    if not rebinds:
        return "%s - no custom bindings (everything is at the game default)." % path
    return "%s - %d rebind%s" % (path, len(rebinds), "" if len(rebinds) == 1 else "s")


# --- the table: what to show, in what order --------------------------------

def map_label(actionmap: str) -> str:
    """The game's name for an action map - 'Vehicles - Cockpit' - or its id."""
    entry = labels().get("maps", {}).get(actionmap or "")
    return (entry or {}).get("label") or actionmap or ""


def describe_bound(b: Binding) -> str:
    """The 'Bound to' cell: the key, or (unbound), with taps and activation."""
    text = describe_input(b.input) or "(unbound)"
    if b.multitap > 1:
        text += "  x%d" % b.multitap
    if b.activation:
        text += "  (%s)" % b.activation
    return text


def chord_key(raw: str) -> tuple:
    """A chord as something two spellings of it compare equal on.

    kb1_lctrl+lshift+f and kb1_lshift+lctrl+f are one binding; the game
    writes modifiers in whatever order the player pressed them.
    """
    match = _RE_INPUT.match((raw or "").strip())
    if not match or match.group(1) != "kb":
        return ("", frozenset(), (raw or "").strip())
    parts = [p for p in match.group(3).split("+") if p]
    modifiers = frozenset(p for p in parts if p in _MODIFIERS)
    keys = [p for p in parts if p not in _MODIFIERS]
    return ("kb", modifiers, "+".join(keys))


@dataclass(frozen=True)
class Filters:
    """Everything the table can be narrowed by. Empty means 'not applied'."""
    mode: str = ""                  # the sheet's page: 'Flight' or 'FPS'
    show_all_modes: bool = False
    bound: str = "all"              # 'all', 'bound', 'unbound'
    category: str = ""              # a map_label(); '' for all
    device: str = ""                # '', 'keyboard', 'mouse'
    yours_only: bool = False
    conflicts_only: bool = False
    needle: str = ""                # lower-cased, stripped
    key: str = ""                   # a raw input to match by chord; '' for none


def visible(bindings: list[Binding], conflict_keys, f: Filters) -> list[Binding]:
    """The rows the table shows for these filters, in the game's own order.

    An action with a keyboard default usually carries an unbound mouse row
    beside it in the shipped defaults. Showing both would double the table,
    so an unbound row appears only when nothing is bound to its action on
    any device being looked at - and only once, however many devices are
    unbound. The exception is a binding the player cleared: that is theirs,
    and it always shows.
    """
    def device_ok(b):
        return bool(b.device) and (not f.device or b.device == f.device)

    has_bound = {(b.actionmap, b.action) for b in bindings if device_ok(b) and b.input}
    shown_unbound: set = set()
    wanted_chord = chord_key(f.key) if f.key else None
    out = []
    for b in bindings:
        if not device_ok(b):
            continue
        if not b.input and b.source != "rebind":
            if (b.actionmap, b.action) in has_bound or (b.actionmap, b.action) in shown_unbound:
                continue
            shown_unbound.add((b.actionmap, b.action))
        if f.bound == "bound" and not b.input:
            continue
        if f.bound == "unbound" and b.input:
            continue
        mode = mode_of(b.actionmap)
        if not f.show_all_modes and f.mode and mode != f.mode:
            continue
        if f.category and map_label(b.actionmap) != f.category:
            continue
        if f.yours_only and b.source != "rebind":
            continue
        if f.conflicts_only and (mode, b.input) not in conflict_keys:
            continue
        if wanted_chord is not None:
            if not b.input or chord_key(b.input) != wanted_chord:
                continue
        elif f.needle:
            haystack = " ".join((describe_action(b.action), b.action, describe_bound(b),
                                 b.actionmap, map_label(b.actionmap))).lower()
            if f.needle not in haystack:
                continue
        out.append(b)
    return out


def categories(bindings: list[Binding], mode: str, show_all: bool) -> list[str]:
    """The map labels on offer for the category menu, for this page."""
    found = {map_label(b.actionmap) for b in bindings
             if b.device and (show_all or not mode or mode_of(b.actionmap) == mode)}
    return sorted(found, key=str.lower)


def sort_rows(rows: list[Binding], column: str | None, reverse: bool = False) -> list[Binding]:
    """Rows ordered by a column; None keeps the game's grouping. Stable."""
    if column == "action":
        key = lambda b: describe_action(b.action).lower()
    elif column == "bound":
        key = lambda b: (not b.input, describe_input(b.input).lower())   # unbound sink
    elif column == "map":
        key = lambda b: map_label(b.actionmap).lower()
    else:
        return list(rows)
    return sorted(rows, key=key, reverse=reverse)


def row_values(b: Binding, mode: str) -> tuple:
    return (mode, describe_action(b.action), describe_bound(b), map_label(b.actionmap),
            "yours" if b.source == "rebind" else "")


def row_tags(b: Binding, mode: str, conflict_keys) -> tuple:
    if b.input and (mode, b.input) in conflict_keys:
        return ("conflict",)
    if b.source == "rebind":
        return ("rebind",)
    if not b.input:
        return ("unbound",)
    return ()


def counts(rows: list[Binding], conflict_keys) -> tuple[int, int, int]:
    """(shown, yours, conflicts) for the status line."""
    yours = sum(1 for b in rows if b.source == "rebind")
    clashing = sum(1 for b in rows if b.input and (mode_of(b.actionmap), b.input) in conflict_keys)
    return len(rows), yours, clashing


def describe_detail(b: Binding) -> str:
    """The line under the table for the selected row: what it is, exactly."""
    entry = labels().get("actions", {}).get(b.action) or {}
    head = entry.get("description") or describe_action(b.action)
    parts = [b.action, b.actionmap, b.input or "(unbound)"]
    if b.activation:
        parts.append(b.activation)
    return "%s — %s" % (head, " · ".join(parts))


def copy_text(b: Binding) -> str:
    return "%s: %s  (%s in %s, %s)" % (describe_action(b.action), describe_bound(b),
                                        b.action, b.actionmap, b.input or "unbound")


# --- between the game's spelling and the keyboard library's -----------------

_MOD_TO_KEYBOARD = {
    "lalt": "alt", "ralt": "right alt",
    "lctrl": "ctrl", "rctrl": "right ctrl",
    "lshift": "shift", "rshift": "right shift",
}
_KEYBOARD_TO_MOD = {
    "alt": "lalt", "left alt": "lalt", "right alt": "ralt", "alt gr": "ralt",
    "ctrl": "lctrl", "left ctrl": "lctrl", "right ctrl": "rctrl",
    "shift": "lshift", "left shift": "lshift", "right shift": "rshift",
}
#: The game's key names against the keyboard library's. One table, read
#: both ways; the library's names are its canonical Windows ones.
_KEY_TO_KEYBOARD = {
    "slash": "/", "backslash": "\\", "comma": ",", "period": ".",
    "semicolon": ";", "apostrophe": "'", "lbracket": "[", "rbracket": "]",
    "minus": "-", "equals": "=", "grave": "`",
    "space": "space", "enter": "enter", "escape": "esc", "tab": "tab",
    "backspace": "backspace", "capslock": "caps lock", "numlock": "num lock",
    "insert": "insert", "delete": "delete", "home": "home", "end": "end",
    "pgup": "page up", "pgdn": "page down",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "print": "print screen", "scrolllock": "scroll lock", "pause": "pause",
    "np_add": "+", "np_subtract": "-", "np_multiply": "*", "np_divide": "/",
    "np_period": ".", "np_enter": "enter",
}
_KEYBOARD_TO_KEY = {v: k for k, v in _KEY_TO_KEYBOARD.items()
                    if not k.startswith("np_")}          # the plain key wins for '+', '-', ...
#: What the library reports for a shifted key on a US layout; the game
#: binds the key, not the character.
_UNSHIFT = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
    "(": "9", ")": "0", "_": "minus", "+": "equals", "{": "lbracket", "}": "rbracket",
    "|": "backslash", ":": "semicolon", '"': "apostrophe", "<": "comma", ">": "period",
    "?": "slash", "~": "grave",
}
#: Numpad keys with Num Lock off report as navigation keys.
_KEYPAD = {
    "end": "np_1", "down": "np_2", "page down": "np_3", "left": "np_4", "clear": "np_5",
    "right": "np_6", "home": "np_7", "up": "np_8", "page up": "np_9", "insert": "np_0",
    "delete": "np_period", "decimal": "np_period", "enter": "np_enter",
    "+": "np_add", "-": "np_subtract", "*": "np_multiply", "/": "np_divide",
}


def to_keyboard_syntax(raw: str) -> str | None:
    """'kb1_lalt+k' -> 'alt+k', as a macro action; None if a macro cannot say it.

    A macro's actions are split on commas and each on '+', so a comma key,
    or a numpad plus under a modifier, cannot be written. Numpad digits come
    out as plain digits: the library sends the top-row key for those.
    """
    match = _RE_INPUT.match((raw or "").strip())
    if not match or match.group(1) != "kb":
        return None
    parts = [p for p in match.group(3).split("+") if p]
    if not parts:
        return None
    modifiers = [p for p in parts if p in _MODIFIERS]
    keys = [p for p in parts if p not in _MODIFIERS]
    if len(keys) != 1:
        return None
    key = keys[0]
    if key == "comma" or (key == "np_add" and modifiers):
        return None
    if key in _KEY_TO_KEYBOARD:
        name = _KEY_TO_KEYBOARD[key]
    elif len(key) == 1 or _RE_FKEY.match(key):
        name = key
    elif _RE_NP_DIGIT.match(key):
        name = key[3:]
    else:
        return None
    return "+".join([_MOD_TO_KEYBOARD[m] for m in modifiers] + [name])


def from_keyboard_names(modifiers, key: str, is_keypad: bool = False) -> str:
    """What the keyboard library reported, as the game would write it.

    ['right alt'], 'k' -> 'kb1_ralt+k'. Windows keys are dropped - the game
    has no such modifier. A keypad digit arrives as '1' with the keypad flag,
    and with Num Lock off as 'end'; both are np_1 to the game.
    """
    mods = []
    for name in modifiers:
        token = _KEYBOARD_TO_MOD.get((name or "").lower())
        if token and token not in mods:
            mods.append(token)
    key = (key or "").strip()
    lower = key.lower()
    if is_keypad and (lower in _KEYPAD or (len(key) == 1 and key.isdigit())):
        token = _KEYPAD.get(lower) or "np_" + key
    elif key in _UNSHIFT:
        token = _UNSHIFT[key]
    elif lower in _KEYBOARD_TO_KEY:
        token = _KEYBOARD_TO_KEY[lower]
    elif len(key) == 1:
        token = lower
    elif _RE_FKEY.match(lower):
        token = lower
    else:
        token = lower.replace(" ", "_")
    return "kb1_" + "+".join(mods + [token])
