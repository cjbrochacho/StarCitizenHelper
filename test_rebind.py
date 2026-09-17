"""What changing a binding must not get wrong:

    python test_rebind.py

No framework and no dependencies, like the rest of this project. Every file
is written into a temporary folder; the game, its console and the keyboard
are never touched.

The case it exists for: the app writes one file and types one command, and
the game does the rest. If the file is not in the shape the game loads, the
command does nothing - or worse, `pp_rebindkeys` with nothing after it
resets every binding the player has. So the file's shape is pinned here
line by line, and the command is refused when it would be empty.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helper import keybinds
from helper.keybinds import Binding, Rebind, conflicts, read_actionmaps
from helper.rebind import (BACKUP_KEEP, CSV_COLUMNS, HELPER_MAPPING, backup_actionmaps,
                           backup_stamp, command_for, default_for, is_at_default,
                           list_backups, newest_backup, plan_change, to_csv,
                           validate_import, write_mapping)

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


def B(actionmap, action, raw, source="default", **kw):
    dev = kw.pop("device", None)
    if dev is None:
        dev = keybinds.device(raw) or ("mouse" if raw.startswith("mo") else "keyboard")
    return Binding(actionmap, action, raw, source, device=dev, **kw)


TABLE = [
    B("spaceship_general", "v_a", "kb1_k"), B("spaceship_general", "v_a", "", device="mouse"),
    B("spaceship_general", "v_b", "", device="keyboard"),
    B("spaceship_general", "v_c", "mo1_mouse2", device="mouse"),
    B("player", "pl_d", "", "rebind", device="keyboard"),
    B("player", "pl_e", "kb1_lshift+lctrl+f", "rebind"),
    B("player", "pl_f", "kb1_k", activation="hold"),
]

REAL = """<ActionMaps>
 <ActionProfiles version="1" optionsVersion="2" rebindVersion="2" profileName="default">
  <options type="keyboard" instance="1" Product="Keyboard  {6F1D2B61-D5A0-11CF-BFC7-444553540000}"/>
  <modifiers />
  <actionmap name="seat_general">
   <action name="v_enter_remote_turret_1"><rebind input="kb1_slash"/></action>
   <action name="v_enter_remote_turret_2"><rebind input="kb1_slash"/></action>
   <action name="v_enter_remote_turret_3"><rebind input="kb1_slash"/></action>
  </actionmap>
  <actionmap name="spaceship_general">
   <action name="v_toggle_all_doorlocks"><rebind input="kb1_rctrl+k"/></action>
   <action name="v_toggle_all_doors"><rebind input="kb1_lalt+k"/></action>
  </actionmap>
 </ActionProfiles>
</ActionMaps>
"""

HEADER = [
    '<ActionMaps version="1" optionsVersion="2" rebindVersion="2" profileName="default">',
    ' <CustomisationUIHeader label="default" description="default" image="">',
    '  <devices>', '   <keyboard instance="1"/>', '   <mouse instance="1"/>', '  </devices>',
    '  <categories/>', ' </CustomisationUIHeader>',
    ' <options type="keyboard" instance="1" Product="Keyboard  {6F1D2B61-D5A0-11CF-BFC7-444553540000}"/>',
    ' <modifiers />',
]


class Tmp:
    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        return self.dir

    def __exit__(self, *exc):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


# --- 1 ---------------------------------------------------------------------

print("\n1. the file the game loads")


def _shape():
    with Tmp() as tmp:
        path = write_mapping(tmp / "m.xml", [Rebind("spaceship_general", "v_toggle_all_doors", "kb1_lalt+k",
                                                    device="keyboard")])
        data = path.read_bytes()
        assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), "not CRLF throughout"
        assert not data.startswith(b"\xef\xbb\xbf") and not data.startswith(b"<?xml"), "the game writes neither"
        lines = data.decode("utf-8").split("\r\n")
        assert lines[:len(HEADER)] == HEADER, "\n".join(lines[:len(HEADER)])
        assert lines[len(HEADER):] == [
            ' <actionmap name="spaceship_general">',
            '  <action name="v_toggle_all_doors">',
            '   <rebind input="kb1_lalt+k"/>',
            '  </action>', ' </actionmap>', '</ActionMaps>', ''], lines[len(HEADER):]
        assert not list(tmp.glob("*.part")), "a .part was left behind"


def _round_trip():
    rows = [
        Rebind("spaceship_general", "v_a", "kb1_k", device="keyboard"),
        Rebind("spaceship_general", "v_a", "", device="mouse"),
        Rebind("player", "pl_x", "", device="keyboard"),
        Rebind("player", "pl_y", "kb1_lshift+u", activation="hold", multitap=2, device="keyboard"),
        Rebind("player", "pl_y", "mo1_mouse3", activation="hold", multitap=2, device="mouse"),
    ]
    with Tmp() as tmp:
        back = read_actionmaps(write_mapping(tmp / "m.xml", rows))
    assert [(r.actionmap, r.action, r.input, r.device, r.activation, r.multitap) for r in back] == \
           [(r.actionmap, r.action, r.input, r.device, r.activation, r.multitap) for r in rows], back


def _bindings_too():
    with Tmp() as tmp:
        back = read_actionmaps(write_mapping(tmp / "m.xml", TABLE))
    assert len(back) == len(TABLE) and back[1].input == "" and back[1].device == "mouse"


def _refusals():
    with Tmp() as tmp:
        for bad in ([], [Rebind("player", "pl_x", "", device="")], [Rebind("", "pl_x", "kb1_a", device="keyboard")]):
            try:
                write_mapping(tmp / "m.xml", bad)
            except ValueError:
                continue
            raise AssertionError("accepted %r" % (bad,))
        assert not (tmp / "m.xml").exists()


def _escaping():
    with Tmp() as tmp:
        path = write_mapping(tmp / "m.xml", [Rebind("player", 'pl_"x"', "kb1_<", device="keyboard")])
        text = path.read_text(encoding="utf-8")
        assert "&lt;" in text and ('name=\'pl_"x"\'' in text or "&quot;" in text), text
        assert read_actionmaps(path)[0].input == "kb1_<"


def _extra_devices():
    with Tmp() as tmp:
        text = write_mapping(tmp / "m.xml", [Rebind("player", "pl_x", "js1_button3", device="joystick")]
                             ).read_text(encoding="utf-8")
        assert '   <joystick instance="1"/>' in text and '   <keyboard instance="1"/>' in text


check("the header and body, line for line, CRLF, no declaration", _shape)
check("every row comes back the same: unbound, activation, taps, two devices", _round_trip)
check("Binding rows are accepted as well as Rebind rows", _bindings_too)
check("nothing, a deviceless blank, and a nameless row are refused", _refusals)
check("a quote or a bracket in a name is escaped and read back", _escaping)
check("a joystick row declares the joystick", _extra_devices)


# --- 2 ---------------------------------------------------------------------

print("\n2. the command")


def _command():
    assert command_for(HELPER_MAPPING) == "pp_rebindkeys layout_sch_helper_exported.xml"
    assert command_for("  layout_x.xml ") == "pp_rebindkeys layout_x.xml"
    for bad in ("", "   ", None, "a b", "dir/x.xml", "dir\\x.xml"):
        try:
            command_for(bad)
        except ValueError:
            continue
        raise AssertionError("built a command from %r" % (bad,))


check("named, or refused - never bare", _command)


# --- 3 ---------------------------------------------------------------------

print("\n3. backups")


def _backup():
    with Tmp() as tmp:
        source = tmp / "actionmaps.xml"
        source.write_text(REAL)
        made = backup_actionmaps(source, tmp / "backups")
        assert made is not None and made.parent == tmp / "backups"
        assert made.name.startswith("actionmaps-") and made.name.endswith(".xml"), made.name
        assert made.read_text() == REAL
        assert newest_backup(tmp / "backups") == made
        stamp = backup_stamp(made)
        assert len(stamp) == 16 and stamp[4] == "-" and stamp[10] == " ", stamp


def _backup_missing():
    with Tmp() as tmp:
        assert backup_actionmaps(tmp / "nope.xml", tmp / "backups") is None
        assert backup_actionmaps(None, tmp / "backups") is None
        assert newest_backup(tmp / "backups") is None


def _backup_prune():
    with Tmp() as tmp:
        source = tmp / "actionmaps.xml"
        source.write_text(REAL)
        folder = tmp / "backups"
        folder.mkdir()
        for i in range(BACKUP_KEEP + 2):                 # twelve old ones, dated apart
            (folder / ("actionmaps-20260901-%06d.xml" % i)).write_text("old %d" % i)
        made = backup_actionmaps(source, folder)
        kept = list_backups(folder)
        assert len(kept) == BACKUP_KEEP, [p.name for p in kept]
        assert kept[0] == made, "the newest should be first"
        assert not (folder / "actionmaps-20260901-000000.xml").exists(), "the oldest survived"
        assert not (folder / "actionmaps-20260901-000002.xml").exists()
        assert (folder / "actionmaps-20260901-000011.xml").exists()


def _backup_same_second():
    with Tmp() as tmp:
        source = tmp / "actionmaps.xml"
        source.write_text(REAL)
        first = backup_actionmaps(source, tmp / "backups")
        second = backup_actionmaps(source, tmp / "backups")
        assert first != second and second.exists() and first.exists()


check("a dated copy, found as the newest, with a readable stamp", _backup)
check("nothing to back up is None, not an error", _backup_missing)
check("only the newest ten are kept", _backup_prune)
check("two in one second get two names", _backup_same_second)


# --- 4 ---------------------------------------------------------------------

print("\n4. planning a change")


def _plan():
    keybinds._labels_cache = {}
    try:
        change = plan_change(TABLE, "spaceship_general", "v_b", "kb1_k")
        assert change.old_input == "" and change.new_input == "kb1_k"
        assert change.conflicts == (("spaceship_general", "v_a"),), change.conflicts
        assert change.rows == (Rebind("spaceship_general", "v_b", "kb1_k", device="keyboard"),)
        assert change.label == "B"
        other_mode = plan_change(TABLE, "player", "pl_d", "kb1_k")
        assert other_mode.conflicts == (("player", "pl_f"),), "the FPS clash, not the Flight one"
        unbind = plan_change(TABLE, "spaceship_general", "v_a", "")
        assert unbind.old_input == "kb1_k" and unbind.conflicts == ()
        new = plan_change(TABLE, "debug", "dbg_z", "kb1_f9")
        assert new.old_input == "" and new.conflicts == ()
    finally:
        keybinds._labels_cache = None


def _defaults():
    defaults = [Rebind("spaceship_general", "v_a", "kb1_lshift+lctrl+k", device="keyboard"),
                Rebind("spaceship_general", "v_a", "", device="mouse")]
    assert default_for(defaults, "spaceship_general", "v_a") == "kb1_lshift+lctrl+k"
    assert default_for(defaults, "spaceship_general", "v_a", "mouse") == ""
    assert default_for(defaults, "player", "pl_x") == ""
    assert is_at_default(defaults, B("spaceship_general", "v_a", "kb1_lctrl+lshift+k", "rebind"))
    assert not is_at_default(defaults, B("spaceship_general", "v_a", "kb1_k", "rebind"))
    assert is_at_default(defaults, B("player", "pl_x", "", device="keyboard"))


check("what would change, and what it would collide with", _plan)
check("the shipped default, whatever order the modifiers are in", _defaults)


# --- 5 ---------------------------------------------------------------------

print("\n5. the spreadsheet")


def _csv():
    keybinds._labels_cache = {"actions": {"v_a": {"label": "Doors, all"}},
                              "maps": {"spaceship_general": {"label": "Vehicles - Cockpit", "mode": "Flight"}}}
    try:
        text = to_csv(TABLE, conflicts(TABLE + [B("spaceship_general", "v_z", "kb1_k", "rebind")]))
    finally:
        keybinds._labels_cache = None
    lines = text.split("\r\n")
    assert lines[0] == ",".join(CSV_COLUMNS), lines[0]
    assert lines[1].startswith('Flight,"Doors, all",v_a,K,kb1_k,Vehicles - Cockpit,spaceship_general,keyboard,,1,default,yes'), lines[1]
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    assert len(lines) == len(TABLE) + 2                # header + rows + trailing empty


check("a header, one row per binding, a comma in a label quoted", _csv)


# --- 6 ---------------------------------------------------------------------

print("\n6. a file somebody hands over")


def _import_real():
    with Tmp() as tmp:
        path = tmp / "backup.xml"
        path.write_text(REAL)
        known = {(r.actionmap, r.action) for r in TABLE} | {("seat_general", "v_enter_remote_turret_1")}
        summary = validate_import(path, TABLE, known)
    assert summary.error == "", summary.error
    assert len(summary.rows) == 5 and summary.actions == 5 and summary.maps == 2 and summary.unbound == 0
    assert ("spaceship_general", "v_toggle_all_doors") in summary.unknown
    assert ("seat_general", "v_enter_remote_turret_1") not in summary.unknown
    assert ("Flight", "kb1_slash") in summary.conflicts, "three turrets on one key"


def _import_layout():
    with Tmp() as tmp:
        path = write_mapping(tmp / "layout.xml", [Rebind("player", "pl_x", "kb1_k", device="keyboard"),
                                                  Rebind("player", "pl_y", "", device="keyboard")])
        summary = validate_import(path, TABLE, set())
    assert summary.error == "" and summary.unbound == 1 and summary.unknown == []
    assert ("FPS", "kb1_k") in summary.conflicts, "pl_f already has K in FPS"


def _import_bad():
    with Tmp() as tmp:
        garbled = tmp / "g.xml"
        garbled.write_text("<ActionMaps><actionmap")
        assert "well-formed" in validate_import(garbled, TABLE, set()).error
        wrong = tmp / "w.xml"
        wrong.write_text("<Settings><x/></Settings>")
        assert "<Settings>" in validate_import(wrong, TABLE, set()).error
        empty = tmp / "e.xml"
        empty.write_text("<ActionMaps><actionmap name='x'/></ActionMaps>")
        assert "no bindings" in validate_import(empty, TABLE, set()).error
        assert "read" in validate_import(tmp / "missing.xml", TABLE, set()).error


check("an actionmaps.xml backup: counted, the unknown named, the clashes found", _import_real)
check("a mapping file: the same", _import_layout)
check("garbled, wrong, empty, missing: each refused with a reason", _import_bad)


print("\n%s  (%d passed, %d failed)"
      % ("FAILED" if FAILED else "REBIND VERIFIED", PASSED, FAILED))
sys.exit(1 if FAILED else 0)
