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

from helper.keybinds import (Rebind, describe_action, describe_input, describe_status,
                             device, find_actionmaps, mode_of, read_rebinds)

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
    assert rows[4] == Rebind("spaceship_general", "v_toggle_all_doors", "kb1_lalt+k")


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
    assert describe_action("v_toggle_all_doors") == "Toggle all doors"      # override
    assert describe_action("v_strafe_forward") == "Strafe forward"          # prefix + sentence case
    assert describe_action("pl_hud_toggle") == "HUD toggle"                 # kept upper
    assert describe_action("ui_something_new") == "Something new"
    assert describe_action("weird") == "Weird"
    assert describe_action("") == ""


check("overrides first, then a readable guess", _actions)


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

print("\n6. the status line")


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
