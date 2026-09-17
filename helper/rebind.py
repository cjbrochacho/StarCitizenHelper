"""Changing a binding: what is written, and what the game is told.

The game keeps the player's bindings in actionmaps.xml and rewrites that
file itself from its options screen, so nothing here touches it directly.
The route the game offers - the one SCJMapper and Joystick Gremlin use - is
a mapping file in USER\\client\\<n>\\Controls\\Mappings\\ and the console
command `pp_rebindkeys <name>`, which loads it live and saves the result.
That is all this module does: write such a file, in the shape the game's
own exports have, and produce the command.

One rule above the rest: `pp_rebindkeys` with no argument resets every
binding to its default. command_for refuses an empty name.

The file the game writes (verified against a real export) is CRLF, one
space of indent per level, no XML declaration, no BOM; write_mapping does
the same by hand rather than through ElementTree, which would do none of
those. Any bindings file the player hands over - an actionmaps.xml backup,
a game export, a mapping someone shared - is read with the reader the app
already has and written back out in this one shape, so the game is only
ever asked to load a file we know it loads.

Nothing here touches Tk or the keyboard.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

from helper.keybinds import (Binding, Rebind, chord_key, conflicts, describe_action,
                             describe_input, map_label, merge, mode_of, read_actionmaps)

#: The one mapping file this app writes; the command names it, never user text.
HELPER_MAPPING = "layout_sch_helper_exported.xml"
BACKUP_KEEP = 10

_RE_BACKUP = re.compile(r"^actionmaps-(\d{8})-(\d{6})(?:-\d+)?\.xml$")
_KEYBOARD_OPTIONS = ('<options type="keyboard" instance="1" '
                     'Product="Keyboard  {6F1D2B61-D5A0-11CF-BFC7-444553540000}"/>')
_DEVICE_TAGS = {"keyboard": "keyboard", "mouse": "mouse", "joystick": "joystick", "gamepad": "gamepad"}
_UNBOUND = {"keyboard": "kb1_ ", "mouse": "mo1_ ", "joystick": "js1_ ", "gamepad": "gp1_ "}


# --- the command -------------------------------------------------------------

def command_for(name: str) -> str:
    """'layout_x.xml' -> 'pp_rebindkeys layout_x.xml'. Never for nothing."""
    name = (name or "").strip()
    if not name or any(c in name for c in " \t\r\n/\\"):
        raise ValueError("a mapping file name is needed: %r" % name)
    return "pp_rebindkeys " + name


# --- the file ----------------------------------------------------------------

def write_mapping(path, rows, profile_name: str = "default") -> Path:
    """Write `rows` (Rebind or Binding) as a mapping file the game loads.

    One <action> per (actionmap, action) in first-seen order, one <rebind>
    per row; an unbound row is the bare device the game uses to mean that.
    Atomic: a .part beside the target, then a rename.
    """
    rows = list(rows)
    if not rows:
        raise ValueError("nothing to write")
    groups: dict[tuple[str, str], list] = {}
    devices: list[str] = []
    for r in rows:
        if not r.actionmap or not r.action:
            raise ValueError("a row with no actionmap or action: %r" % (r,))
        if not r.input and r.device not in _UNBOUND:
            raise ValueError("a row with neither an input nor a device: %r" % (r,))
        groups.setdefault((r.actionmap, r.action), []).append(r)
        device = r.device or _device_of(r.input)
        if device and device not in devices:
            devices.append(device)
    for device in ("keyboard", "mouse"):
        if device not in devices:
            devices.insert(("keyboard", "mouse").index(device), device)

    lines = [
        '<ActionMaps version="1" optionsVersion="2" rebindVersion="2" profileName=%s>'
        % quoteattr(profile_name),
        ' <CustomisationUIHeader label=%s description=%s image="">'
        % (quoteattr(profile_name), quoteattr(profile_name)),
        "  <devices>",
    ]
    for device in devices:
        lines.append('   <%s instance="1"/>' % _DEVICE_TAGS[device])
    lines += ["  </devices>", "  <categories/>", " </CustomisationUIHeader>",
              " " + _KEYBOARD_OPTIONS, " <modifiers />"]
    current_map = None
    for (actionmap, action), members in groups.items():
        if actionmap != current_map:
            if current_map is not None:
                lines.append(" </actionmap>")
            lines.append(" <actionmap name=%s>" % quoteattr(actionmap))
            current_map = actionmap
        first = members[0]
        attributes = "name=%s" % quoteattr(action)
        if getattr(first, "activation", ""):
            attributes += " activationMode=%s" % quoteattr(first.activation)
        if getattr(first, "multitap", 1) > 1:
            attributes += ' multiTap="%d"' % first.multitap
        lines.append("  <action %s>" % attributes)
        for r in members:
            token = r.input or _UNBOUND[r.device]
            lines.append("   <rebind input=%s/>" % quoteattr(token))
        lines.append("  </action>")
    if current_map is not None:
        lines.append(" </actionmap>")
    lines.append("</ActionMaps>")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    os.replace(partial, path)
    return path


def _device_of(raw: str) -> str:
    prefixes = {"kb": "keyboard", "mo": "mouse", "js": "joystick", "gp": "gamepad"}
    return prefixes.get((raw or "")[:2], "")


# --- backups -----------------------------------------------------------------

def backup_actionmaps(source, backup_dir, keep: int = BACKUP_KEEP) -> Path | None:
    """Copy actionmaps.xml aside, dated; keep only the newest `keep`."""
    if source is None:
        return None
    source = Path(source)
    if not source.is_file():
        return None
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backup_dir / ("actionmaps-%s.xml" % stamp)
    n = 1
    while target.exists():
        target = backup_dir / ("actionmaps-%s-%d.xml" % (stamp, n))
        n += 1
    shutil.copy2(source, target)
    for old in list_backups(backup_dir)[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def list_backups(backup_dir) -> list[Path]:
    """Newest first. The names sort by time, which is why they look like that."""
    try:
        found = [p for p in Path(backup_dir).iterdir() if _RE_BACKUP.match(p.name)]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.name, reverse=True)


def newest_backup(backup_dir) -> Path | None:
    found = list_backups(backup_dir)
    return found[0] if found else None


def backup_stamp(path) -> str:
    """'actionmaps-20260917-154612.xml' -> '2026-09-17 15:46'."""
    match = _RE_BACKUP.match(Path(path).name)
    if not match:
        return Path(path).name
    d, t = match.group(1), match.group(2)
    return "%s-%s-%s %s:%s" % (d[:4], d[4:6], d[6:], t[:2], t[2:4])


# --- planning one change -----------------------------------------------------

@dataclass(frozen=True)
class Change:
    actionmap: str
    action: str
    device: str
    old_input: str
    new_input: str
    rows: tuple                     # the Rebind rows to write
    conflicts: tuple                # other (actionmap, action) on the same key in this mode

    @property
    def label(self) -> str:
        return describe_action(self.action)


def plan_change(bindings: list[Binding], actionmap: str, action: str, new_input: str,
                device: str = "keyboard") -> Change:
    """What setting this action to `new_input` would mean, before it is done."""
    old = ""
    table = []
    replaced = False
    row = Binding(actionmap, action, new_input, "rebind", device=device)
    for b in bindings:
        if (b.actionmap, b.action, b.device) == (actionmap, action, device):
            old = b.input
            table.append(row)
            replaced = True
        else:
            table.append(b)
    if not replaced:
        table.append(row)
    clashes: tuple = ()
    if new_input:
        members = conflicts(table).get((mode_of(actionmap), new_input), [])
        clashes = tuple(m for m in members if m != (actionmap, action))
    return Change(actionmap, action, device, old, new_input,
                  (Rebind(actionmap, action, new_input, device=device),), clashes)


def default_for(defaults, actionmap: str, action: str, device: str = "keyboard") -> str:
    """The shipped default for the action on that device; '' if none."""
    for r in defaults:
        if (r.actionmap, r.action, r.device) == (actionmap, action, device):
            return r.input
    return ""


def is_at_default(defaults, b: Binding) -> bool:
    """Whether the row's key is the shipped default, whatever the modifier order."""
    return chord_key(b.input) == chord_key(default_for(defaults, b.actionmap, b.action, b.device))


# --- export ------------------------------------------------------------------

CSV_COLUMNS = ("mode", "action", "action_id", "bound", "input", "action_map", "action_map_id",
               "device", "activation", "multitap", "source", "conflict")


def to_csv(bindings: list[Binding], conflict_keys) -> str:
    """Every binding with a device, one row each, for a spreadsheet."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for b in bindings:
        if not b.device:
            continue
        mode = mode_of(b.actionmap)
        writer.writerow([
            mode, describe_action(b.action), b.action, describe_input(b.input), b.input,
            map_label(b.actionmap), b.actionmap, b.device, b.activation, b.multitap, b.source,
            "yes" if b.input and (mode, b.input) in conflict_keys else "",
        ])
    return out.getvalue()


# --- import ------------------------------------------------------------------

@dataclass
class ImportSummary:
    path: Path
    rows: list
    actions: int = 0
    maps: int = 0
    unbound: int = 0
    unknown: list = None            # (actionmap, action) the shipped defaults do not know
    conflicts: dict = None          # (mode, input) -> members, for the imported keys
    error: str = ""


def validate_import(path, current: list[Binding], known) -> ImportSummary:
    """What a bindings file holds, and whether it can be offered for loading."""
    path = Path(path)
    summary = ImportSummary(path=path, rows=[], unknown=[], conflicts={})
    try:
        root = ET.parse(path).getroot()
    except OSError as exc:
        summary.error = "could not read it: %s" % exc
        return summary
    except ET.ParseError as exc:
        summary.error = "not well-formed XML: %s" % exc
        return summary
    if root.tag != "ActionMaps":
        summary.error = "not a Star Citizen bindings file (root is <%s>)" % root.tag
        return summary
    rows = read_actionmaps(path)
    if not rows:
        summary.error = "no bindings in it"
        return summary
    summary.rows = rows
    keys = {(r.actionmap, r.action) for r in rows}
    summary.actions = len(keys)
    summary.maps = len({r.actionmap for r in rows})
    summary.unbound = sum(1 for r in rows if not r.input)
    if known:
        summary.unknown = sorted(k for k in keys if k not in known)
    imported_keys = {(mode_of(r.actionmap), r.input) for r in rows if r.input}
    summary.conflicts = {k: v for k, v in conflicts(merge(current, rows)).items()
                         if k in imported_keys}
    return summary
