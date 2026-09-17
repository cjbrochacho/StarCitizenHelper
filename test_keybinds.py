"""What the key bindings reader must not get wrong:

    python test_keybinds.py

No framework and no dependencies, like the rest of this project. It reads
fabricated actionmaps.xml files from temporary folders; the game is never
needed, and nothing here opens a window.

The case it exists for: actionmaps.xml holds only what the player changed,
in the game's own shorthand - "kb1_lalt+k" - and the Key Bindings tab shows
it as "Alt+K" next to a readable action name. A wrong table there is a lie
told with confidence, so every spelling this module claims to understand is
written down here.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helper import keybinds
from helper.keybinds import (Binding, Rebind, conflicts, describe_action, describe_input,
                             describe_status, device, find_actionmaps, find_exports,
                             is_full_export, load_full_export, load_shipped_defaults, merge, mode_of,
                             read_actionmaps, read_rebinds)

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


#: The real file from this machine, verbatim.
REAL = """<ActionMaps>
 <ActionProfiles version="1" optionsVersion="2" rebindVersion="2" profileName="default">
  <options type="keyboard" instance="1" Product="Keyboard  {6F1D2B61-D5A0-11CF-BFC7-444553540000}"/>
  <options type="gamepad" instance="1" Product="Controller (Gamepad)"/>
  <options type="joystick" instance="1"/>
  <modifiers />
  <actionmap name="seat_general">
   <action name="v_enter_remote_turret_1">
    <rebind input="kb1_slash"/>
   </action>
   <action name="v_enter_remote_turret_2">
    <rebind input="kb1_slash"/>
   </action>
   <action name="v_enter_remote_turret_3">
    <rebind input="kb1_slash"/>
   </action>
  </actionmap>
  <actionmap name="spaceship_general">
   <action name="v_toggle_all_doorlocks">
    <rebind input="kb1_rctrl+k"/>
   </action>
   <action name="v_toggle_all_doors">
    <rebind input="kb1_lalt+k"/>
   </action>
  </actionmap>
 </ActionProfiles>
</ActionMaps>
"""


#: A slice of what `pp_rebindkeys export all` writes: the same format, every
#: action, the unbound ones as a bare device.
EXPORT = """<ActionMaps>
 <ActionProfiles version="1" optionsVersion="2" rebindVersion="2" profileName="default">
  <actionmap name="spaceship_general">
   <action name="v_toggle_all_doors"><rebind input="kb1_k"/><rebind input="mo1_ "/></action>
   <action name="v_toggle_landing_system"><rebind input="kb1_n"/></action>
  </actionmap>
  <actionmap name="seat_general">
   <action name="v_enter_remote_turret_1"><rebind input="kb1_ "/></action>
  </actionmap>
  <actionmap name="player">
   <action name="pl_jump"><rebind input="kb1_space"/></action>
   <action name="pl_fire"><rebind input="mo1_mouse1"/></action>
  </actionmap>
 </ActionProfiles>
</ActionMaps>
"""


def export_rows():
    path = written(EXPORT)
    try:
        return read_actionmaps(path)
    finally:
        path.unlink()


def big_export(count):
    """`count` distinct actions, for the size threshold."""
    return [Rebind("player", "pl_%d" % i, "kb1_a", device="keyboard") for i in range(count)]


def written(text):
    """A temporary actionmaps.xml holding `text`; the caller cleans up."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return Path(handle.name)


# --- 1 ---------------------------------------------------------------------

print("\n1. reading the file")


def _real():
    path = written(REAL)
    try:
        rows = read_rebinds(path)
    finally:
        path.unlink()
    assert len(rows) == 5, rows
    assert [r.actionmap for r in rows] == ["seat_general"] * 3 + ["spaceship_general"] * 2
    assert [r.input for r in rows] == ["kb1_slash"] * 3 + ["kb1_rctrl+k", "kb1_lalt+k"]
    assert rows[4] == Rebind("spaceship_general", "v_toggle_all_doors", "kb1_lalt+k",
                             device="keyboard")


def _empty():
    path = written("")
    try:
        assert read_rebinds(path) == []
    finally:
        path.unlink()


def _missing():
    assert read_rebinds(Path(tempfile.gettempdir()) / "no-such-actionmaps.xml") == []
    assert read_rebinds(None) == []


def _garbled():
    path = written("<ActionMaps><actionmap name='x'><action")
    try:
        assert read_rebinds(path) == []
    finally:
        path.unlink()


def _cleared():
    path = written('<ActionMaps><actionmap name="player"><action name="pl_x">'
                   '<rebind input=" "/><rebind input="kb1_"/></action></actionmap></ActionMaps>')
    try:
        rows = read_rebinds(path)
    finally:
        path.unlink()
    assert [r.input for r in rows] == ["", ""], rows
    assert [r.device for r in rows] == ["", "keyboard"], "the device of a cleared binding was lost"


def _two_devices():
    path = written('<ActionMaps><actionmap name="player"><action name="pl_fire" multiTap="2" '
                   'activationMode="press"><rebind input="kb1_f"/>'
                   '<rebind input="mo1_mouse1" activationMode="hold"/></action></actionmap></ActionMaps>')
    try:
        rows = read_rebinds(path)
    finally:
        path.unlink()
    assert len(rows) == 2 and all(r.action == "pl_fire" for r in rows)
    assert rows[0].activation == "press" and rows[1].activation == "hold"
    assert rows[0].multitap == 2 and rows[1].multitap == 2


check("the real file: five rebinds, in order, with their inputs", _real)
check("an empty file is nothing", _empty)
check("a missing file is nothing", _missing)
check("a garbled file is nothing, not a traceback", _garbled)
check("a cleared binding reads as unbound", _cleared)
check("two devices on one action are two rows, with activation and multitap", _two_devices)


# --- 2 ---------------------------------------------------------------------

print("\n2. naming what is bound")


def _inputs():
    cases = [
        ("kb1_lalt+k", "Alt+K"),
        ("kb1_rctrl+k", "RCtrl+K"),
        ("kb1_lshift+lctrl+f", "Shift+Ctrl+F"),
        ("kb1_slash", "/"),
        ("kb1_backslash", "\\"),
        ("kb1_np_1", "Num 1"),
        ("kb1_np_add", "Num +"),
        ("kb1_f12", "F12"),
        ("kb1_pgup", "Page Up"),
        ("kb1_space", "Space"),
        ("mo1_mouse1", "Mouse 1"),
        ("mo1_button1", "Mouse 1"),
        ("mo1_mwheel_up", "Wheel Up"),
        ("mo1_lalt+mouse2", "Alt+Mouse 2"),
        ("js1_button3", "Joy1 B3"),
        ("js2_rotx", "Joy2 RotX"),
        ("js1_hat1_up", "Joy1 Hat1 Up"),
        ("gp1_shoulderl", "Pad Shoulderl"),
        ("kb1_frobnicate", "Frobnicate"),
        ("kb1_", ""),
        ("", ""),
        (None, ""),
    ]
    for raw, expected in cases:
        got = describe_input(raw)
        assert got == expected, "%r -> %r, expected %r" % (raw, got, expected)


def _devices():
    assert device("kb1_k") == "keyboard"
    assert device("mo1_mouse1") == "mouse"
    assert device("js3_button1") == "joystick"
    assert device("gp1_a") == "gamepad"
    assert device("") == "" and device("junk") == ""


check("the shorthand, spelled out", _inputs)
check("and which device each belongs to", _devices)


# --- 3 ---------------------------------------------------------------------

print("\n3. naming the action")


def _actions():
    keybinds._labels_cache = {}                     # no shipped names: the guess alone
    try:
        assert describe_action("v_toggle_all_doors") == "Toggle all doors"      # override
        assert describe_action("v_strafe_forward") == "Strafe forward"          # prefix + sentence case
        assert describe_action("pl_hud_toggle") == "HUD toggle"                 # kept upper
        assert describe_action("ui_something_new") == "Something new"
        assert describe_action("weird") == "Weird"
        assert describe_action("") == ""
    finally:
        keybinds._labels_cache = None


def _labels_first():
    keybinds._labels_cache = {"actions": {"v_x": {"label": "The game's own name"}},
                              "maps": {"weird_map": {"mode": "FPS"}}}
    try:
        assert describe_action("v_x") == "The game's own name"
        assert describe_action("v_toggle_all_doors") == "Toggle all doors", "fell through to the override"
        assert mode_of("weird_map") == "FPS", "the game's category should win over the prefix guess"
        assert mode_of("spaceship_x") == "Flight", "and the prefix guess still stands in"
    finally:
        keybinds._labels_cache = None


def _shipped():
    path, rows, game = load_shipped_defaults()
    assert path is not None and len(rows) > 1000, "the shipped defaults are missing or thin"
    assert game and game[0].isdigit(), game
    assert describe_action("v_toggle_all_doors") != "Toggle all doors", "labels.json is not being read"


check("with no shipped names: overrides first, then a readable guess", _actions)
check("with shipped names: the game's own, then the rest", _labels_first)
check("the defaults that ship are real and named", _shipped)


# --- 4 ---------------------------------------------------------------------

print("\n4. which page of the sheet")


def _modes():
    for name, expected in [("spaceship_general", "Flight"), ("seat_general", "Flight"),
                           ("mining", "Flight"), ("turret_main", "Flight"),
                           ("player", "FPS"), ("prone", "FPS"), ("zero_gravity_eva", "FPS"),
                           ("mapui", "FPS"), ("default", "Other"), ("debug", "Other"),
                           ("", "Other"), ("something_else", "Other")]:
        got = mode_of(name)
        assert got == expected, "%r -> %r, expected %r" % (name, got, expected)


check("flight, FPS, or neither", _modes)


# --- 5 ---------------------------------------------------------------------

print("\n5. finding the file")


def _profile(live, client, with_actionmaps=True, age=0):
    folder = live / "USER" / "client" / str(client) / "Profiles" / "default"
    folder.mkdir(parents=True)
    attributes = folder / "attributes.xml"
    attributes.write_text("<Attributes/>")
    if with_actionmaps:
        (folder / "actionmaps.xml").write_text(REAL)
    stamp = time.time() - age
    os.utime(attributes, (stamp, stamp))
    return folder


def _newest_client():
    with tempfile.TemporaryDirectory() as tmp:
        live = Path(tmp)
        _profile(live, 0, age=3600)
        newer = _profile(live, 1)
        assert find_actionmaps(live) == newer / "actionmaps.xml"


def _no_user():
    with tempfile.TemporaryDirectory() as tmp:
        assert find_actionmaps(Path(tmp)) is None
    assert find_actionmaps(None) is None


def _newest_without():
    with tempfile.TemporaryDirectory() as tmp:
        live = Path(tmp)
        _profile(live, 0, age=3600)
        _profile(live, 1, with_actionmaps=False)
        assert find_actionmaps(live) is None, "fell back to an older profile"


check("the newest client profile wins", _newest_client)
check("no profile, no file", _no_user)
check("the newest profile without one is None, not an older one", _newest_without)


# --- 6 ---------------------------------------------------------------------

print("\n6. reading a full export")


def _export_rows():
    rows = export_rows()
    assert [(r.action, r.input, r.device) for r in rows] == [
        ("v_toggle_all_doors", "kb1_k", "keyboard"),
        ("v_toggle_all_doors", "", "mouse"),
        ("v_toggle_landing_system", "kb1_n", "keyboard"),
        ("v_enter_remote_turret_1", "", "keyboard"),
        ("pl_jump", "kb1_space", "keyboard"),
        ("pl_fire", "mo1_mouse1", "mouse"),
    ], rows


def _threshold():
    assert not is_full_export(big_export(149))
    assert is_full_export(big_export(150))
    real = written(REAL)
    try:
        assert not is_full_export(read_actionmaps(real)), "five rebinds are not a full export"
    finally:
        real.unlink()


def _exports_newest_first():
    with tempfile.TemporaryDirectory() as tmp:
        live = Path(tmp)
        _profile(live, 0)
        folder = live / "USER" / "client" / "0" / "Controls" / "Mappings"
        folder.mkdir(parents=True)
        older = folder / "layout_old_exported.xml"
        newer = folder / "layout_new_exported.xml"
        older.write_text(EXPORT)
        newer.write_text(EXPORT)
        stamp = time.time() - 3600
        os.utime(older, (stamp, stamp))
        (folder / "not_an_export.xml").write_text("<x/>")
        assert find_exports(live) == [newer, older]


def _exports_none():
    with tempfile.TemporaryDirectory() as tmp:
        live = Path(tmp)
        _profile(live, 0)
        assert find_exports(live) == [], "no Mappings folder should mean no exports"
    assert find_exports(None) == []


def _load_full():
    with tempfile.TemporaryDirectory() as tmp:
        live = Path(tmp)
        _profile(live, 0)
        folder = live / "USER" / "client" / "0" / "Controls" / "Mappings"
        folder.mkdir(parents=True)
        small = folder / "layout_small_exported.xml"
        small.write_text(EXPORT)                       # newest, but only five actions
        big = folder / "layout_big_exported.xml"
        actions = "".join('<action name="pl_%d"><rebind input="kb1_a"/></action>' % i for i in range(200))
        big.write_text('<ActionMaps><actionmap name="player">%s</actionmap></ActionMaps>' % actions)
        stamp = time.time() - 3600
        os.utime(big, (stamp, stamp))
        path, rows = load_full_export(live)
        assert path == big, "a small newer file was preferred over the full older one"
        assert len(rows) == 200
        small.unlink()
        big.unlink()
        assert load_full_export(live) == (None, [])


check("every row, in order, with its device - cleared ones included", _export_rows)
check("a full export has at least 150 actions", _threshold)
check("exports are found newest first, and only real ones", _exports_newest_first)
check("no folder, no exports", _exports_none)
check("the newest full export wins, whatever else is there", _load_full)


# --- 7 ---------------------------------------------------------------------

print("\n7. laying the player's rebinds over the export")


def _override():
    rows = merge(export_rows(), [Rebind("spaceship_general", "v_toggle_all_doors", "kb1_lalt+k", device="keyboard")])
    assert rows[0] == Binding("spaceship_general", "v_toggle_all_doors", "kb1_lalt+k", "rebind", device="keyboard")
    assert rows[1].source == "default" and rows[1].device == "mouse", "the mouse row was touched"
    assert len(rows) == 6, "a replacement changed the row count"


def _cleared_stays():
    rows = merge(export_rows(), [Rebind("player", "pl_jump", "", device="keyboard")])
    jump = [r for r in rows if r.action == "pl_jump"]
    assert len(jump) == 1 and jump[0].input == "" and jump[0].source == "rebind", jump


def _extra_appended():
    rows = merge(export_rows(), [Rebind("player", "pl_new_thing", "kb1_x", device="keyboard")])
    assert rows[-1] == Binding("player", "pl_new_thing", "kb1_x", "rebind", device="keyboard")
    assert [r.action for r in rows[:-1]] == [r.action for r in export_rows()], "order changed"


def _last_wins():
    rows = merge(export_rows(), [
        Rebind("player", "pl_jump", "kb1_a", device="keyboard"),
        Rebind("player", "pl_jump", "kb1_b", device="keyboard"),
    ])
    jump = [r for r in rows if r.action == "pl_jump"]
    assert len(jump) == 1 and jump[0].input == "kb1_b", jump


def _no_export():
    rebinds = [Rebind("player", "pl_jump", "kb1_a", device="keyboard"),
               Rebind("player", "pl_fire", "kb1_b", device="keyboard")]
    rows = merge([], rebinds)
    assert [(r.action, r.source) for r in rows] == [("pl_jump", "rebind"), ("pl_fire", "rebind")]


check("a rebind replaces its row in place, other devices untouched", _override)
check("a cleared binding stays, empty and marked as the player's", _cleared_stays)
check("an action the export never heard of goes on the end", _extra_appended)
check("two rebinds for one device: the later one", _last_wins)
check("with no export, the rebinds alone", _no_export)


# --- 8 ---------------------------------------------------------------------

print("\n8. conflicts")


def _conflict():
    rows = [Binding("spaceship_general", "v_a", "kb1_k", "default", device="keyboard"),
            Binding("spaceship_weapons", "v_b", "kb1_k", "rebind", device="keyboard")]
    found = conflicts(rows)
    assert found == {("Flight", "kb1_k"): [("spaceship_general", "v_a"), ("spaceship_weapons", "v_b")]}, found


def _defaults_only():
    rows = [Binding("spaceship_general", "v_a", "kb1_k", "default", device="keyboard"),
            Binding("mining", "v_b", "kb1_k", "default", device="keyboard")]
    assert conflicts(rows) == {}, "the game's own shared keys are not conflicts"


def _across_modes():
    rows = [Binding("spaceship_general", "v_a", "kb1_k", "rebind", device="keyboard"),
            Binding("player", "pl_b", "kb1_k", "rebind", device="keyboard")]
    assert conflicts(rows) == {}, "flight and FPS are never active together"


def _ignored_inputs():
    rows = [Binding("player", "pl_a", "", "rebind", device="keyboard"),
            Binding("player", "pl_b", "", "rebind", device="keyboard"),
            Binding("player", "pl_c", "js1_button1", "rebind", device="joystick"),
            Binding("player", "pl_d", "js1_button1", "rebind", device="joystick")]
    assert conflicts(rows) == {}


def _real_turrets():
    real = written(REAL)
    try:
        rows = merge([], read_actionmaps(real))
    finally:
        real.unlink()
    found = conflicts(rows)
    assert list(found) == [("Flight", "kb1_slash")], found
    assert len(found[("Flight", "kb1_slash")]) == 3


check("one key, two actions, one of them the player's", _conflict)
check("defaults sharing a key are left alone", _defaults_only)
check("the same key in flight and on foot is fine", _across_modes)
check("unbound and joystick inputs never count", _ignored_inputs)
check("the real file: three turrets on one key", _real_turrets)


# --- 9 ---------------------------------------------------------------------

print("\n9. the status line")


def _status():
    assert "not found" in describe_status(None, [])
    path = Path("C:/x/actionmaps.xml")
    assert "no custom bindings" in describe_status(path, [])
    one = [Rebind("player", "pl_x", "kb1_x")]
    assert describe_status(path, one).endswith("1 rebind")
    assert describe_status(path, one * 3).endswith("3 rebinds")


check("three answers, one per situation", _status)


print("\n%s  (%d passed, %d failed)"
      % ("FAILED" if FAILED else "KEYBINDS VERIFIED", PASSED, FAILED))
sys.exit(1 if FAILED else 0)
