"""Turn the game's defaultProfile.xml and global.ini into the files that ship.

    py -3 data\\keybinds\\extract_defaults.py      (first: pulls the two files out of Data.p4k)
    py -3 data\\keybinds\\build_defaults.py        (then: writes defaults.xml and labels.json)

A maintainer's tool, run once per game patch; the app only reads its output.

defaults.xml is every keyboard and mouse default, written in the same format
as actionmaps.xml so the app reads it with the reader it already has - one
<rebind input="kb1_..."/> per device, modifiers first the way the game writes
rebinds. labels.json is the English name and description of every action that
has one, and each action map's name and which sheet it belongs to, taken from
the game's own UICategory rather than guessed from the map's name.

Both carry the game version they came from, read from build_manifest.id.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from helper import cryxml  # noqa: E402

_MODIFIERS = ("lalt", "ralt", "lctrl", "rctrl", "lshift", "rshift")
_RE_MOUSE = re.compile(r"^(mouse\d+(?:_\d+)?|mwheel_(?:up|down)|maxis_[xyz])$")

#: The game's category ids, mapped to the sheet's pages.
_CATEGORY_MODE = {
    "@ui_CCSpaceFlight": "Flight", "@ui_CCSeatGeneral": "Flight",
    "@ui_CCTurrets": "Flight", "@ui_CCVehicle": "Flight",
    "@ui_CCFPS": "FPS", "@ui_CCEVA": "FPS", "@ui_CCEVAZGT": "FPS",
}


def load_profile(path: Path) -> ET.Element:
    """defaultProfile.xml as the game ships it (CryXmlB) or as plain XML."""
    data = path.read_bytes()
    if cryxml.is_cryxml(data):
        return cryxml.parse(data)
    return ET.fromstring(data)


def load_strings(path: Path) -> dict[str, str]:
    """global.ini: `key=value` lines, UTF-8 with a BOM, CRLF."""
    strings: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            key, sep, value = line.rstrip("\r\n").partition("=")
            if sep:
                strings[key] = value
    return strings


def localise(strings: dict[str, str], key: str) -> str:
    """'@ui_CIToggleDoors' -> 'Toggle all doors', or '' if there is no string."""
    key = (key or "").strip()
    if not key.startswith("@"):
        return ""
    return strings.get(key[1:], "").strip()


def to_input(device: str, value: str) -> str:
    """'lalt+e' on the keyboard -> 'kb1_lalt+e'; 'u+lshift' -> 'kb1_lshift+u'.

    The profile writes a chord in either order; rebinds always put the
    modifiers first, and the two must compare equal in the table.
    """
    tokens = [t.strip().lower() for t in (value or "").split("+") if t.strip()]
    if not tokens:
        return ""
    modifiers = [t for t in tokens if t in _MODIFIERS]
    keys = [t for t in tokens if t not in _MODIFIERS]
    # A keyboard-column chord whose key is a mouse button is a mouse binding.
    if keys and all(_RE_MOUSE.match(k) for k in keys):
        device = "mouse"
    prefix = "mo1_" if device == "mouse" else "kb1_"
    return prefix + "+".join(modifiers + keys)


def convert(root: ET.Element, strings: dict[str, str], game: str, branch: str
            ) -> tuple[ET.Element, dict]:
    """(the defaults document, the labels dict) from the profile."""
    out = ET.Element("ActionMaps", {
        "version": "1", "optionsVersion": "2", "rebindVersion": "2",
        "profileName": "default", "game": game, "branch": branch,
        "built": _dt.date.today().isoformat(),
    })
    labels: dict = {"game": game, "branch": branch, "maps": {}, "actions": {}}
    for actionmap in root.iter("actionmap"):
        map_name = actionmap.get("name", "")
        if not map_name:
            continue
        category = (actionmap.get("UICategory") or "").strip()
        labels["maps"][map_name] = {
            "label": localise(strings, actionmap.get("UILabel", "")),
            "category": localise(strings, category),
            "mode": _CATEGORY_MODE.get(category, "Other"),
        }
        out_map = ET.SubElement(out, "actionmap", {"name": map_name})
        for action in actionmap.findall("action"):
            name = action.get("name", "")
            if not name:
                continue
            label = localise(strings, action.get("UILabel", ""))
            description = localise(strings, action.get("UIDescription", ""))
            if label or description:
                labels["actions"][name] = {"label": label, "description": description}
            attributes = {"name": name}
            activation = action.get("activationMode") or action.get("ActivationMode") or ""
            if activation:
                attributes["activationMode"] = activation
            out_action = ET.SubElement(out_map, "action", attributes)
            for device in ("keyboard", "mouse"):
                value = action.get(device)
                if value is None:
                    child = action.find(device)
                    value = child.get("input") if child is not None else None
                if value is None:
                    continue
                token = to_input(device, value)
                ET.SubElement(out_action, "rebind", {"input": token or ("mo1_ " if device == "mouse" else "kb1_ ")})
    return out, labels


def game_version(live: Path | None) -> tuple[str, str]:
    """(Version, Branch) from build_manifest.id, or ('', '') without one."""
    if live is None:
        return "", ""
    try:
        with open(live / "build_manifest.id", encoding="utf-8") as handle:
            data = json.load(handle).get("Data", {})
        return data.get("Version", ""), data.get("Branch", "")
    except (OSError, ValueError):
        return "", ""


def find_live() -> Path | None:
    try:
        from helper.net import find_game_log
    except ImportError:
        return None
    log = find_game_log()
    return log.parent if log else None


def _indent(element: ET.Element, level: int = 0) -> None:
    pad = "\n" + " " * level
    if len(element):
        if not (element.text or "").strip():
            element.text = pad + " "
        for child in element:
            _indent(child, level + 1)
        if not (child.tail or "").strip():
            child.tail = pad
    if level and not (element.tail or "").strip():
        element.tail = pad


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--extracted", type=Path, default=HERE / "_extracted",
                        help="folder holding defaultProfile.xml and global.ini")
    parser.add_argument("--live", type=Path, help="the game's LIVE folder, for build_manifest.id")
    parser.add_argument("--out", type=Path, default=HERE)
    args = parser.parse_args(argv)

    profile = args.extracted / "defaultProfile.xml"
    strings_path = args.extracted / "global.ini"
    for path in (profile, strings_path):
        if not path.is_file():
            raise SystemExit("%s is missing - run extract_defaults.py first" % path)
    version, branch = game_version(args.live or find_live())
    root = load_profile(profile)
    strings = load_strings(strings_path)
    document, labels = convert(root, strings, version, branch)

    _indent(document)
    xml_text = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(document, encoding="unicode") + "\n"
    (args.out / "defaults.xml").write_text(xml_text, encoding="utf-8", newline="\n")
    (args.out / "labels.json").write_text(
        json.dumps(labels, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")

    maps = document.findall("actionmap")
    actions = sum(len(m.findall("action")) for m in maps)
    print("defaults.xml : %d action maps, %d actions, game %s (%s)" % (len(maps), actions, version or "?", branch or "?"))
    print("labels.json  : %d action labels, %d map labels" % (len(labels["actions"]), len(labels["maps"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
