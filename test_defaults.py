"""What the defaults pipeline must not get wrong:

    python test_defaults.py

No framework and no dependencies, like the rest of this project. It builds a
small CryXmlB document by hand and reads it back, and runs the converter on
a profile it makes up; Data.p4k is never opened.

The case it exists for: the table's full list is data/keybinds/defaults.xml,
and every row in it went through helper/cryxml.py and then
data/keybinds/build_defaults.py. A wrong offset in the one, or a chord put
in the wrong order by the other, is a thousand quietly wrong rows.
"""

import json
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "data" / "keybinds"))

from helper import cryxml
import build_defaults  # noqa: E402  (data/keybinds/build_defaults.py)

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


# --- a CryXmlB encoder, for the test's own use -------------------------------

def encode(root):
    """An ElementTree element as CryXmlB, laid out the way the game does."""
    strings = bytearray()
    offsets = {}

    def intern(text):
        if text not in offsets:
            offsets[text] = len(strings)
            strings.extend(text.encode("utf-8") + b"\0")
        return offsets[text]

    nodes, attributes, children = [], [], []
    order = []

    def walk(element):                       # breadth-first, parents before children
        order.append(element)
    queue = [root]
    while queue:
        element = queue.pop(0)
        walk(element)
        queue.extend(list(element))
    index = {id(e): i for i, e in enumerate(order)}
    for element in order:
        first_attr = len(attributes)
        for key, value in element.attrib.items():
            attributes.append((intern(key), intern(value)))
        first_child = len(children)
        for child in element:
            children.append(index[id(child)])
        parent = next((index[id(p)] for p in order if element in list(p)), 0xFFFFFFFF)
        nodes.append((intern(element.tag), intern((element.text or "").strip()),
                      len(element.attrib), len(element), parent, first_attr, first_child, 0))
    node_off = 44
    attr_off = node_off + 28 * len(nodes)
    child_off = attr_off + 8 * len(attributes)
    str_off = child_off + 4 * len(children)
    body = b"".join(struct.pack("<IIHHIIII", *n) for n in nodes)
    body += b"".join(struct.pack("<II", *a) for a in attributes)
    body += struct.pack("<%dI" % len(children), *children)
    body += bytes(strings)
    total = 44 + len(body)
    header = struct.pack("<8s9I", b"CryXmlB\0", total, node_off, len(nodes), attr_off,
                         len(attributes), child_off, len(children), str_off, len(strings))
    return header + body


# --- 1 ---------------------------------------------------------------------

print("\n1. binary XML comes back as the XML it was")


def _roundtrip():
    root = ET.Element("profile", {"version": "1"})
    am = ET.SubElement(root, "actionmap", {"name": "seat_general", "UICategory": "@ui_CCSeatGeneral"})
    ET.SubElement(am, "action", {"name": "v_eject", "keyboard": "ralt+y", "UILabel": "@ui_CIEject"})
    a2 = ET.SubElement(am, "action", {"name": "v_look", "keyboard": "comma"})
    ET.SubElement(a2, "mouse", {"input": "mouse3"})
    note = ET.SubElement(root, "note")
    note.text = "hello"
    blob = encode(root)
    assert cryxml.is_cryxml(blob)
    back = cryxml.parse(blob)
    assert back.tag == "profile" and back.get("version") == "1"
    maps = back.findall("actionmap")
    assert len(maps) == 1 and maps[0].get("UICategory") == "@ui_CCSeatGeneral"
    actions = maps[0].findall("action")
    assert [a.get("name") for a in actions] == ["v_eject", "v_look"], "children out of order"
    assert actions[0].get("keyboard") == "ralt+y" and actions[0].get("UILabel") == "@ui_CIEject"
    assert actions[1].find("mouse").get("input") == "mouse3", "a grandchild was lost"
    assert back.find("note").text == "hello"


def _not_cryxml():
    assert not cryxml.is_cryxml(b"<profile/>")
    try:
        cryxml.parse(b"<profile/>")
    except ValueError:
        return
    raise AssertionError("plain XML was accepted as CryXmlB")


def _truncated():
    blob = encode(ET.Element("profile"))
    try:
        cryxml.parse(blob[:-3])
    except ValueError:
        return
    raise AssertionError("a truncated document was accepted")


def _real_file_if_here():
    path = Path(__file__).resolve().parent / "data" / "keybinds" / "_extracted" / "defaultProfile.xml"
    if not path.is_file():
        print("         (skipped: no _extracted/defaultProfile.xml; run extract_defaults.py to include it)")
        return
    root = cryxml.parse(path.read_bytes())
    maps = root.findall(".//actionmap")
    assert len(maps) >= 40, "only %d action maps" % len(maps)
    assert any(m.get("name") == "spaceship_general" for m in maps)


check("attributes, nested children and text all survive", _roundtrip)
check("plain XML is refused, not misread", _not_cryxml)
check("a truncated file is refused", _truncated)
check("the real profile, when extracted, reads as expected", _real_file_if_here)


# --- 2 ---------------------------------------------------------------------

print("\n2. the profile's chords, in the rebind's spelling")


def _to_input():
    cases = [
        (("keyboard", "lalt+e"), "kb1_lalt+e"),
        (("keyboard", "u+lshift"), "kb1_lshift+u"),          # profile puts the modifier last sometimes
        (("keyboard", "ralt+K"), "kb1_ralt+k"),
        (("keyboard", "lshift+lctrl+f"), "kb1_lshift+lctrl+f"),
        (("keyboard", "ralt+mouse2"), "mo1_ralt+mouse2"),      # a mouse button in the keyboard column
        (("keyboard", "lalt+mwheel_up"), "mo1_lalt+mwheel_up"),
        (("mouse", "mouse1"), "mo1_mouse1"),
        (("mouse", "mouse1_2"), "mo1_mouse1_2"),
        (("mouse", "maxis_x"), "mo1_maxis_x"),
        (("keyboard", " "), ""),
        (("keyboard", ""), ""),
        (("mouse", " "), ""),
    ]
    for (device, value), expected in cases:
        got = build_defaults.to_input(device, value)
        assert got == expected, "%s %r -> %r, expected %r" % (device, value, got, expected)


check("modifiers first, device by what the key is", _to_input)


# --- 3 ---------------------------------------------------------------------

print("\n3. the converter")

STRINGS = {
    "ui_CCSpaceFlight": "FLIGHT", "ui_CCFPS": "ON FOOT",
    "ui_CGCockpit": "Vehicles - Cockpit", "ui_CGOnFoot": "On Foot - All",
    "ui_CIToggleDoors": "Open/Close Doors (Toggle)", "ui_CIToggleDoorsDesc": "Toggle Open/Close Doors",
    "ui_CIJump": "Jump",
}


def profile():
    root = ET.Element("profile")
    flight = ET.SubElement(root, "actionmap", {"name": "spaceship_general", "UILabel": "@ui_CGCockpit",
                                               "UICategory": "@ui_CCSpaceFlight"})
    ET.SubElement(flight, "action", {"name": "v_toggle_all_doors", "activationMode": "press",
                                     "keyboard": "k", "mouse": " ", "joystick": " ",
                                     "UILabel": "@ui_CIToggleDoors", "UIDescription": "@ui_CIToggleDoorsDesc"})
    ET.SubElement(flight, "action", {"name": "v_unlabelled", "keyboard": "u+lshift"})
    child = ET.SubElement(flight, "action", {"name": "v_roll_left"})
    ET.SubElement(child, "keyboard", {"input": "q", "activationMode": "double_tap"})
    foot = ET.SubElement(root, "actionmap", {"name": "player", "UILabel": "@ui_CGOnFoot", "UICategory": "@ui_CCFPS"})
    ET.SubElement(foot, "action", {"name": "pl_jump", "keyboard": "space", "UILabel": "@ui_CIJump"})
    ET.SubElement(root, "actionmap", {"name": "mining"})            # no category at all
    return root


def _convert():
    document, labels = build_defaults.convert(profile(), STRINGS, "4.10.193.11644", "sc-alpha-4.10.0")
    assert document.tag == "ActionMaps" and document.get("game") == "4.10.193.11644"
    assert document.get("rebindVersion") == "2", "not the actionmaps schema"
    maps = {m.get("name"): m for m in document.findall("actionmap")}
    assert set(maps) == {"spaceship_general", "player", "mining"}
    doors = maps["spaceship_general"].find("action[@name='v_toggle_all_doors']")
    assert doors.get("activationMode") == "press"
    assert [r.get("input") for r in doors.findall("rebind")] == ["kb1_k", "mo1_ "], \
        "keyboard and mouse both present, the mouse one unbound, no joystick"
    unlabelled = maps["spaceship_general"].find("action[@name='v_unlabelled']")
    assert [r.get("input") for r in unlabelled.findall("rebind")] == ["kb1_lshift+u"]
    roll = maps["spaceship_general"].find("action[@name='v_roll_left']")
    assert [r.get("input") for r in roll.findall("rebind")] == ["kb1_q"], "a child-element binding was missed"
    assert labels["game"] == "4.10.193.11644"
    assert labels["actions"]["v_toggle_all_doors"] == {"label": "Open/Close Doors (Toggle)",
                                                       "description": "Toggle Open/Close Doors"}
    assert "v_unlabelled" not in labels["actions"], "an action with no strings got an entry"
    assert labels["maps"]["spaceship_general"] == {"label": "Vehicles - Cockpit", "category": "FLIGHT", "mode": "Flight"}
    assert labels["maps"]["player"]["mode"] == "FPS"
    assert labels["maps"]["mining"] == {"label": "", "category": "", "mode": "Other"}


def _readable_by_the_app():
    """What the converter writes, the app's reader must read as a full list."""
    from helper.keybinds import is_full_export, read_actionmaps
    root = ET.Element("profile")
    am = ET.SubElement(root, "actionmap", {"name": "player"})
    for i in range(160):
        ET.SubElement(am, "action", {"name": "pl_%d" % i, "keyboard": "f%d" % (i % 12 + 1)})
    document, _ = build_defaults.convert(root, {}, "x", "y")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "defaults.xml"
        path.write_text(ET.tostring(document, encoding="unicode"), encoding="utf-8")
        rows = read_actionmaps(path)
    assert len(rows) == 160 and rows[0].input == "kb1_f1" and rows[0].device == "keyboard"
    assert is_full_export(rows)


def _strings():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "global.ini"
        path.write_bytes("﻿ui_A=Alpha\r\nui_B=Beta = with equals\r\nnot a pair\r\n".encode("utf-8"))
        strings = build_defaults.load_strings(path)
    assert strings == {"ui_A": "Alpha", "ui_B": "Beta = with equals"}, strings
    assert build_defaults.localise(strings, "@ui_A") == "Alpha"
    assert build_defaults.localise(strings, "@ui_missing") == ""
    assert build_defaults.localise(strings, "no at sign") == ""


check("every action, both devices, real names, the map's own category", _convert)
check("and the app reads the result as a full list", _readable_by_the_app)
check("global.ini: BOM, CRLF, a value with an equals sign", _strings)


# --- 4 ---------------------------------------------------------------------

print("\n4. what ships")


def _shipped():
    folder = Path(__file__).resolve().parent / "data" / "keybinds"
    defaults = folder / "defaults.xml"
    labels = folder / "labels.json"
    assert defaults.is_file() and labels.is_file(), "defaults.xml / labels.json missing - run build_defaults.py"
    root = ET.parse(defaults).getroot()
    assert root.tag == "ActionMaps" and root.get("game"), "no game version stamped on defaults.xml"
    actions = root.findall(".//action")
    assert len(actions) >= 900, "only %d actions shipped" % len(actions)
    data = json.loads(labels.read_text(encoding="utf-8"))
    assert data.get("game") == root.get("game"), "labels.json and defaults.xml are from different builds"
    assert len(data.get("actions", {})) >= 500


check("defaults.xml and labels.json are there, and from one build", _shipped)


print("\n%s  (%d passed, %d failed)"
      % ("FAILED" if FAILED else "DEFAULTS VERIFIED", PASSED, FAILED))
sys.exit(1 if FAILED else 0)
